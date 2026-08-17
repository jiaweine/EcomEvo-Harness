from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Any

from .anthropic import AnthropicProvider
from .base import BaseProvider, ProviderInfo
from .gemini import GeminiProvider
from .openai_compat import OpenAICompatProvider


class ProviderRegistry:
    def __init__(self):
        self.providers = {}
        # Provider selection is request-local. Concurrent tasks can use different engines
        # without one conversation leaking its choice into another.
        self._active_provider: ContextVar[BaseProvider | None] = ContextVar(
            "ecomevo_active_provider", default=None
        )
        self._load()

    def _load(self):
        e = os.environ
        self.providers = {
            'openai': OpenAICompatProvider(
                key='openai', name='OpenAI', vendor='OpenAI', api_key=e.get('OPENAI_API_KEY'),
                base_url=e.get('OPENAI_BASE_URL', 'https://api.openai.com/v1'), model=e.get('OPENAI_MODEL'),
                multimodal=True, note='通用推理、图片理解与工具协作'),
            'deepseek': OpenAICompatProvider(
                key='deepseek', name='DeepSeek', vendor='DeepSeek', api_key=e.get('DEEPSEEK_API_KEY'),
                base_url=e.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'), model=e.get('DEEPSEEK_MODEL'),
                multimodal=False, note='文本推理与长上下文任务'),
            'qwen': OpenAICompatProvider(
                key='qwen', name='通义千问', vendor='阿里云百炼', api_key=e.get('DASHSCOPE_API_KEY'),
                base_url=e.get('QWEN_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1'), model=e.get('QWEN_MODEL'),
                multimodal=True, note='中文、多模态与文档场景'),
            'doubao': OpenAICompatProvider(
                key='doubao', name='豆包', vendor='火山方舟', api_key=e.get('ARK_API_KEY'),
                base_url=e.get('DOUBAO_BASE_URL', 'https://ark.cn-beijing.volces.com/api/v3'), model=e.get('DOUBAO_MODEL'),
                multimodal=True, note='中文业务场景与多模态'),
            'anthropic': AnthropicProvider(e.get('ANTHROPIC_API_KEY'), e.get('ANTHROPIC_MODEL')),
            'gemini': GeminiProvider(e.get('GEMINI_API_KEY'), e.get('GEMINI_MODEL')),
        }
        # Optional frontier open-weight/self-hosted controller. The model name is intentionally
        # configuration-driven so operators can replace it without changing product code.
        if e.get('OPEN_MODEL_BASE_URL'):
            self.providers['open_model'] = OpenAICompatProvider(
                key='open_model', name='Open-weight Runtime', vendor='Self-hosted',
                api_key=e.get('OPEN_MODEL_API_KEY'), base_url=e['OPEN_MODEL_BASE_URL'],
                model=e.get('OPEN_MODEL_MODEL'), multimodal=e.get('OPEN_MODEL_MULTIMODAL', '0') == '1',
                note='自托管/开源权重推理与工具协作')
        if e.get('CUSTOM_BASE_URL'):
            self.providers['custom'] = OpenAICompatProvider(
                key='custom', name='Enterprise Runtime', vendor='OpenAI-Compatible', api_key=e.get('CUSTOM_API_KEY'),
                base_url=e['CUSTOM_BASE_URL'], model=e.get('CUSTOM_MODEL'),
                multimodal=e.get('CUSTOM_MULTIMODAL', '1') != '0', note='企业自建或私有化模型服务')

    def _select(self, provider: BaseProvider | None) -> BaseProvider | None:
        self._active_provider.set(provider)
        return provider

    def current_provider(self) -> BaseProvider | None:
        """Return the provider chosen in the current request/task context, if any."""
        return self._active_provider.get()

    @staticmethod
    def _public_engine_info(info: ProviderInfo, index: int) -> dict[str, Any]:
        # Product UI describes capabilities, not vendor brands. Keys remain stable for routing.
        caps = []
        if info.multimodal:
            caps.append('图片')
        if info.supports_audio:
            caps.append('音频')
        if info.supports_document:
            caps.append('扫描文档')
        note = ' · '.join(caps) if caps else '文本规划 · 工具协作'
        return ProviderInfo(
            key=info.key,
            name=f'认知引擎 {index:02d}',
            vendor='可替换运行服务',
            model=None,
            configured=info.configured,
            multimodal=info.multimodal,
            supports_video=info.supports_video,
            supports_audio=info.supports_audio,
            note=note,
            supports_document=info.supports_document,
        ).dict()

    def list(self) -> list[dict[str, Any]]:
        configured = [p.info for p in self.providers.values() if p.info.configured]
        rows = [ProviderInfo(
            'auto', '自动编排', '系统', None, True,
            any(x.multimodal for x in configured),
            any(x.supports_video for x in configured),
            any(x.supports_audio for x in configured),
            '按任务模态、可用能力与配置自动选择',
            any(x.supports_document for x in configured),
        ).dict()]
        rows.extend(self._public_engine_info(p.info, index) for index, p in enumerate(self.providers.values(), 1))
        # Local mode is a strict privacy boundary and clears the task-local provider.
        rows.append(ProviderInfo(
            'demo', '本地受控', '本地', None, True, False, False, False,
            '文本与结构化资料的确定性受控运行', False).dict())
        return rows

    def choose(self, preferred: str | None, assets: list[dict[str, Any]]) -> BaseProvider | None:
        if preferred == 'demo':
            return self._select(None)
        needs_audio = any(str(a.get('mime', '')).startswith('audio/') for a in assets)
        needs_visual = any(str(a.get('mime', '')).startswith(('image/', 'video/')) for a in assets)
        # Native-text PDFs can already be parsed locally. Image-only/scanned PDFs require
        # a provider that explicitly advertises document-vision support.
        needs_document = any(
            str(a.get('mime', '')) == 'application/pdf' and (
                not str((a.get('meta') or {}).get('text') or '').strip()
                or float((a.get('meta') or {}).get('text_density') or 0) < 40
            )
            for a in assets
        )

        def compatible(p: BaseProvider | None) -> bool:
            if not (p and p.info.configured):
                return False
            if needs_audio and not p.info.supports_audio:
                return False
            if needs_document and not p.info.supports_document:
                return False
            # Video can be represented by verified keyframes, so image multimodality is sufficient.
            if needs_visual and not p.info.multimodal:
                return False
            return True

        if preferred and preferred not in {'auto', 'demo'}:
            p = self.providers.get(preferred)
            return self._select(p if compatible(p) else None)

        open_first = ['open_model'] if 'open_model' in self.providers else []
        if needs_audio or needs_document:
            order = ['gemini', 'qwen', 'doubao', 'openai', 'anthropic', 'deepseek'] + open_first
        elif needs_visual:
            order = open_first + ['qwen', 'doubao', 'gemini', 'openai', 'anthropic', 'deepseek']
        else:
            # Routine text planning/tool coordination can preferentially use an operator-provided
            # current open-weight model; deterministic EvoGain + verifier boundaries remain authoritative.
            order = open_first + ['deepseek', 'qwen', 'doubao', 'openai', 'anthropic', 'gemini']
        for key in order:
            p = self.providers.get(key)
            if compatible(p):
                return self._select(p)
        return self._select(None)
