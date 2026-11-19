"""OpenAI 视觉模型调用（默认 dry-run，不联网）。

外发闸门（三项同时满足才发请求，缺一项即拒绝并退出）：
  1) 配置文件（config_hybrid_live.yaml 风格）里 vlm.allow_external: true；
  2) 命令行显式 --live；
  3) 环境变量（默认 OPENAI_API_KEY）存在——只检查有无，绝不打印、不写日志。
dry-run 模式不读取任何 key 环境变量，只写「将要发送什么」的清单。
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

from .crops import prompt_for, prompt_hash, usd_from_usage

API_URL = "https://api.openai.com/v1/chat/completions"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def live_gate(cfg: dict, live_flag: bool, environ=None) -> list[str]:
    """返回拒绝原因列表；空列表表示可以外发。environ 只在 live_flag 为真时才被查看。"""
    v = (cfg or {}).get("vlm") or {}
    why = []
    if not live_flag:
        why.append("未加 --live（默认 dry-run）")
    if v.get("allow_external") is not True:
        why.append("配置 vlm.allow_external 不是 true")
    if live_flag and v.get("allow_external") is True:  # 开关未开时连 key 是否存在都不查
        env = os.environ if environ is None else environ
        if not env.get(v.get("api_key_env") or "OPENAI_API_KEY"):
            why.append(f"环境变量 {v.get('api_key_env') or 'OPENAI_API_KEY'} 不存在（不打印值）")
    return why


def build_request(item: dict, prompt: str, image_bytes: bytes) -> dict:
    url = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
    max_out = 4000 if item["kind"] == "table_band" else 1500
    body = {"model": item["model"],
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt},
                                                      {"type": "image_url", "image_url": {"url": url, "detail": item["detail"]}}]}]}
    if item["model"].startswith("gpt-5"):
        body.update({"reasoning_effort": "low", "verbosity": "low", "max_completion_tokens": max_out})
    else:
        body.update({"temperature": 0, "max_tokens": max_out})
    return body


def _post_openai(body: dict, api_key: str, timeout: float) -> dict:
    import requests

    r = requests.post(API_URL, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                      data=json.dumps(body), timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def parse_response(resp: dict) -> tuple[str, dict, str | None]:
    ch = (resp.get("choices") or [{}])[0]
    text = ((ch.get("message") or {}).get("content")) or ""
    u = resp.get("usage") or {}
    usage = {"prompt_tokens": int(u.get("prompt_tokens") or 0), "completion_tokens": int(u.get("completion_tokens") or 0),
             "total_tokens": int(u.get("total_tokens") or 0),
             "cached_tokens": int(((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0),
             "reasoning_tokens": int(((u.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0)}
    return str(text).strip(), usage, ch.get("finish_reason")


class Runner:
    def __init__(self, root: Path, out_dir: Path, cfg: dict, live: bool, poster: Callable | None = None, environ=None):
        self.root, self.out, self.cfg, self.live = root, out_dir, cfg, live
        v = cfg.get("vlm") or {}
        self.soft = float(v.get("soft_cap_usd", 5.0))
        self.hard = float(v.get("hard_cap_usd", 10.0))
        self.retries = int(v.get("max_retries", 2))
        self.timeout = float(v.get("timeout_sec", 120))
        self.poster = poster or _post_openai
        self.lock = threading.Lock()
        self.ledger = out_dir / "vlm" / "ledger.jsonl"
        self.spent = self._ledger_spent()
        self.stopped = ""
        self._key = None
        if live:
            why = live_gate(cfg, True, environ)
            if why:
                raise PermissionError("拒绝外发：" + "；".join(why))
            env = os.environ if environ is None else environ
            self._key = env[v.get("api_key_env") or "OPENAI_API_KEY"]  # 只在内存中使用，不打印、不落盘

    def _ledger_spent(self) -> float:
        if not self.ledger.exists():
            return 0.0
        s = 0.0
        for line in self.ledger.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            # 每行都是一次尝试记录（注意 "kind" 字段是区域类型，不能用来判断）
            if "usd" in rec and not rec.get("cache_hit"):
                s += float(rec.get("usd") or 0)
        return s

    def _log(self, rec: dict):
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, self.ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def run_item(self, item: dict, use_cache: bool = True) -> dict:
        prompt_kind, prompt = prompt_for(item)
        res_path = self.out / "vlm" / "results" / item["route"] / f"{item['id']}.json"
        cache_path = self.out / "vlm" / "results" / "_cache" / f"{item['sha256']}-{item['model']}-{prompt_kind}-{prompt_hash(prompt)}.json"
        crop = self.root / item["crop_path"]
        base = {"item_id": item["id"], "route": item["route"], "page": item["page"], "kind": item["kind"], "reasons": item["reasons"],
                "model": item["model"], "detail": item["detail"], "prompt_kind": prompt_kind, "prompt_sha12": prompt_hash(prompt), "crop": item["crop_path"],
                "crop_sha256": item["sha256"], "crop_wh": [item["crop_w"], item["crop_h"]], "bbox_pt": item["bbox_pt"]}
        if not self.live:
            return {**base, "dry_run": True, "would_send_bytes": crop.stat().st_size if crop.exists() else None,
                    "est_prompt_tokens": item["est_prompt_tokens"], "est_completion_tokens": item["est_completion_tokens"], "est_usd": item["est_usd"]}
        if use_cache and cache_path.exists():
            c = json.loads(cache_path.read_text(encoding="utf-8"))
            rec = {**base, **{k: c[k] for k in ("text", "prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "reasoning_tokens", "usd", "finish_reason")},
                   "ok": True, "cache_hit": True, "reused_from": c.get("item_id"), "attempts": 0, "elapsed_sec": 0.0, "created_at": now_iso()}
            res_path.parent.mkdir(parents=True, exist_ok=True)
            res_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            self._log({"kind": "attempt", **{k: v for k, v in rec.items() if k != "text"}})
            return rec
        with self.lock:
            if self.stopped:
                return {**base, "ok": False, "skipped": self.stopped}
            if self.spent >= self.soft:
                self.stopped = f"已达软上限 ${self.soft}（累计 ${self.spent:.4f}），停止并回报"
                return {**base, "ok": False, "skipped": self.stopped}
            if self.spent + 3 * float(item["est_usd"]) > self.hard:
                self.stopped = f"下一次调用可能超过硬上限 ${self.hard}（累计 ${self.spent:.4f}）"
                return {**base, "ok": False, "skipped": self.stopped}
        body = build_request(item, prompt, crop.read_bytes())
        t0 = time.perf_counter()
        err, attempts = "", 0
        while attempts <= self.retries:
            attempts += 1
            try:
                resp = self.poster(body, self._key, self.timeout)
                text, usage, fin = parse_response(resp)
                usd = usd_from_usage(item["model"], usage["prompt_tokens"], usage["completion_tokens"], usage["cached_tokens"])
                rec = {**base, "ok": True, "text": text, **usage, "usd": usd, "finish_reason": fin, "attempts": attempts,
                       "elapsed_sec": round(time.perf_counter() - t0, 3), "created_at": now_iso(), "cache_hit": False}
                with self.lock:
                    self.spent += usd
                res_path.parent.mkdir(parents=True, exist_ok=True)
                res_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
                self._log({"kind": "attempt", **{k: v for k, v in rec.items() if k != "text"}})
                return rec
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {str(e)[:300]}"
                if self._key and self._key in err:
                    err = err.replace(self._key, "***")
                time.sleep(min(2 * attempts, 6))
        rec = {**base, "ok": False, "error": err, "attempts": attempts, "elapsed_sec": round(time.perf_counter() - t0, 3), "created_at": now_iso(), "usd": 0.0}
        self._log({"kind": "attempt", **rec})
        return rec

    def run(self, items: list[dict], workers: int = 1, use_cache: bool = True) -> list[dict]:
        if workers <= 1 or not self.live:
            return [self.run_item(i, use_cache) for i in items]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(lambda i: self.run_item(i, use_cache), items))
