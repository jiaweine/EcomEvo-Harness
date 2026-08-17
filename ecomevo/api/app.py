from __future__ import annotations
import asyncio, hashlib, json, logging, mimetypes, os, re, shutil, subprocess, time, uuid, zipfile
from pathlib import Path
from typing import Any, Literal
import httpx
from PIL import Image, UnidentifiedImageError
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from ecomevo.product import ConversationStore, ProductAnalyzer, extract_video_frames, probe_media
from ecomevo.providers import ProviderRegistry
from ecomevo.runtime import EcomEvoEngine
from ecomevo.runtime.mcp import MCPRegistry

ROOT=Path(__file__).resolve().parents[2]
DATA_DIR=Path(os.environ.get('ECOMEVO_DATA',ROOT/'outputs'/'runtime'))
FRONTEND=ROOT/'frontend';DATA_DIR.mkdir(parents=True,exist_ok=True)
store=ConversationStore(DATA_DIR/'product.db',DATA_DIR/'assets')
providers=ProviderRegistry();mcp=MCPRegistry()
engine=EcomEvoEngine(DATA_DIR/'runtime.db',mcp=mcp,model_gateway=providers)
analyzer=ProductAnalyzer(engine,providers,asset_meta_writer=store.patch_asset_meta)
queues:dict[str,list[asyncio.Queue]]={}
logger=logging.getLogger(__name__)
try:WS_POLL_SECONDS=float(os.environ.get('ECOMEVO_WS_POLL_SECONDS','2'))
except (TypeError,ValueError):WS_POLL_SECONDS=2.0
WS_POLL_SECONDS=max(.5,min(15.0,WS_POLL_SECONDS))
WS_HEARTBEAT_SECONDS=15.0

app=FastAPI(title='EcomEvo 商业决策工作台 API',description='面向商品治理、商家审核、售后与风险核查的对话式多模态决策服务。',version='1.0.0')
cors_origins=[x.strip() for x in os.environ.get('ECOMEVO_CORS_ORIGINS','').split(',') if x.strip()]
if cors_origins:
    app.add_middleware(CORSMiddleware,allow_origins=cors_origins,allow_credentials=False,allow_methods=['GET','POST','PATCH','OPTIONS'],allow_headers=['content-type','authorization'])

@app.middleware('http')
async def product_security_headers(request,response_next):
    response=await response_next(request)
    response.headers.setdefault('X-Content-Type-Options','nosniff')
    response.headers.setdefault('X-Frame-Options','DENY')
    response.headers.setdefault('Referrer-Policy','no-referrer')
    response.headers.setdefault('Permissions-Policy','camera=(), microphone=(), geolocation=(), payment=()')
    response.headers.setdefault('Cross-Origin-Resource-Policy','same-origin')
    response.headers.setdefault('Content-Security-Policy',"default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    if request.url.path.startswith('/api/'):
        response.headers.setdefault('Cache-Control','private, no-store')
    return response

app.mount('/assets',StaticFiles(directory=FRONTEND),name='assets')

Scene=Literal['product_governance','merchant_review','aftersales','risk_review','content_audit']
class ConversationCreate(BaseModel):
    title:str=Field(default='新的业务任务',max_length=120)
    scene:Scene='product_governance'
    @field_validator('title')
    @classmethod
    def clean_title(cls,value):
        value=value.strip()
        if not value:raise ValueError('任务名称不能为空')
        return value
class ConversationPatch(BaseModel):
    title:str|None=Field(default=None,max_length=120)
    scene:Scene|None=None
    @field_validator('title')
    @classmethod
    def clean_title(cls,value):
        if value is None:return None
        value=value.strip()
        if not value:raise ValueError('任务名称不能为空')
        return value
class ChatRequest(BaseModel):
    content:str=Field(min_length=1,max_length=16000);asset_ids:list[str]=Field(default_factory=list,max_length=30);provider:str=Field(default='auto',max_length=40)
    @field_validator('content')
    @classmethod
    def non_blank_content(cls,value):
        value=value.strip()
        if not value:raise ValueError('消息不能为空')
        return value
    @field_validator('asset_ids')
    @classmethod
    def unique_assets(cls,value):
        return list(dict.fromkeys(value))
class ActionDecision(BaseModel):
    decision:str=Field(pattern='^(approve|reject)$')
    note:str=Field(default='',max_length=2000)

def _file_sha256(path:str|Path)->str:
    digest=hashlib.sha256()
    with Path(path).open('rb') as fh:
        while True:
            chunk=fh.read(1024*1024)
            if not chunk:break
            digest.update(chunk)
    return digest.hexdigest()

_RASTER_MIMES={'image/jpeg','image/png','image/webp','image/gif','image/bmp','image/tiff'}
_DOC_SUFFIXES={'.pdf','.docx','.xlsx','.xlsm'}
_BLOCKED_SUFFIXES={'.exe','.dll','.so','.dylib','.bat','.cmd','.ps1','.sh','.com','.scr','.msi','.apk','.jar'}
_TEXT_SUFFIXES={'.txt','.log','.json','.csv','.tsv','.yaml','.yml','.xml','.md'}

def _normalize_upload_type(filename:str,mime:str)->tuple[str,str]:
    suffix=Path(filename or '').suffix.lower()
    guessed=mimetypes.guess_type(filename or '')[0]
    if suffix in _BLOCKED_SUFFIXES:
        raise HTTPException(415,'不支持可执行文件或脚本文件')
    if guessed and guessed.startswith(('image/','video/','audio/')):
        mime=guessed
    elif suffix=='.pdf':mime='application/pdf'
    elif suffix=='.docx':mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    elif suffix in {'.xlsx','.xlsm'}:mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    elif suffix in _TEXT_SUFFIXES and (not mime or mime=='application/octet-stream'):mime=guessed or 'text/plain'
    elif guessed and (mime in {'','application/octet-stream'}):mime=guessed
    mime=mime or 'application/octet-stream'
    allowed=(mime.startswith(('image/','video/','audio/','text/')) or suffix in _DOC_SUFFIXES|_TEXT_SUFFIXES or mime in {'application/pdf','application/json','application/xml'})
    if not allowed:raise HTTPException(415,'暂不支持该文件类型')
    return suffix,mime[:120]

def _upload_limit(mime:str,suffix:str)->int:
    if mime.startswith('image/'):return 25*1024*1024
    if mime.startswith(('audio/','video/')):return 150*1024*1024
    if suffix in _DOC_SUFFIXES or mime=='application/pdf':return 80*1024*1024
    return 25*1024*1024

def _validate_raster(path:Path)->None:
    try:
        with Image.open(path) as im:im.verify()
        with Image.open(path) as im:
            width,height=im.size
            if width<=0 or height<=0 or width>20000 or height>20000 or width*height>80_000_000:raise HTTPException(413,'图片尺寸过大')
    except HTTPException:raise
    except Image.DecompressionBombError as exc:raise HTTPException(413,'图片像素尺寸过大') from exc
    except (UnidentifiedImageError,OSError,ValueError) as exc:raise HTTPException(400,'图片文件损坏或格式不正确') from exc

def _validate_office_zip(path:Path,suffix:str)->None:
    try:
        with zipfile.ZipFile(path) as zf:
            infos=zf.infolist()
            if len(infos)>10000:raise HTTPException(413,'文档内部文件数量过多')
            total=sum(max(0,i.file_size) for i in infos);compressed=sum(max(1,i.compress_size) for i in infos)
            if total>500*1024*1024 or total/max(1,compressed)>150:raise HTTPException(413,'文档解压体积异常')
            names={i.filename for i in infos}
            if suffix=='.docx' and not {'[Content_Types].xml','word/document.xml'}<=names:raise HTTPException(400,'Word 文件结构不完整')
            if suffix in {'.xlsx','.xlsm'} and not {'[Content_Types].xml','xl/workbook.xml'}<=names:raise HTTPException(400,'Excel 文件结构不完整')
    except HTTPException:raise
    except (zipfile.BadZipFile,OSError) as exc:raise HTTPException(400,'Office 文件损坏或格式不正确') from exc

def _validate_pdf(path:Path)->None:
    try:
        from pypdf import PdfReader
        reader=PdfReader(str(path),strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(''):raise HTTPException(400,'暂不支持加密 PDF')
            except HTTPException:raise
            except Exception as exc:raise HTTPException(400,'暂不支持加密 PDF') from exc
        _=len(reader.pages)
    except HTTPException:raise
    except Exception as exc:raise HTTPException(400,'PDF 文件损坏或格式不正确') from exc

def _validate_av_media(path:Path,mime:str)->None:
    kind='video' if mime.startswith('video/') else 'audio'
    try:cp=subprocess.run(['ffprobe','-v','error','-print_format','json','-show_streams',str(path)],capture_output=True,text=True,timeout=20)
    except (OSError,subprocess.TimeoutExpired) as exc:raise HTTPException(400,'媒体文件无法读取') from exc
    if cp.returncode!=0:raise HTTPException(400,'媒体文件损坏或格式不正确')
    try:streams=json.loads(cp.stdout or '{}').get('streams',[])
    except Exception:streams=[]
    if not any(x.get('codec_type')==kind for x in streams if isinstance(x,dict)):raise HTTPException(400,'媒体文件内容与声明类型不匹配')

def _validate_uploaded_file(path:Path,mime:str,suffix:str)->None:
    if mime.startswith('image/'): _validate_raster(path)
    elif mime.startswith(('audio/','video/')):_validate_av_media(path,mime)
    elif suffix=='.pdf' or mime=='application/pdf':_validate_pdf(path)
    elif suffix in {'.docx','.xlsx','.xlsm'}:_validate_office_zip(path,suffix)

def _safe_download_name(value:str)->str:
    value=Path(value or 'download').name;value=re.sub(r'[\x00-\x1f\x7f]+','',value).strip();return value[:180] or 'download'

_PUBLIC_ASSET_META={'kind','width','height','mode','duration','bit_rate','sample_rate','channels','pages','indexed_pages','rows','indexed_rows','sheets','paragraphs','tables','chars','lines','sha256','semantic_observation_count','semantic_confidence'}
def _public_asset(row:dict[str,Any])->dict[str,Any]:
    meta=row.get('meta') or {};public={k:v for k,v in row.items() if k not in {'path','meta'}}
    public['meta']={k:meta[k] for k in _PUBLIC_ASSET_META if k in meta};public['url']=f"/api/assets/{row['id']}/file"
    if str(row.get('mime','')).startswith(('image/','video/')):public['preview_url']=f"/api/assets/{row['id']}/preview/0"
    return public

async def emit(cid:str,t:str,payload:dict[str,Any]):
    ev=store.add_event(cid,t,payload)
    for q in list(queues.get(cid,[])):
        try:q.put_nowait(ev)
        except asyncio.QueueFull:
            try:q.get_nowait()
            except asyncio.QueueEmpty:pass
            try:q.put_nowait(ev)
            except asyncio.QueueFull:pass
    return ev

@app.get('/')
def index():return FileResponse(FRONTEND/'index.html')
@app.get('/api/product')
def product_info():return {'name':'EcomEvo 商业决策工作台','subtitle':'把商品、商家、订单与多媒体证据放进一个任务里，直接完成核对、判定和后续处理。','scenes':[{'key':'product_governance','name':'商品治理','desc':'商品信息、主图、详情、资质和风险声明交叉核对'},{'key':'merchant_review','name':'商家审核','desc':'主体、资质、授权、历史风险与关联信息复核'},{'key':'aftersales','name':'售后判责','desc':'订单、物流、沟通记录和用户举证统一判定'},{'key':'risk_review','name':'风险核查','desc':'交易、账户、商品与履约异常信号综合复核'},{'key':'content_audit','name':'内容审核','desc':'图片、视频、文案与商品事实一致性检查'}],'accepted':['图片','视频','音频','PDF','Word','Excel','CSV/JSON','日志与文本'],'side_effect_policy':'高影响操作必须人工确认'}
@app.get('/api/health')
def health():return {'status':'ok','product':'EcomEvo 商业决策工作台','providers_configured':sum(1 for x in providers.list() if x.get('configured') and x['key'] not in {'auto','demo'}),'mcp_connections':len(mcp.servers)}
@app.get('/api/providers')
def provider_list():return providers.list()
@app.get('/api/runtime')
def runtime_info():return {'plugins':engine.plugins.describe(),'tools':engine.tools.describe(),'event_store':{'append_only':True,'hash_chain':True,'checkpoint':True,'rollback':True,'fork_ready':True},'planner':{'adaptive':True,'parallel_tool_composition':True,'recursive_review':True,'cost_gate':True,'learned_checks':engine.planner.evolution_state()},'recovery':{'verify_before_finish':True,'rollback_replan':True,'failure_driven_evolution':True,'sandbox_replay':True,'regression_gate':True},'mcp':mcp.list(),'evolution_patches':engine.events.list_patches(10)}
@app.get('/api/evolution')
def evolution(limit:int=Query(default=30,ge=1,le=100)):return engine.events.list_patches(limit)
@app.get('/api/runtime/sessions/{session_id}/events')
def runtime_events(session_id:str):
    if not engine.events.has_session(session_id):raise HTTPException(404,'运行记录不存在')
    return [x.model_dump() for x in engine.events.list_events(session_id)]
@app.get('/api/conversations')
def conversation_list(limit:int=Query(default=40,ge=1,le=100)):return store.list_conversations(limit)
@app.post('/api/conversations')
def conversation_create(req:ConversationCreate):return store.create_conversation(req.title,req.scene)
@app.patch('/api/conversations/{cid}')
def conversation_patch(cid:str,req:ConversationPatch):
    try:
        current=store.get_conversation(cid)
        if req.scene is not None and req.scene!=current['scene'] and store.has_messages(cid):raise HTTPException(409,'已有对话内容的任务不能修改业务场景，请新建任务')
        return store.update_conversation(cid,title=req.title,scene=req.scene)
    except KeyError:raise HTTPException(404,'任务不存在')
@app.get('/api/conversations/{cid}')
def conversation_get(cid:str):
    try:conv=store.get_conversation(cid)
    except KeyError:raise HTTPException(404,'任务不存在')
    store.recover_interrupted_turn(cid);store.recover_stale_actions(cid);message_count=store.count_messages(cid)
    return {**conv,'messages':store.list_messages(cid,limit=200),'message_count':message_count,'history_truncated':message_count>200,'assets':[_public_asset(x) for x in store.list_assets(cid)],'events':store.list_events(cid,limit=600),'actions':store.list_actions(cid)}

@app.post('/api/assets')
async def asset_upload(file:UploadFile=File(...),conversation_id:str=Form(...)):
    try:store.get_conversation(conversation_id)
    except KeyError:raise HTTPException(404,'任务不存在')
    existing=store.list_assets(conversation_id)
    if len(existing)>=120:raise HTTPException(409,'单个任务最多保留 120 份资料，请新建任务或整理现有资料')
    task_bytes=sum(int(x.get('size') or 0) for x in existing);task_cap=2*1024*1024*1024
    if task_bytes>=task_cap:raise HTTPException(413,'当前任务资料总量已达到上限')
    filename=file.filename or 'upload.bin';suffix,mime=_normalize_upload_type(filename,file.content_type or '')
    tmp=store.asset_dir/f'{uuid.uuid4().hex}{suffix}';frame_dir=store.asset_dir/f'{tmp.stem}_frames';limit=min(_upload_limit(mime,suffix),task_cap-task_bytes);size=0;digest=hashlib.sha256()
    try:
        with tmp.open('wb') as out:
            while True:
                chunk=await file.read(1024*1024)
                if not chunk:break
                size+=len(chunk)
                if size>limit:raise HTTPException(413,f'单个文件不能超过 {limit//1024//1024}MB')
                digest.update(chunk);out.write(chunk)
        if size==0:raise HTTPException(400,'不能上传空文件')
        await asyncio.to_thread(_validate_uploaded_file,tmp,mime,suffix);meta=await asyncio.to_thread(probe_media,tmp,mime);meta['sha256']=digest.hexdigest()
        if mime.startswith('video/'):
            frames=await asyncio.to_thread(extract_video_frames,tmp,frame_dir,4);meta['keyframes']=frames;meta['keyframe_sha256']={frame:await asyncio.to_thread(_file_sha256,frame) for frame in frames if Path(frame).is_file()}
        row=store.add_asset(conversation_id,name=_safe_download_name(filename),mime=mime,path=str(tmp),size=size,meta=meta);return _public_asset(row)
    except Exception:
        if tmp.exists():tmp.unlink(missing_ok=True)
        if frame_dir.exists():shutil.rmtree(frame_dir,ignore_errors=True)
        raise

def _verified_asset(asset_id:str)->dict[str,Any]:
    try:r=store.get_asset(asset_id)
    except KeyError:raise HTTPException(404,'资料不存在')
    path=Path(r['path'])
    if not path.is_file():raise HTTPException(410,'资料文件已不可用')
    expected=str((r.get('meta') or {}).get('sha256') or '')
    if expected and _file_sha256(path)!=expected:raise HTTPException(409,'资料内容指纹校验失败，请重新上传')
    return r
@app.get('/api/assets/{asset_id}/file')
def asset_file(asset_id:str):
    r=_verified_asset(asset_id);return FileResponse(r['path'],media_type=r['mime'],filename=_safe_download_name(r['name']),content_disposition_type='attachment')
@app.get('/api/assets/{asset_id}/preview/{index}')
def asset_preview(asset_id:str,index:int=0):
    r=_verified_asset(asset_id);meta=r.get('meta') or {};frames=meta.get('keyframes',[])
    if frames:
        index=max(0,min(index,len(frames)-1));frame=Path(frames[index])
        if not frame.is_file():raise HTTPException(410,'预览文件已不可用')
        expected=str((meta.get('keyframe_sha256') or {}).get(str(frame)) or '')
        if expected and _file_sha256(frame)!=expected:raise HTTPException(409,'预览内容指纹校验失败')
        return FileResponse(frame,media_type='image/jpeg')
    if str(r.get('mime','')) in _RASTER_MIMES:return FileResponse(r['path'],media_type=r['mime'])
    raise HTTPException(404,'没有可用预览')

@app.post('/api/conversations/{cid}/messages')
async def conversation_message(cid:str,req:ChatRequest,background_tasks:BackgroundTasks):
    try:conv=store.get_conversation(cid)
    except KeyError:raise HTTPException(404,'任务不存在')
    if req.provider not in {'auto','demo'} and req.provider not in providers.providers:raise HTTPException(422,'未知模型服务')
    had_messages=store.has_messages(cid);prior_messages=store.list_messages(cid,limit=12)
    for aid in req.asset_ids:
        try:r=store.bind_asset(aid,cid)
        except KeyError:raise HTTPException(404,f'资料不存在：{aid}')
        if r is None:raise HTTPException(409,'不能引用其他任务中的资料')
        if not Path(r['path']).is_file():raise HTTPException(410,f"资料文件已不可用：{r['name']}")
    assets=[];unavailable=[];integrity_failed=[];task_assets=store.list_assets(cid)
    for r in task_assets:
        path=Path(r['path'])
        if not path.is_file():unavailable.append(r['name']);continue
        expected=str((r.get('meta') or {}).get('sha256') or '')
        if expected:
            actual=await asyncio.to_thread(_file_sha256,path)
            if actual!=expected:integrity_failed.append(r['name']);continue
        assets.append(r)
    if integrity_failed:raise HTTPException(409,'资料内容指纹校验失败，请重新上传：'+'、'.join(integrity_failed[:5]))
    lease=store.claim_turn(cid)
    if lease is None:raise HTTPException(409,'当前任务正在处理上一条消息，请在结果返回后继续')
    try:
        user=store.add_message(cid,'user',req.content,{'asset_ids':req.asset_ids})
        if not had_messages:store.touch(cid,title=req.content.strip().replace('\n',' ')[:30] or '新的业务任务')
        await emit(cid,'message.accepted',{'message_id':user['id'],'message':user,'asset_count':len(req.asset_ids),'task_asset_count':len(assets)})
    except Exception:
        store.release_turn(cid,lease);raise
    if unavailable:await emit(cid,'notice',{'title':'部分历史资料已不可用','detail':'、'.join(unavailable[:5])+' 已从本轮核对中排除。'})
    async def work():
        stop_renew=asyncio.Event()
        async def renew_lease():
            while not stop_renew.is_set():
                try:await asyncio.wait_for(stop_renew.wait(),timeout=30)
                except asyncio.TimeoutError:
                    if not store.renew_turn(cid,lease):return
        renew_task=asyncio.create_task(renew_lease())
        try:
            async def sink(t,p):await emit(cid,t,p)
            result=await analyzer.run(text=req.content,assets=assets,provider_key=req.provider,sink=sink,domain_hint=conv['scene'],history=prior_messages)
            actions=[]
            from ecomevo.models import BusinessAction
            for x in result.get('actions',[]):
                action=BusinessAction(**x);binding=mcp.action_binding(action.kind,{'conversation_id':cid,'session_id':result['session_id'],'domain':result['domain'],'verifier_score':result.get('runtime',{}).get('verifier_score',0),'action_kind':action.kind,'action_id':action.action_id,'risk_level':action.risk_level})
                if binding:action.payload.update(binding)
                actions.append(action)
            store.save_actions(cid,result['session_id'],actions);msg=store.add_message(cid,'assistant',result['answer'],result);await emit(cid,'answer.ready',{'message':msg,'result':result,'actions':store.list_actions(cid)})
        except Exception:
            logger.exception('conversation processing failed: %s',cid);await emit(cid,'answer.error',{'message':'本次处理没有完成','detail':'服务执行异常，请重试；如持续失败请联系管理员。'})
        finally:
            stop_renew.set();renew_task.cancel()
            try:await renew_task
            except (asyncio.CancelledError, Exception):pass
            store.release_turn(cid,lease)
    background_tasks.add_task(work);return {'status':'accepted','message':user}

@app.get('/api/conversations/{cid}/actions')
def action_list(cid:str,status:str|None=None):
    try:store.get_conversation(cid)
    except KeyError:raise HTTPException(404,'任务不存在')
    store.recover_stale_actions(cid);return store.list_actions(cid,status)
@app.post('/api/actions/{action_id}/decision')
async def action_decide(action_id:str,req:ActionDecision):
    try:a=store.get_action(action_id)
    except KeyError:raise HTTPException(404,'操作不存在')
    if req.decision=='reject':
        row=store.transition_action(action_id,'proposed','rejected',{'operator_note':req.note})
        if row is None:raise HTTPException(409,'该操作已经处理过')
        await emit(a['conversation_id'],'action.updated',row);return row
    decision=engine.sandbox.validate_action(a['side_effect'],confirmed=True)
    if not decision.allowed:raise HTTPException(409,decision.reason)
    payload_patch={'operator_note':req.note,'execution_mode':'local_queue'};claimed=store.transition_action(action_id,'proposed','approved',payload_patch)
    if claimed is None:raise HTTPException(409,'该操作已经处理过')
    await emit(a['conversation_id'],'action.updated',claimed);mcp_server=claimed['payload'].get('mcp_server');mcp_tool=claimed['payload'].get('mcp_tool')
    try:
        if mcp_server and mcp_tool:
            result=await mcp.call_tool(mcp_server,mcp_tool,claimed['payload'].get('arguments',{}));payload_patch.update({'execution_mode':'mcp','execution_result':result,'execution_outcome':'confirmed'})
        else:payload_patch.update({'execution_result':{'queued':True,'message':'已进入业务处理队列（本地演示执行器）'},'execution_outcome':'confirmed'})
        row=store.update_action(action_id,'executed',payload_patch);await emit(a['conversation_id'],'action.updated',row);return row
    except (httpx.ReadTimeout,httpx.WriteTimeout,httpx.WriteError,httpx.RemoteProtocolError,httpx.ReadError) as exc:
        logger.exception('business action result is uncertain: %s',action_id);row=store.update_action(action_id,'uncertain',{'execution_error':'与业务系统通信中断，暂无法确认下游是否已执行；请先核对业务系统结果，不要直接重复操作。','execution_outcome':'unknown'});await emit(a['conversation_id'],'action.updated',row);raise HTTPException(502,'业务系统响应中断，当前操作结果暂无法确认，请先核对实际业务状态') from exc
    except Exception as exc:
        logger.exception('business action execution failed: %s',action_id);row=store.update_action(action_id,'failed',{'execution_error':'下游业务服务明确返回执行失败','execution_outcome':'failed'});await emit(a['conversation_id'],'action.updated',row);raise HTTPException(502,'业务操作执行失败') from exc

@app.websocket('/ws/conversations/{cid}')
async def conversation_ws(ws:WebSocket,cid:str,after_id:int=0):
    await ws.accept()
    try:store.get_conversation(cid)
    except KeyError:
        await ws.close(code=4404);return
    q:asyncio.Queue=asyncio.Queue(maxsize=500);queues.setdefault(cid,[]).append(q)
    try:
        latest_rows=store.list_events(cid,limit=1);durable_latest=int(latest_rows[-1]['id']) if latest_rows else 0
        requested_after=max(0,int(after_id or 0));start_after=min(requested_after,durable_latest)
        history=store.list_events(cid,after_id=start_after,limit=600) if start_after else store.list_events(cid,limit=600)
        cutoff=start_after
        for ev in history:
            event_id=int(ev.get('id',0) or 0)
            if event_id<=cutoff:continue
            await ws.send_json(ev);cutoff=max(cutoff,event_id)
        last_heartbeat=time.monotonic()
        while True:
            try:
                item=await asyncio.wait_for(q.get(),timeout=WS_POLL_SECONDS);item_id=int(item.get('id',0) or 0)
                if item_id<=cutoff:continue
                await ws.send_json(item);cutoff=max(cutoff,item_id)
            except asyncio.TimeoutError:
                pending=store.list_events(cid,after_id=cutoff,limit=200)
                if pending:
                    for ev in pending:
                        event_id=int(ev.get('id',0) or 0)
                        if event_id<=cutoff:continue
                        await ws.send_json(ev);cutoff=max(cutoff,event_id)
                    continue
                recovered=store.recover_interrupted_turn(cid)
                if recovered:
                    event_id=int(recovered.get('id',0) or 0)
                    if event_id>cutoff:await ws.send_json(recovered);cutoff=event_id
                    continue
                now=time.monotonic()
                if now-last_heartbeat>=WS_HEARTBEAT_SECONDS:
                    await ws.send_json({'type':'heartbeat','conversation_id':cid,'after_id':cutoff});last_heartbeat=now
    except WebSocketDisconnect:pass
    finally:
        if q in queues.get(cid,[]):queues[cid].remove(q)
        if not queues.get(cid):queues.pop(cid,None)
