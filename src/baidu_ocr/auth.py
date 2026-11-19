"""用 API Key + Secret Key 换 access_token。默认不发请求；绝不打印密钥或 token。"""

from __future__ import annotations

import os
import time
from typing import Any

TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"


class AuthError(RuntimeError):
    pass


class ExternalDisabled(RuntimeError):
    """allow_external 为 false，拒绝任何对外请求。"""


def key_status(cfg: dict) -> dict:
    """只报告环境变量是否已设置，不返回也不打印值。"""
    auth = cfg.get("auth") or {}
    api_env = auth.get("api_key_env") or "BAIDU_OCR_API_KEY"
    secret_env = auth.get("secret_key_env") or "BAIDU_OCR_SECRET_KEY"
    return {
        "api_key_env": api_env,
        "api_key_set": bool(os.environ.get(api_env)),
        "secret_key_env": secret_env,
        "secret_key_set": bool(os.environ.get(secret_env)),
        "allow_external": bool(cfg.get("allow_external")),
    }


def _read_keys(cfg: dict) -> tuple[str, str]:
    auth = cfg.get("auth") or {}
    api_env = auth.get("api_key_env") or "BAIDU_OCR_API_KEY"
    secret_env = auth.get("secret_key_env") or "BAIDU_OCR_SECRET_KEY"
    api_key = "".join((os.environ.get(api_env) or "").split())  # 去掉粘贴时混入的空白
    secret = "".join((os.environ.get(secret_env) or "").split())
    if not api_key or not secret:
        missing = [n for n, v in ((api_env, api_key), (secret_env, secret)) if not v]
        raise AuthError("缺少环境变量: " + ", ".join(missing))
    return api_key, secret


class TokenProvider:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._token: str | None = None
        self._expires_at: float = 0.0

    def get(self) -> str:
        if not self.cfg.get("allow_external"):
            raise ExternalDisabled("allow_external=false，拒绝获取 access_token")
        now = time.time()
        if self._token and now < self._expires_at - 60:
            return self._token
        import requests

        api_key, secret = _read_keys(self.cfg)
        url = (self.cfg.get("auth") or {}).get("token_url") or TOKEN_URL
        timeout = float((self.cfg.get("auth") or {}).get("timeout_sec") or 30)
        # 不把 key 写入日志。请求失败只暴露 error 字段名。
        resp = requests.post(
            url,
            params={
                "grant_type": "client_credentials",
                "client_id": api_key,
                "client_secret": secret,
            },
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        data: dict[str, Any] = {}
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise AuthError(f"token 响应不是 JSON，HTTP {resp.status_code}") from exc
        token = data.get("access_token")
        if not token:
            err = data.get("error") or data.get("error_description") or f"HTTP {resp.status_code}"
            raise AuthError(f"获取 access_token 失败: {err}")
        self._token = str(token)
        self._expires_at = now + float(data.get("expires_in") or 2592000)
        return self._token

    def redact(self, text: str) -> str:
        """从文本中抹去已缓存 token（若有）。不读取环境变量值到日志。"""
        if self._token and self._token in (text or ""):
            return text.replace(self._token, "[REDACTED_TOKEN]")
        return text
