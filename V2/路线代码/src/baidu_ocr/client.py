"""方案 A 客户端：通用文字识别标准/高精度含位置版。默认 dry-run，不发请求。"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

from .auth import ExternalDisabled, TokenProvider
from .cost import CostCapExceeded, CostTracker

GENERAL_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/general"
ACCURATE_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate"
API_URLS = {"general": GENERAL_URL, "accurate": ACCURATE_URL}

# 官方限制（见 docs/baidu_api.md）
LIMITS = {
    "general": {"max_b64_mb": 8, "max_side": 4096, "min_side": 15},
    "accurate": {"max_b64_mb": 10, "max_side": 8192, "min_side": 15},
}


class ClientError(RuntimeError):
    pass


def default_params() -> dict[str, str]:
    return {
        "language_type": "CHN_ENG",
        "detect_direction": "true",
        "paragraph": "true",
        "vertexes_location": "true",
        "probability": "true",
        "recognize_granularity": "big",
    }


class BaiduOcrClient:
    def __init__(self, cfg: dict, tracker: CostTracker | None = None):
        self.cfg = cfg
        self.tracker = tracker or CostTracker.from_config(cfg)
        self.tokens = TokenProvider(cfg)

    def recognize_image(
        self,
        image_path: str | Path,
        api: str,
        *,
        page: int | None = None,
        filename: str | None = None,
        mock_response: dict | None = None,
        extra_params: dict | None = None,
    ) -> dict[str, Any]:
        """识别一页图片。allow_external=false 时必须提供 mock 或只返回 dry-run 占位。"""
        if api not in API_URLS:
            raise ClientError(f"未知接口: {api}")
        allow = bool(self.cfg.get("allow_external"))
        t0 = time.perf_counter()
        if not allow:
            elapsed = time.perf_counter() - t0
            self.tracker.add(
                api=api,
                success=False,
                elapsed_sec=elapsed,
                page=page,
                filename=filename,
                dry_run=True,
                extra={"reason": "allow_external_false"},
            )
            if mock_response is not None:
                return {
                    "ok": True,
                    "dry_run": True,
                    "api": api,
                    "url": API_URLS[api],
                    "response": mock_response,
                    "elapsed_sec": elapsed,
                }
            return {
                "ok": False,
                "dry_run": True,
                "api": api,
                "url": API_URLS[api],
                "skipped": True,
                "reason": "allow_external_false",
                "elapsed_sec": elapsed,
            }

        # 以下分支只有 allow_external=true 才会执行
        ok_charge, cap = self.tracker.can_charge(api)
        if not ok_charge:
            raise CostCapExceeded(f"费用硬上限，跳过 {api}")

        import requests
        from PIL import Image

        image_path = Path(image_path)
        with image_path.open("rb") as f:
            raw = f.read()
        b64 = base64.b64encode(raw).decode("ascii")
        limits = LIMITS[api]
        if len(b64) > limits["max_b64_mb"] * 1024 * 1024:
            raise ClientError(f"图像 base64 超过 {api} 上限 {limits['max_b64_mb']}MB")
        with Image.open(image_path) as im:
            w, h = im.size
        if max(w, h) > limits["max_side"] or min(w, h) < limits["min_side"]:
            raise ClientError(f"图像边长超出 {api} 限制（最短>={limits['min_side']}，最长<={limits['max_side']}），实际 {w}x{h}")

        token = self.tokens.get()
        params = default_params()
        if extra_params:
            params.update({k: str(v) for k, v in extra_params.items()})
        params["image"] = b64
        url = API_URLS[api] + "?access_token=" + token
        timeout = float((self.cfg.get("auth") or {}).get("timeout_sec") or 60)
        resp = requests.post(
            url,
            data=params,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
        )
        elapsed = time.perf_counter() - t0
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            self.tracker.add(api=api, success=False, elapsed_sec=elapsed, page=page, filename=filename)
            raise ClientError(f"{api} 响应不是 JSON，HTTP {resp.status_code}") from exc
        # 成功调用才计费；业务 error_code 视为失败
        err = data.get("error_code")
        success = resp.status_code == 200 and (not err or err in {0, "0", None})
        self.tracker.add(api=api, success=success, elapsed_sec=elapsed, page=page, filename=filename)
        if not success:
            return {
                "ok": False,
                "dry_run": False,
                "api": api,
                "error_code": err,
                "error_msg": data.get("error_msg"),
                "elapsed_sec": elapsed,
            }
        return {
            "ok": True,
            "dry_run": False,
            "api": api,
            "response": data,
            "elapsed_sec": elapsed,
        }

    def recognize_with_retry(
        self,
        image_path: str | Path,
        api: str,
        *,
        coverage_check,
        page: int | None = None,
        filename: str | None = None,
        mock_responses: list[dict] | None = None,
    ) -> dict[str, Any]:
        """识别并做行框覆盖检查；缺行则重试，仍缺则在结果里标明。"""
        max_retries = int((self.cfg.get("coverage") or {}).get("max_retries") or 1)
        attempts = []
        last = None
        mocks = list(mock_responses or [])
        for attempt in range(max_retries + 1):
            mock = mocks[attempt] if attempt < len(mocks) else (mocks[-1] if mocks else None)
            result = self.recognize_image(
                image_path, api, page=page, filename=filename, mock_response=mock
            )
            last = result
            words = ((result.get("response") or {}).get("words_result")) or []
            cov = coverage_check(words)
            attempts.append({"attempt": attempt, "coverage": cov, "ok": result.get("ok"), "dry_run": result.get("dry_run")})
            if cov.get("complete"):
                break
            # 未覆盖：重试
        gaps = (attempts[-1]["coverage"].get("missing") if attempts else []) or []
        return {
            "result": last,
            "attempts": attempts,
            "coverage": attempts[-1]["coverage"] if attempts else None,
            "retried": len(attempts) > 1,
            "still_missing": gaps,
        }
