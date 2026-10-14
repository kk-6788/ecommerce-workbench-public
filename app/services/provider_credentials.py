"""Per-owner BYOK settings; no global or environment fallback credentials."""
import re
from app.services.libtv_connection import Connection

CATALOG = {
    'typesafe': {'label': 'TypeSafe 审核', 'base_url': 'https://api.typesafe.ai/v1',
                 'purpose': 'AI 证据审核', 'default_model': 'jev-latest'},
    'seedance': {'label': '火山方舟 / Seedance', 'base_url': 'https://ark.cn-beijing.volces.com/api/v3',
                 'purpose': '视频制作', 'default_model': ''},
    'openai_image': {'label': 'OpenAI 图片', 'base_url': 'https://api.openai.com/v1',
                    'purpose': '图片制作', 'default_model': ''},
}


class Credentials:
    def __init__(self, settings):
        self.vault = Connection(settings)

    def _key(self, owner, provider):
        if provider not in CATALOG:
            raise ValueError('暂不支持此服务，请选择已有服务类型')
        return 'byok:' + owner + ':' + provider

    def list(self, owner):
        result = []
        for name, descriptor in CATALOG.items():
            config = self.vault.load(self._key(owner, name)) or {}
            result.append({'provider': name, **descriptor,
                           'configured': bool(config.get('api_key')),
                           'model': config.get('model', descriptor['default_model']),
                           'revision': config.get('revision', 0),
                           'status': 'CONFIGURED_UNVERIFIED' if config.get('api_key') else 'NEEDS_CONFIGURATION',
                           'billing_enabled': False})
        return result

    def save(self, owner, provider, api_key, model):
        key = self._key(owner, provider)
        if not api_key or api_key != api_key.strip() or len(api_key) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in api_key):
            raise ValueError('API Key 不能为空，且不能包含空格或换行')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}', model):
            raise ValueError('请填写服务商提供的模型或推理接入点 ID')
        previous = self.vault.load(key) or {}
        self.vault.save(key, {'api_key': api_key, 'model': model,
                              'revision': previous.get('revision', 0) + 1})
        return next(row for row in self.list(owner) if row['provider'] == provider)

    def delete(self, owner, provider):
        self.vault.remove(self._key(owner, provider))
        return {'removed': True}

    def resolve(self, owner, provider):
        value = self.vault.load(self._key(owner, provider))
        if not value:
            raise ValueError('当前用户尚未配置此服务；不会使用其他人的密钥')
        return {**value, 'base_url': CATALOG[provider]['base_url']}
