"""gpt-6.2-astra 视觉复核：价格、图像 token 估算、请求体、Batch 辅助。导入时不联网。

价格（2026-11-18 从 OpenAI 官方页核实：platform.openai.com/docs/pricing 与 /docs/models/gpt-6.2-astra）：
  Standard 输入 $8 / 输出 $40 / 缓存输入 $0.4（每百万 token，短上下文 <272K）；Batch 为 50%：$4 / $20 / $0.2。
图像 token：官方 vision 文档给出 gpt-6-astra 为 32px 补丁、detail=high 补丁预算 2,500、倍率 1.2；
  gpt-6.2-astra 未单独列出，按同族规则估算（报告中注明）。
"""
from __future__ import annotations

import base64
import json
import math

PRICES = {"standard": {"input": 8.0, "output": 40.0, "cached": 0.4},
          "batch": {"input": 4.0, "output": 20.0, "cached": 0.2}}
PATCH = 32
PATCH_BUDGET_HIGH = 2500
MULT = 1.2


def image_tokens(w: int, h: int, detail: str = "high") -> int:
    if detail == "low":
        s = min(1.0, 512 / max(w, h))
        w, h = int(w * s), int(h * s)
    n = math.ceil(w / PATCH) * math.ceil(h / PATCH)
    if detail == "high" and n > PATCH_BUDGET_HIGH:
        f = math.sqrt(PATCH * PATCH * PATCH_BUDGET_HIGH / (w * h))
        f *= min(math.floor(w * f / PATCH) / (w * f / PATCH), math.floor(h * f / PATCH) / (h * f / PATCH))
        n = math.ceil(int(w * f) / PATCH) * math.ceil(int(h * f) / PATCH)
    return math.ceil(n * MULT)


def usd(prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0, mode: str = "standard") -> float:
    p = PRICES[mode]
    billed = max(0, prompt_tokens - cached_tokens)
    return round(billed / 1e6 * p["input"] + cached_tokens / 1e6 * p["cached"] + completion_tokens / 1e6 * p["output"], 6)


def build_body(model: str, prompt: str, image_bytes: bytes, detail: str, effort: str, max_out: int) -> dict:
    url = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
    return {"model": model, "reasoning_effort": effort, "max_completion_tokens": int(max_out),
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt},
                                                      {"type": "image_url", "image_url": {"url": url, "detail": detail}}]}]}


def batch_line(custom_id: str, body: dict) -> str:
    return json.dumps({"custom_id": custom_id, "method": "POST", "url": "/v1/chat/completions", "body": body}, ensure_ascii=False)


def parse_chat(resp: dict) -> tuple[str, dict, str | None]:
    ch = (resp.get("choices") or [{}])[0]
    text = ((ch.get("message") or {}).get("content")) or ""
    u = resp.get("usage") or {}
    usage = {"prompt_tokens": int(u.get("prompt_tokens") or 0), "completion_tokens": int(u.get("completion_tokens") or 0),
             "cached_tokens": int(((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0),
             "reasoning_tokens": int(((u.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0)}
    return str(text).strip(), usage, ch.get("finish_reason")


def result_ok(kind: str, text: str, finish: str | None) -> tuple[bool, str]:
    """截断或（非漏读行、非印章）空输出视为失败，不合并。"""
    if finish == "length":
        return False, "finish_reason=length（输出被截断）"
    if not text and kind in ("line", "table_band"):
        return False, "空输出"
    return True, ""


def worst_case_usd(n_req: int, prompt_tokens_total: int, max_out: int, mode: str) -> float:
    return usd(prompt_tokens_total, n_req * max_out, 0, mode)
