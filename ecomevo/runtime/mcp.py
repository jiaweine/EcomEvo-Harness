from __future__ import annotations
import base64, json, os, re, time, uuid
from dataclasses import dataclass
from typing import Any
import httpx

MODERN_VERSION='2026-07-28'
LEGACY_VERSION='2025-11-25'


@dataclass
class MCPServer:
    key:str
    name:str
    url:str
    token_env:str|None=None
    enabled:bool=True


class _LegacyRequired(RuntimeError):
    pass


class MCPRegistry:
    """MCP Streamable HTTP client with 2026-07-28 requests and 2025-11-25 fallback."""
    def __init__(self, transport:httpx.AsyncBaseTransport|None=None):
        self.servers:dict[str,MCPServer]={}
        self.action_map:dict[str,dict[str,Any]]={}
        self.read_tools:list[dict[str,Any]]=[]
        self._legacy_sessions:dict[str,tuple[str,str|None]]={}
        self._tool_cache:dict[str,tuple[float,dict[str,dict[str,Any]]]]={}
        self._transport=transport
        try:timeout=float(os.environ.get('ECOMEVO_MCP_TIMEOUT_SECONDS','30'))
        except (TypeError,ValueError):timeout=30.0
        self.timeout_s=max(3.0,min(120.0,timeout))
        self._load_env()

    def _load_env(self):
        raw=os.environ.get('ECOMEVO_MCP_SERVERS','').strip()
        if raw:
            try:
                for row in json.loads(raw):
                    s=MCPServer(**row);self.servers[s.key]=s
            except Exception:
                pass
        raw_map=os.environ.get('ECOMEVO_ACTION_MCP_MAP','').strip()
        if raw_map:
            try:
                value=json.loads(raw_map)
                if isinstance(value,dict):self.action_map=value
            except Exception:
                pass
        raw_read=os.environ.get('ECOMEVO_MCP_READ_TOOLS','').strip()
        if raw_read:
            try:
                value=json.loads(raw_read)
                if isinstance(value,list):
                    allowed={'product_governance','merchant_review','aftersales','risk_review','content_audit','general'}
                    for i,row in enumerate(value):
                        if not isinstance(row,dict) or row.get('domain') not in allowed or not row.get('server') or not row.get('tool'):continue
                        item=dict(row);item['key']=str(item.get('key') or f"mcp.read.{i+1}")
                        item['purpose']=str(item.get('purpose') or '读取企业业务数据')
                        item['arguments']=item.get('arguments') if isinstance(item.get('arguments'),dict) else {}
                        item['evidence_tags']=[str(x) for x in (item.get('evidence_tags') or []) if str(x).strip()]
                        try:item['cost']=max(.1,min(5.0,float(item.get('cost',1.0))))
                        except Exception:item['cost']=1.0
                        self.read_tools.append(item)
            except Exception:
                pass

    def read_tool_specs(self)->list[dict[str,Any]]:
        return [dict(x) for x in self.read_tools]

    def list(self):
        # Never expose internal endpoints or secret environment-variable names to the browser.
        return [{'key':s.key,'name':s.name,'enabled':s.enabled,'configured':True} for s in self.servers.values()]

    def action_binding(self,kind:str,context:dict[str,Any]|None=None)->dict[str,Any]|None:
        row=self.action_map.get(kind)
        if not isinstance(row,dict) or not row.get('server') or not row.get('tool'):return None
        context=context or {}
        def expand(value):
            if isinstance(value,dict):return {k:expand(v) for k,v in value.items()}
            if isinstance(value,list):return [expand(v) for v in value]
            if isinstance(value,str):
                exact=re.fullmatch(r'\$\{([A-Za-z0-9_]+)\}',value)
                if exact:return context.get(exact.group(1),value)
                for k,v in context.items():value=value.replace('${'+k+'}',str(v))
                return value
            return value
        return {'mcp_server':row['server'],'mcp_tool':row['tool'],'arguments':expand(row.get('arguments',{}))}

    def _auth_headers(self,s:MCPServer)->dict[str,str]:
        headers={'content-type':'application/json','accept':'application/json, text/event-stream'}
        if s.token_env and os.environ.get(s.token_env):headers['authorization']=f"Bearer {os.environ[s.token_env]}"
        return headers

    @staticmethod
    def _header_value(value:Any)->str:
        text=str(value)
        safe=bool(text) and text==text.strip() and all(0x20<=ord(ch)<=0x7E for ch in text)
        if safe and not (text.startswith('=?base64?') and text.endswith('?=')):return text
        return '=?base64?'+base64.b64encode(text.encode('utf-8')).decode('ascii')+'?='

    @staticmethod
    def _json_from_response(r:httpx.Response, request_id:str|None=None)->dict[str,Any]:
        content_type=r.headers.get('content-type','').lower()
        if 'text/event-stream' not in content_type:
            if not r.content:return {}
            try:return r.json()
            except Exception as exc:raise RuntimeError('MCP 返回了无法解析的 JSON') from exc
        final=None
        # Request-scoped SSE: ignore progress/notification events and return the matching JSON-RPC response.
        for block in re.split(r'\r?\n\r?\n',r.text):
            lines=[]
            for line in block.splitlines():
                if line.startswith('data:'):lines.append(line[5:].lstrip())
            if not lines:continue
            try:data=json.loads('\n'.join(lines))
            except Exception:continue
            if request_id is None or str(data.get('id'))==str(request_id):
                if 'result' in data or 'error' in data:final=data
        if final is None:raise RuntimeError('MCP SSE 响应中没有最终 JSON-RPC 结果')
        return final

    async def _post(self,s:MCPServer,payload:dict[str,Any],headers:dict[str,str])->tuple[httpx.Response,dict[str,Any]]:
        async with httpx.AsyncClient(timeout=self.timeout_s,transport=self._transport) as client:
            r=await client.post(s.url,headers=headers,json=payload)
        data={}
        if r.content:
            try:data=self._json_from_response(r,str(payload.get('id')) if payload.get('id') is not None else None)
            except RuntimeError as exc:
                if r.status_code<400 and payload.get('method')=='tools/call':
                    raise httpx.RemoteProtocolError(
                        'MCP tool-call response could not be confirmed',
                        request=r.request,
                    ) from exc
                if r.status_code<400:raise
        return r,data

    @staticmethod
    def _result_or_raise(r:httpx.Response,data:dict[str,Any])->dict[str,Any]:
        if r.status_code>=400:
            detail=(data.get('error') if isinstance(data,dict) else None) or r.text[:300]
            raise RuntimeError(f'MCP HTTP {r.status_code}: {detail}')
        if data.get('error'):raise RuntimeError(str(data['error']))
        return data.get('result',{})

    @staticmethod
    def _raise_if_ambiguous_tool_transport(r:httpx.Response)->None:
        if r.status_code==408 or r.status_code>=500:
            raise httpx.RemoteProtocolError(
                f'MCP tool-call response is ambiguous: HTTP {r.status_code}',
                request=r.request,
            )

    @classmethod
    def _tool_result_or_raise(cls,r:httpx.Response,data:dict[str,Any])->dict[str,Any]:
        """Return a confirmed tool result or distinguish definite rejection from ambiguity."""
        cls._raise_if_ambiguous_tool_transport(r)
        if r.status_code>=400:
            return cls._result_or_raise(r,data)
        if not isinstance(data,dict) or ('result' not in data and 'error' not in data):
            raise httpx.RemoteProtocolError('MCP tool-call response has no confirmable result',request=r.request)
        error=data.get('error')
        if error:
            code=error.get('code') if isinstance(error,dict) else None
            if code in {-32600,-32601,-32602}:
                # Invalid request/method/params are pre-execution rejections and are safe to
                # classify as explicit failures. Server/internal/application errors are not.
                raise RuntimeError(str(error))
            raise httpx.RemoteProtocolError(
                f'MCP tool-call returned an ambiguous JSON-RPC error: {error}',
                request=r.request,
            )
        result=data.get('result')
        if not isinstance(result,dict):
            raise httpx.RemoteProtocolError('MCP tool-call result has an invalid shape',request=r.request)
        if result.get('isError') is True:
            raise httpx.RemoteProtocolError('MCP tool reported an unconfirmed execution error',request=r.request)
        return result

    def _modern_meta(self)->dict[str,Any]:
        return {
            'io.modelcontextprotocol/protocolVersion':MODERN_VERSION,
            'io.modelcontextprotocol/clientInfo':{'name':'EcomEvo','version':'1.0.0'},
            'io.modelcontextprotocol/clientCapabilities':{},
        }

    async def _modern_request(self,s:MCPServer,method:str,params:dict[str,Any],name:str|None=None,extra_headers:dict[str,str]|None=None,*,allow_legacy_probe:bool=False)->dict[str,Any]:
        rid=uuid.uuid4().hex
        params={**params,'_meta':self._modern_meta()}
        payload={'jsonrpc':'2.0','id':rid,'method':method,'params':params}
        headers={**self._auth_headers(s),'MCP-Protocol-Version':MODERN_VERSION,'Mcp-Method':method}
        if name is not None:headers['Mcp-Name']=self._header_value(name)
        if extra_headers:headers.update(extra_headers)
        r,data=await self._post(s,payload,headers)
        if method=='tools/call':return self._tool_result_or_raise(r,data)
        if allow_legacy_probe and r.status_code in {400,404,405}:
            err=data.get('error',{}) if isinstance(data,dict) else {}
            code=err.get('code') if isinstance(err,dict) else None
            msg=str(err.get('message','')).lower() if isinstance(err,dict) else ''
            # Probe-only fallback: before any side-effect call, a clearly incompatible endpoint may be retried
            # with the legacy lifecycle. Application errors from tools/call must never trigger a replay.
            if code not in {-32020,-32021,-32022} and 'protocol version' not in msg and 'header' not in msg:
                raise _LegacyRequired('legacy MCP transport suspected')
        if allow_legacy_probe and data.get('error'):
            msg=str(data['error']).lower()
            if 'initializ' in msg or 'session' in msg:
                raise _LegacyRequired('legacy MCP lifecycle required')
        return self._result_or_raise(r,data)

    @staticmethod
    def _walk_header_annotations(schema:dict[str,Any],arguments:dict[str,Any])->list[tuple[str,Any]]:
        found=[]
        props=schema.get('properties',{}) if isinstance(schema,dict) else {}
        for key,sub in props.items():
            if not isinstance(sub,dict):
                continue
            present=isinstance(arguments,dict) and key in arguments
            value=arguments.get(key) if present else None
            header_name=sub.get('x-mcp-header')
            if header_name and present and value is not None:
                if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+",str(header_name)):
                    raise RuntimeError(f'MCP tool schema has invalid x-mcp-header: {header_name}')
                if isinstance(value,(str,int,bool)) and not isinstance(value,float):
                    found.append((str(header_name),value))
                else:
                    raise RuntimeError(f'MCP x-mcp-header parameter must be string/integer/boolean: {key}')
            if isinstance(value,dict):
                found.extend(MCPRegistry._walk_header_annotations(sub,value))
        return found

    async def _modern_tool_schema(self,s:MCPServer,tool_name:str)->dict[str,Any]|None:
        now=time.time();cached=self._tool_cache.get(s.key)
        if not cached or cached[0]<=now:
            result=await self._modern_request(s,'tools/list',{},allow_legacy_probe=True)
            tools={x.get('name'):x for x in result.get('tools',[]) if isinstance(x,dict) and x.get('name')}
            ttl_ms=int(result.get('ttlMs',30000) or 30000);ttl_ms=max(1000,min(ttl_ms,300000))
            self._tool_cache[s.key]=(now+ttl_ms/1000,tools);cached=self._tool_cache[s.key]
        return cached[1].get(tool_name)

    async def _call_modern(self,s:MCPServer,tool_name:str,arguments:dict[str,Any])->dict[str,Any]:
        schema=await self._modern_tool_schema(s,tool_name)
        extra={}
        if schema:
            for name,value in self._walk_header_annotations(schema.get('inputSchema',{}),arguments):
                extra[f'Mcp-Param-{name}']=self._header_value(value)
        return await self._modern_request(s,'tools/call',{'name':tool_name,'arguments':arguments},name=tool_name,extra_headers=extra)

    async def _legacy_initialize(self,s:MCPServer)->tuple[str,str|None]:
        rid=uuid.uuid4().hex
        payload={'jsonrpc':'2.0','id':rid,'method':'initialize','params':{'protocolVersion':LEGACY_VERSION,'capabilities':{},'clientInfo':{'name':'EcomEvo','version':'1.0.0'}}}
        r,data=await self._post(s,payload,self._auth_headers(s));result=self._result_or_raise(r,data)
        version=str(result.get('protocolVersion') or LEGACY_VERSION);session=r.headers.get('MCP-Session-Id') or r.headers.get('Mcp-Session-Id')
        headers={**self._auth_headers(s),'MCP-Protocol-Version':version}
        if session:headers['MCP-Session-Id']=session
        notify={'jsonrpc':'2.0','method':'notifications/initialized'}
        nr,_=await self._post(s,notify,headers)
        if nr.status_code>=400:raise RuntimeError(f'MCP initialized notification failed: HTTP {nr.status_code}')
        self._legacy_sessions[s.key]=(version,session);return version,session

    async def _call_legacy(self,s:MCPServer,tool_name:str,arguments:dict[str,Any],retry:bool=True)->dict[str,Any]:
        version,session=self._legacy_sessions.get(s.key) or await self._legacy_initialize(s)
        rid=uuid.uuid4().hex;payload={'jsonrpc':'2.0','id':rid,'method':'tools/call','params':{'name':tool_name,'arguments':arguments}}
        headers={**self._auth_headers(s),'MCP-Protocol-Version':version}
        if session:headers['MCP-Session-Id']=session
        r,data=await self._post(s,payload,headers)
        if r.status_code==404 and session:
            # A tools/call may already have reached business logic before the session failure is observed.
            # Never replay it automatically. Clear the stale session and surface an ambiguous transport result.
            self._legacy_sessions.pop(s.key,None)
            raise httpx.RemoteProtocolError('MCP legacy session expired during tool call',request=r.request)
        return self._tool_result_or_raise(r,data)

    async def call_tool(self,server_key:str,tool_name:str,arguments:dict[str,Any])->dict[str,Any]:
        s=self.servers.get(server_key)
        if not s or not s.enabled:raise RuntimeError('MCP server not configured')
        try:
            try:return await self._call_modern(s,tool_name,arguments)
            except _LegacyRequired:return await self._call_legacy(s,tool_name,arguments)
        except httpx.TimeoutException as exc:
            # Normalize every transport timeout to ReadTimeout so the action API uses the
            # conservative `uncertain` outcome instead of claiming an explicit business failure.
            raise httpx.ReadTimeout('MCP request timed out',request=getattr(exc,'request',None)) from exc
