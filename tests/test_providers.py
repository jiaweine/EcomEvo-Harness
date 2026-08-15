from ecomevo.providers.registry import ProviderRegistry


def test_explicit_text_only_provider_is_rejected_for_visual_asset(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','x')
    monkeypatch.setenv('DEEPSEEK_MODEL','deepseek-chat')
    registry=ProviderRegistry()
    image={'mime':'image/png','path':'/tmp/unused.png'}
    assert registry.choose('deepseek',[image]) is None


def test_audio_only_routes_to_declared_audio_provider(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','x')
    monkeypatch.setenv('GEMINI_MODEL','gemini-test')
    monkeypatch.setenv('DASHSCOPE_API_KEY','x')
    monkeypatch.setenv('QWEN_MODEL','qwen-test')
    registry=ProviderRegistry()
    audio={'mime':'audio/mpeg','path':'/tmp/unused.mp3'}
    chosen=registry.choose('auto',[audio])
    assert chosen is not None and chosen.info.key=='gemini'


def test_native_openai_uses_current_completion_limit_and_retries_temperature():
    import asyncio, json, httpx
    from ecomevo.providers.openai_compat import OpenAICompatProvider
    calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        assert 'max_completion_tokens' in body and 'max_tokens' not in body
        if len(calls)==1:
            return httpx.Response(400,json={'error':{'message':'temperature is not supported for this model'}})
        assert 'temperature' not in body
        return httpx.Response(200,json={'choices':[{'message':{'content':'ok'}}]})
    p=OpenAICompatProvider(key='openai',name='OpenAI',vendor='OpenAI',api_key='x',base_url='https://api.test/v1',model='gpt-test',multimodal=True,transport=httpx.MockTransport(handler))
    out=asyncio.run(p.chat(messages=[{'role':'user','content':'hi'}]))
    assert out=='ok' and len(calls)==2


def test_scanned_pdf_auto_routes_to_document_provider(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','x')
    monkeypatch.setenv('GEMINI_MODEL','gemini-test')
    monkeypatch.setenv('DEEPSEEK_API_KEY','x')
    monkeypatch.setenv('DEEPSEEK_MODEL','deepseek-chat')
    registry=ProviderRegistry()
    scanned={'mime':'application/pdf','path':'/tmp/scan.pdf','meta':{'kind':'document','text':''}}
    chosen=registry.choose('auto',[scanned])
    assert chosen is not None and chosen.info.key=='gemini' and chosen.info.supports_document


def test_gemini_small_pdf_is_sent_as_document_inline(tmp_path):
    import asyncio, json, httpx
    from ecomevo.providers.gemini import GeminiProvider
    pdf=tmp_path/'license.pdf';pdf.write_bytes(b'%PDF-1.4 mocked')
    seen={}
    def handler(request):
        assert request.url.path.endswith(':generateContent')
        body=json.loads(request.content);seen['body']=body
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'ok'}]}}]})
    p=GeminiProvider('x','gemini-test',transport=httpx.MockTransport(handler))
    out=asyncio.run(p.chat(messages=[{'role':'user','content':'read'}],assets=[{'mime':'application/pdf','path':str(pdf),'name':'license.pdf'}]))
    assert out=='ok'
    parts=seen['body']['contents'][0]['parts']
    assert any(x.get('inline_data',{}).get('mime_type')=='application/pdf' for x in parts)


def test_gemini_large_media_uses_files_api_and_deletes_after_use(tmp_path):
    import asyncio, json, httpx
    from ecomevo.providers.gemini import GeminiProvider
    audio=tmp_path/'call.mp3';audio.write_bytes(b'1234')
    seen=[]
    def handler(request):
        seen.append((request.method,str(request.url)))
        if request.url.path=='/upload/v1beta/files':
            assert request.headers['x-goog-upload-protocol']=='resumable'
            return httpx.Response(200,headers={'x-goog-upload-url':'https://upload.test/resumable'})
        if request.url.host=='upload.test':
            assert request.headers['x-goog-upload-command']=='upload, finalize'
            return httpx.Response(200,json={'file':{'name':'files/f1','uri':'https://files.test/f1','state':'ACTIVE'}})
        if request.url.path.endswith(':generateContent'):
            body=json.loads(request.content)
            assert any(x.get('file_data',{}).get('file_uri')=='https://files.test/f1' for x in body['contents'][0]['parts'])
            return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'heard'}]}}]})
        if request.method=='DELETE' and request.url.path=='/v1beta/files/f1':
            return httpx.Response(200,json={})
        return httpx.Response(404,text='unexpected')
    p=GeminiProvider('x','gemini-test',transport=httpx.MockTransport(handler),inline_limit=1)
    out=asyncio.run(p.chat(messages=[{'role':'user','content':'listen'}],assets=[{'mime':'audio/mpeg','path':str(audio),'name':'call.mp3'}]))
    assert out=='heard'
    assert any(m=='DELETE' and '/v1beta/files/f1' in u for m,u in seen)
