from __future__ import annotations
import os
from typing import Any
from .anthropic import AnthropicProvider
from .base import BaseProvider, ProviderInfo
from .gemini import GeminiProvider
from .openai_compat import OpenAICompatProvider

class ProviderRegistry:
    def __init__(self): self.providers={}; self._load()
    def _load(self):
        e=os.environ
        self.providers={
          'openai':OpenAICompatProvider(key='openai',name='OpenAI',vendor='OpenAI',api_key=e.get('OPENAI_API_KEY'),base_url=e.get('OPENAI_BASE_URL','https://api.openai.com/v1'),model=e.get('OPENAI_MODEL'),multimodal=True,note='通用推理、图片理解与工具协作'),
          'deepseek':OpenAICompatProvider(key='deepseek',name='DeepSeek',vendor='DeepSeek',api_key=e.get('DEEPSEEK_API_KEY'),base_url=e.get('DEEPSEEK_BASE_URL','https://api.deepseek.com'),model=e.get('DEEPSEEK_MODEL'),multimodal=False,note='文本推理与长上下文任务'),
          'qwen':OpenAICompatProvider(key='qwen',name='通义千问',vendor='阿里云百炼',api_key=e.get('DASHSCOPE_API_KEY'),base_url=e.get('QWEN_BASE_URL','https://dashscope.aliyuncs.com/compatible-mode/v1'),model=e.get('QWEN_MODEL'),multimodal=True,note='中文、多模态与文档场景'),
          'doubao':OpenAICompatProvider(key='doubao',name='豆包',vendor='火山方舟',api_key=e.get('ARK_API_KEY'),base_url=e.get('DOUBAO_BASE_URL','https://ark.cn-beijing.volces.com/api/v3'),model=e.get('DOUBAO_MODEL'),multimodal=True,note='中文业务场景与多模态'),
          'anthropic':AnthropicProvider(e.get('ANTHROPIC_API_KEY'),e.get('ANTHROPIC_MODEL')),
          'gemini':GeminiProvider(e.get('GEMINI_API_KEY'),e.get('GEMINI_MODEL')),
        }
        if e.get('CUSTOM_BASE_URL'):
            self.providers['custom']=OpenAICompatProvider(key='custom',name='企业模型',vendor='OpenAI-Compatible',api_key=e.get('CUSTOM_API_KEY'),base_url=e['CUSTOM_BASE_URL'],model=e.get('CUSTOM_MODEL'),multimodal=e.get('CUSTOM_MULTIMODAL','1')!='0',note='企业自建或私有化模型服务')
    def list(self)->list[dict[str,Any]]:
        rows=[p.info.dict() for p in self.providers.values()]
        rows.insert(0,ProviderInfo('auto','自动选择','系统',None,True,True,True,True,'根据附件类型和已配置服务选择',True).dict())
        rows.append(ProviderInfo('demo','本地演示','本地','Deterministic Runtime',True,True,True,True,'无需密钥，可完整跑通产品流程').dict())
        return rows
    def choose(self,preferred:str|None,assets:list[dict[str,Any]])->BaseProvider|None:
        needs_audio=any(str(a.get('mime','')).startswith('audio/') for a in assets)
        needs_visual=any(str(a.get('mime','')).startswith(('image/','video/')) for a in assets)
        # Native-text PDFs can already be parsed locally. A scanned/image-only PDF
        # requires a provider with real document-vision support.
        needs_document=any(
            str(a.get('mime',''))=='application/pdf' and (not str((a.get('meta') or {}).get('text') or '').strip() or float((a.get('meta') or {}).get('text_density') or 0)<40)
            for a in assets
        )
        def compatible(p:BaseProvider|None)->bool:
            if not (p and p.info.configured):return False
            if needs_audio and not p.info.supports_audio:return False
            if needs_document and not p.info.supports_document:return False
            # Video can be represented by extracted keyframes, so image multimodality is sufficient.
            if needs_visual and not p.info.multimodal:return False
            return True
        if preferred and preferred not in {'auto','demo'}:
            p=self.providers.get(preferred);return p if compatible(p) else None
        if needs_audio or needs_document:
            order=['gemini','qwen','doubao','openai','anthropic','deepseek']
        elif needs_visual:
            order=['qwen','doubao','gemini','openai','anthropic','deepseek']
        else:
            order=['deepseek','qwen','doubao','openai','anthropic','gemini']
        for k in order:
            p=self.providers.get(k)
            if compatible(p):return p
        return None
