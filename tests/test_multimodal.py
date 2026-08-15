import asyncio
from pathlib import Path
from ecomevo.product.analyzer import ProductAnalyzer
from ecomevo.runtime import EcomEvoEngine
from ecomevo.providers.base import BaseProvider, ProviderInfo


class FakeVisionProvider(BaseProvider):
    def __init__(self):
        self.info=ProviderInfo('vision','Vision Test','test','v1',True,True,False,False,'')
        self.calls=0
    async def chat(self,*,messages,assets=None,temperature=.2,max_tokens=1400):
        self.calls+=1
        if self.calls==1:
            return '{"assets":[{"asset_id":"img-1","visible_text":"营业执照 91310000123456789A 品牌授权书","observations":["主体名称：海风贸易有限公司"],"identifiers":["91310000123456789A"],"risk_signals":[],"confidence":0.94}]}'
        return '### 处理结论\n资料已完成核对，后续操作仍需人工确认。'


class FakeRegistry:
    def __init__(self,p):self.p=p;self.providers={'vision':p}
    def choose(self,preferred,assets):return self.p


def test_visual_observation_enters_verification_before_action(tmp_path):
    img=tmp_path/'license.png';img.write_bytes(b'not-a-real-image-provider-is-mocked')
    asset={'id':'img-1','name':'营业执照.png','mime':'image/png','path':str(img),'meta':{'kind':'image','width':100,'height':100}}
    provider=FakeVisionProvider();engine=EcomEvoEngine(tmp_path/'runtime.db');analyzer=ProductAnalyzer(engine,FakeRegistry(provider))
    events=[]
    async def sink(t,p):events.append((t,p))
    result=asyncio.run(analyzer.run(text='帮我审核这个商家',assets=[asset],provider_key='vision',sink=sink,domain_hint='merchant_review'))
    assert provider.calls==2
    assert result['runtime']['status']=='completed'
    assert result['runtime']['belief']['missing_evidence']==[]
    assert result['actions'][0]['side_effect'] is True
    assert result['evidence'][0]['tags']==['multimodal_observation']
    assert any(t=='notice' and p.get('title')=='多媒体资料已读取' for t,p in events)


def test_malformed_visual_extraction_never_becomes_evidence(tmp_path):
    class Bad(FakeVisionProvider):
        async def chat(self,**kwargs):
            self.calls+=1
            return '无法确认' if self.calls==1 else '本地外部回答'
    img=tmp_path/'license.png';img.write_bytes(b'x')
    asset={'id':'img-1','name':'营业执照.png','mime':'image/png','path':str(img),'meta':{'kind':'image'}}
    p=Bad();engine=EcomEvoEngine(tmp_path/'runtime.db');analyzer=ProductAnalyzer(engine,FakeRegistry(p))
    async def sink(t,p):pass
    result=asyncio.run(analyzer.run(text='审核商家',assets=[asset],provider_key='vision',sink=sink,domain_hint='merchant_review'))
    assert result['runtime']['status']=='needs_evidence'
    assert all(not x['side_effect'] for x in result['actions'])


def test_scanned_pdf_semantic_observation_enters_runtime(tmp_path):
    class FakeDocumentProvider(FakeVisionProvider):
        def __init__(self):
            super().__init__()
            self.info=ProviderInfo('doc','Document Test','test','v1',True,True,False,False,'',True)
        async def chat(self,*,messages,assets=None,temperature=.2,max_tokens=1400):
            self.calls+=1
            if self.calls==1:
                return '{"assets":[{"asset_id":"pdf-1","visible_text":"营业执照 91310000123456789A 品牌授权书","observations":["主体名称：海风贸易有限公司"],"identifiers":["91310000123456789A"],"risk_signals":[],"confidence":0.95}]}'
            return '已完成审核。'
    pdf=tmp_path/'scan.pdf';pdf.write_bytes(b'%PDF scan mocked')
    asset={'id':'pdf-1','name':'扫描营业执照.pdf','mime':'application/pdf','path':str(pdf),'meta':{'kind':'document','text':'','preview':''}}
    provider=FakeDocumentProvider();engine=EcomEvoEngine(tmp_path/'runtime.db');analyzer=ProductAnalyzer(engine,FakeRegistry(provider))
    async def sink(t,p):pass
    result=asyncio.run(analyzer.run(text='帮我审核这个商家',assets=[asset],provider_key='doc',sink=sink,domain_hint='merchant_review'))
    assert provider.calls==2
    assert result['runtime']['status']=='completed'
    assert result['runtime']['belief']['missing_evidence']==[]
    assert result['evidence'][0]['tags']==['multimodal_observation']


def test_tampered_video_keyframe_is_not_sent_to_provider(tmp_path):
    import hashlib
    original=tmp_path/'v.mp4';original.write_bytes(b'video')
    frame=tmp_path/'frame.jpg';frame.write_bytes(b'good-frame')
    digest=hashlib.sha256(frame.read_bytes()).hexdigest()
    asset={'id':'v1','name':'v.mp4','mime':'video/mp4','path':str(original),'meta':{'kind':'video','keyframes':[str(frame)],'keyframe_sha256':{str(frame):digest}}}
    rows=ProductAnalyzer._provider_assets([asset])
    assert any(x.get('id')=='v1-frame-0' for x in rows)
    frame.write_bytes(b'tampered')
    rows=ProductAnalyzer._provider_assets([asset])
    assert all(x.get('id')!='v1-frame-0' for x in rows)

def test_incomplete_case_cannot_be_overridden_by_model_wording(tmp_path):
    from ecomevo.product.analyzer import ProductAnalyzer
    from ecomevo.runtime.engine import EcomEvoEngine
    from ecomevo.providers.base import BaseProvider, ProviderInfo

    class UnsafeProvider(BaseProvider):
        info=ProviderInfo('unsafe','Unsafe','test','x',True,False)
        async def chat(self,**kwargs):
            return '审核通过，可以立即执行退款和下架。'
    class Registry:
        providers={'unsafe':UnsafeProvider()}
        def choose(self,*args,**kwargs):return self.providers['unsafe']

    analyzer=ProductAnalyzer(EcomEvoEngine(tmp_path/'r.db'),Registry())
    events=[]
    async def sink(t,p):events.append((t,p))
    result=asyncio.run(analyzer.run(text='审核这个商家',assets=[],provider_key='unsafe',sink=sink,domain_hint='merchant_review'))
    assert result['runtime']['status']=='needs_evidence'
    assert '审核通过，可以立即执行退款和下架' not in result['answer']
    assert '还不适合直接做最终处置' in result['answer']

def test_semantic_media_result_is_cached_and_not_reextracted(tmp_path):
    from ecomevo.product.store import ConversationStore
    store=ConversationStore(tmp_path/'p.db',tmp_path/'assets')
    conv=store.create_conversation('m','merchant_review')
    img=tmp_path/'license.png';img.write_bytes(b'fake-image')
    row=store.add_asset(conv['id'],name='license.png',mime='image/png',path=str(img),size=10,meta={'kind':'image','sha256':'x'})
    class CachedProvider(FakeVisionProvider):
        async def chat(self,*,messages,assets=None,temperature=.2,max_tokens=1400):
            self.calls+=1
            if self.calls==1:
                aid=assets[0]['id']
                return '{"assets":[{"asset_id":"'+aid+'","visible_text":"营业执照 91310000123456789A 品牌授权书","observations":["主体名称：海风贸易有限公司"],"identifiers":["91310000123456789A"],"risk_signals":[],"confidence":0.94}]}'
            return '已完成审核。'
    provider=CachedProvider();engine=EcomEvoEngine(tmp_path/'runtime.db')
    analyzer=ProductAnalyzer(engine,FakeRegistry(provider),asset_meta_writer=store.patch_asset_meta)
    async def sink(t,p):pass
    first=asyncio.run(analyzer.run(text='审核商家',assets=[row],provider_key='vision',sink=sink,domain_hint='merchant_review'))
    assert first['runtime']['status']=='completed' and provider.calls==2
    persisted=store.get_asset(row['id'])
    assert persisted['meta'].get('semantic_text') and persisted['meta'].get('semantic_source')=='vision'
    second=asyncio.run(analyzer.run(text='继续看刚才资料',assets=[persisted],provider_key='vision',sink=sink,domain_hint='merchant_review'))
    assert second['runtime']['status']=='completed'
    assert provider.calls==3  # only the final wording call on turn two; no second vision extraction

def test_low_confidence_visual_read_never_unlocks_business_action(tmp_path):
    class LowConfidence(FakeVisionProvider):
        async def chat(self,*,messages,assets=None,temperature=.2,max_tokens=1400):
            self.calls+=1
            if self.calls==1:
                return '{"assets":[{"asset_id":"img-1","visible_text":"营业执照 91310000123456789A 品牌授权书","observations":["主体名称：疑似某公司"],"identifiers":["91310000123456789A"],"risk_signals":[],"confidence":0.22}]}'
            return '审核通过。'
    img=tmp_path/'blur.png';img.write_bytes(b'x')
    asset={'id':'img-1','name':'模糊营业执照.png','mime':'image/png','path':str(img),'meta':{'kind':'image'}}
    p=LowConfidence();analyzer=ProductAnalyzer(EcomEvoEngine(tmp_path/'r.db'),FakeRegistry(p))
    async def sink(t,p):pass
    result=asyncio.run(analyzer.run(text='审核商家',assets=[asset],provider_key='vision',sink=sink,domain_hint='merchant_review'))
    assert result['runtime']['status']=='needs_evidence'
    assert result['actions']==[]
    assert result['evidence'][0]['tags']==[]
    assert '还不适合直接做最终处置' in result['answer']
