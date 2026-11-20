#!/usr/bin/env python3
"""synth18 纯文本 LLM 纠错测试（gpt-4o-mini / gpt-4o / DeepSeek / Qwen / Ernie）。

子命令：
  prepare   由 B 文档解析标准版在 synth18 上的原始中间 JSON 生成分块（固定：每页一块，一段 = pred_pages 的一个 section；印章文字不送）、
            组装请求、按 tiktoken / 官方近似估算 token 与费用 → output/synth18_llm/{chunks.json, estimate.json, requests/}（不联网）
  run       默认 dry-run（只校验与打印将发什么）；--live 才外发。需 config_llm_synth18_live.yaml 里 allow_external: true 且 approval 非空。
            仅向 approved_providers 列出的提供商外发。每次调用前检查「累计已花（含已完成的 $0.1574）+ 本次最坏情况 ≤ hard_cap_usd」，否则停。
            ledger / 日志只记 token/费用/耗时/HTTP 状态，不记 key。
  score     计分（blind20_score 解析逻辑指向 synth18 参照，不套 blind20 修正）、错字分类、自动列出 LLM 新引入的错误 → report.md（不联网）
  selftest  用「原样返回草稿」和人为改坏的假结果跑一遍 score 流程，验证计分 = B 原始、检测器能抓到改数字/删除/编造（不联网）
"""
from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import math
import os
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from baidu_ocr.postprocess import pred_pages  # noqa: E402
import synth18_run as S  # noqa: E402

OUT = ROOT / "output" / "synth18_llm"
PROMPT = OUT / "prompt_v1.txt"
PROMPT_SHA = "86ae3d1c77ba3276cc209077ec04166ae878031a09fc285057c5b345430d231a"
LIVE_CFG = ROOT / "config_llm_synth18_live.yaml"
B_DOC = S.W / "final" / "scheme_b_standard" / "blind20-scan.json"  # 内容来自 synth18-scan.pdf（加载代码写死文件名）
B_BASE = (96.28, 4.79)
MODELS = ["gpt-4o-mini", "gpt-4o", "deepseek", "qwen", "ernie"]
# 主报告五列（不含 gpt-4o-mini）；显示名用于表头
REPORT_COLS = ["B", "gpt-4o", "deepseek", "qwen", "ernie"]
REPORT_LABELS = {"B": "B", "gpt-4o": "gpt-4o", "deepseek": "DeepSeek", "qwen": "Qwen", "ernie": "Ernie"}
DEFAULT_APPROVED_PROVIDERS = ("openai", "deepseek")


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def wj(p, o):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def prompt_text():
    t = PROMPT.read_text(encoding="utf-8")
    assert hashlib.sha256(t.encode("utf-8")).hexdigest() == PROMPT_SHA, "prompt_v1.txt 被改过"
    return t


def load_cfg():
    return yaml.safe_load(LIVE_CFG.read_text(encoding="utf-8"))["llm"]


def _parse_env_file(path: Path):
    """Parse KEY=VALUE lines. Values stay in-process; caller must not print them."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip("'").strip('"')
    return out


def key_file_status(path_str):
    """Existence / mode / size only. Never returns file contents."""
    if not path_str:
        return {"configured": False}
    pth = Path(path_str)
    st = {"configured": True, "path": path_str, "exists": pth.is_file()}
    if pth.is_file():
        mode = pth.stat().st_mode & 0o777
        st["mode"] = oct(mode)
        st["mode_ok"] = mode == 0o600
        st["size_bytes"] = pth.stat().st_size
    return st


def load_api_key(mc):
    """Load a key in-process only. Prefer api_key_file (dotenv or raw), else api_key_env.

    Never print, log, write, or copy the value. Returns (key_or_None, status_dict_without_value).
    """
    env_name = (mc.get("api_key_env") or "").strip()
    path_str = (mc.get("api_key_file") or "").strip()
    info = {"env": env_name or None, "file": key_file_status(path_str), "source": None, "present": False, "length": 0}
    key = None
    if path_str:
        pth = Path(path_str)
        if pth.is_file():
            parsed = _parse_env_file(pth)
            if env_name and env_name in parsed and parsed[env_name]:
                key = parsed[env_name]
                info["source"] = "file"
            elif not env_name and len(parsed) == 0:
                raw = pth.read_text(encoding="utf-8").strip()
                if raw and "\n" not in raw and "=" not in raw:
                    key = raw
                    info["source"] = "file"
    if key is None and env_name:
        ev = os.environ.get(env_name)
        if ev:
            key = ev.strip().strip("'").strip('"')
            info["source"] = "env"
    if key:
        info["present"] = True
        info["length"] = len(key)
    return key, info


def redact(text, *secrets):
    t = "" if text is None else str(text)
    for s in secrets:
        if s:
            t = t.replace(s, "***")
    return t


def approved_providers(cfg):
    v = cfg.get("approved_providers")
    if not v:
        return set(DEFAULT_APPROVED_PROVIDERS)
    return set(v)


# ------------------------------------------------------------------ 分块
def chunks():
    """固定分块：每页一块；页内每个 pred_pages section 是一段（id = pXX-sYY）；印章文字不送、原样保留。"""
    doc = jl(B_DOC)
    pr = pred_pages(doc)
    out = []
    for p in sorted(pr, key=int):
        segs = [{"id": f"p{int(p):02d}-s{i + 1:02d}", "type": s["section"], "text": s["text"]} for i, s in enumerate(pr[p]["sections"])]
        out.append({"page": int(p), "segments": segs, "seal": pr[p].get("seal") or ""})
    return pr, out


def user_msg(ch):
    return json.dumps({"page": ch["page"], "segments": ch["segments"]}, ensure_ascii=False)


def expected_out(ch):
    return json.dumps({"segments": [{"id": s["id"], "text": s["text"]} for s in ch["segments"]]}, ensure_ascii=False)


# ------------------------------------------------------------------ token / 价格
_ENC = None


def oa_tokens(text):
    global _ENC
    if _ENC is None:
        import tiktoken
        _ENC = tiktoken.get_encoding("o200k_base")  # gpt-4o / gpt-4o-mini 的分词器
    return len(_ENC.encode(text))


def ds_tokens(text):
    """DeepSeek 官方近似：1 个中文字 ≈ 0.6 token，1 个英文字符 ≈ 0.3 token（api-docs.deepseek.com/quick_start/token_usage）。"""
    cjk = sum(1 for c in text if unicodedata.east_asian_width(c) in ("W", "F"))
    return math.ceil(cjk * 0.6 + (len(text) - cjk) * 0.3)


def count_tokens(mc, text):
    # 无官方本地分词器的提供商用 tiktoken 只估 max_tokens；DeepSeek 沿用其官方字符近似。
    if mc["provider"] == "deepseek":
        return ds_tokens(text)
    return oa_tokens(text)


def tokenizer_label(mc):
    if mc["provider"] == "deepseek":
        return "官方近似（中文 0.6 / 英文 0.3 token 每字符）"
    if mc["provider"] in ("qwen", "ernie"):
        return "tiktoken o200k_base（无官方本地分词器，仅估 max_tokens；费用以 API usage 为准）"
    return "tiktoken o200k_base"


def cny_rate(mc=None, cfg=None):
    if mc and mc.get("cny_per_usd"):
        return float(mc["cny_per_usd"])
    if cfg and cfg.get("cny_per_usd"):
        return float(cfg["cny_per_usd"])
    return 7.2


def _as_usd_price(src, rate):
    return {k: (None if v is None else v / rate) for k, v in src.items()}


def price(mc, when=None, cfg=None):
    rate = cny_rate(mc, cfg)
    if "price_cny_per_1m" in mc:
        return _as_usd_price(mc["price_cny_per_1m"], rate)
    if "price_cny_per_1m_peak" in mc:
        h = (when or datetime.now(timezone.utc))
        peak = h.weekday() < 5 and any(a <= h.hour < b for a, b in mc["peak_hours_utc"])
        src = mc["price_cny_per_1m_peak"] if peak else mc["price_cny_per_1m_offpeak"]
        return _as_usd_price(src, rate)
    if "price_usd_per_1m" in mc:
        return mc["price_usd_per_1m"]
    h = (when or datetime.now(timezone.utc))
    peak = h.weekday() < 5 and any(a <= h.hour < b for a, b in mc["peak_hours_utc"])
    return mc["price_usd_per_1m_peak"] if peak else mc["price_usd_per_1m_offpeak"]


def usd(mc, p_tok, c_tok, cached=0, when=None, cfg=None):
    pr = price(mc, when, cfg)
    cached_rate = pr["cached_input"] if "cached_input" in pr and pr["cached_input"] is not None else pr["input"]
    return round(((p_tok - cached) * pr["input"] + cached * cached_rate + c_tok * pr["output"]) / 1e6, 6)


def _price_band(mc, when=None, cfg=None):
    if "price_usd_per_1m_peak" in mc or "price_cny_per_1m_peak" in mc:
        h = (when or datetime.now(timezone.utc))
        peak = h.weekday() < 5 and any(a <= h.hour < b for a, b in mc["peak_hours_utc"])
        return "peak" if peak else "offpeak"
    if "price_cny_per_1m" in mc:
        return "cny_list"
    return "standard"


def peak_or_list_price(mc, cfg=None):
    """Conservative unit price for estimates (peak / list, no cache)."""
    rate = cny_rate(mc, cfg)
    if "price_cny_per_1m_peak" in mc:
        return _as_usd_price(mc["price_cny_per_1m_peak"], rate)
    if "price_cny_per_1m" in mc:
        return _as_usd_price(mc["price_cny_per_1m"], rate)
    return mc.get("price_usd_per_1m_peak") or mc["price_usd_per_1m"]


def max_out(est_out, reserve=0):
    return int(min(16384, max(1024, math.ceil(est_out * 2) + 256 + int(reserve or 0))))


def body(mc, cfg, sysp, ch, mo):
    b = {"model": mc["model"], "temperature": cfg["temperature"], "max_tokens": mo,
         "messages": [{"role": "system", "content": sysp}, {"role": "user", "content": user_msg(ch)}]}
    if mc.get("json_mode"):
        b["response_format"] = {"type": "json_object"}
    b.update(mc.get("extra_body") or {})
    return b


def already_spent(path=None):
    path = path or (OUT / "ledger.jsonl")
    if not path.exists():
        return 0.0
    tot = 0.0
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            tot += float(json.loads(ln).get("usd") or 0)
    return round(tot, 6)


def prior_actual_tokens(path=None):
    """上一轮成功请求的实际 tokens。优先用 gpt-4o（与任务所称 ~20k/~8.6k 一致）。"""
    path = path or (OUT / "ledger.jsonl")
    if not path.exists():
        return {}
    by = {}
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r.get("http") != 200:
            continue
        k = r["model_key"]
        by.setdefault(k, {"prompt_tokens": 0, "completion_tokens": 0, "n": 0})
        by[k]["prompt_tokens"] += int(r.get("prompt_tokens") or 0)
        by[k]["completion_tokens"] += int(r.get("completion_tokens") or 0)
        by[k]["n"] += 1
    pick = "gpt-4o" if "gpt-4o" in by else (next(iter(by), None))
    if not pick:
        return {}
    d = dict(by[pick])
    d["source_model"] = pick
    return d


# ------------------------------------------------------------------ prepare
def prepare(args):
    cfg = load_cfg()
    sysp = prompt_text()
    pr, chs = chunks()
    wj(OUT / "draft_pred_pages.json", pr)
    wj(OUT / "chunks.json", {"source": str(B_DOC.relative_to(ROOT)), "source_sha256": hashlib.sha256(B_DOC.read_bytes()).hexdigest(),
                             "rule": "每页一块；一段 = pred_pages 的一个 section（body/table）；印章文字不送", "chunks": chs})
    est = {"created_at": now(), "prompt_sha256": PROMPT_SHA, "n_chunks": len(chs), "n_segments": sum(len(c["segments"]) for c in chs),
           "draft_chars": sum(len(s["text"]) for c in chs for s in c["segments"]), "models": {}}
    for m in MODELS:
        mc = cfg["models"][m]
        P = C = W = 0
        rows = []
        for ch in chs:
            p = count_tokens(mc, sysp) + count_tokens(mc, user_msg(ch)) + 10
            c = math.ceil(count_tokens(mc, expected_out(ch)) * 1.1)
            mo = max_out(c, mc.get("thinking_reserve_tokens") or 0)
            rows.append({"page": ch["page"], "prompt_tokens": p, "completion_tokens": c, "max_tokens": mo})
            P, C, W = P + p, C + c, W + mo
            wj(OUT / "requests" / m / f"p{ch['page']:02d}.json", body(mc, cfg, sysp, ch, mo))
        peak = peak_or_list_price(mc, cfg)
        rec = {"model": mc["model"], "provider": mc["provider"], "tokenizer": tokenizer_label(mc),
               "est_prompt_tokens": P, "est_completion_tokens": C,
               "est_usd": round((P * peak["input"] + C * peak["output"]) / 1e6, 4),
               "worst_case_usd": round((P * peak["input"] + W * peak["output"]) / 1e6, 4), "per_page": rows}
        if "price_cny_per_1m" in mc or "price_cny_per_1m_peak" in mc:
            rec["cny_per_usd"] = cny_rate(mc, cfg)
            src = mc.get("price_cny_per_1m_peak") or mc["price_cny_per_1m"]
            rec["price_cny_per_1m"] = src
        est["models"][m] = rec
        _, ks = load_api_key(mc)
        rec["key_status"] = {"env": ks["env"], "file_exists": ks["file"].get("exists"), "file_mode": ks["file"].get("mode"),
                             "present": ks["present"], "length": ks["length"], "source": ks["source"]}
    # 参照上一轮实际 usage（约 20k 输入 / 8.6k 输出）再估一遍千问、文心
    prior = prior_actual_tokens()
    est["prior_actual_tokens"] = prior
    if prior:
        for m in ("qwen", "ernie"):
            if m not in est["models"]:
                continue
            mc = cfg["models"][m]
            pk = peak_or_list_price(mc, cfg)
            # 按页把上一轮实际输入/输出和本模型 max_tokens 对齐
            pin, cout = prior["prompt_tokens"], prior["completion_tokens"]
            w = sum(r["max_tokens"] for r in est["models"][m]["per_page"])
            est["models"][m]["est_usd_from_prior_actual"] = round((pin * pk["input"] + cout * pk["output"]) / 1e6, 4)
            est["models"][m]["worst_case_usd_from_prior_max_tokens"] = round((pin * pk["input"] + w * pk["output"]) / 1e6, 4)
    est["est_usd_total"] = round(sum(v["est_usd"] for v in est["models"].values()), 4)
    est["worst_case_usd_total"] = round(sum(v["worst_case_usd"] for v in est["models"].values()), 4)
    est["already_spent_usd"] = already_spent()
    new_worst = sum(est["models"][m]["worst_case_usd"] for m in ("qwen", "ernie") if m in est["models"])
    est["remaining_cap_usd"] = round(cfg["hard_cap_usd"] - est["already_spent_usd"], 4)
    est["new_models_worst_plus_spent_usd"] = round(est["already_spent_usd"] + new_worst, 4)
    est["cap_usd"] = cfg["hard_cap_usd"]
    est["note"] = ("DeepSeek 按峰时价估（偏保守）；cached input 按 0 计。"
                   "Qwen/Ernie 官方价为人民币，按配置 cny_per_usd 换算美元。"
                   "已花金额计入 cap 检查。Ernie 官方文档未提供关闭思考的参数，最坏情况按 max_tokens（含思维链）计。")
    wj(OUT / "estimate.json", est)
    print(json.dumps({k: v for k, v in est.items() if k not in ("models", "prior_actual_tokens")}, ensure_ascii=False))
    for m, v in est["models"].items():
        show = {k: v[k] for k in ("est_prompt_tokens", "est_completion_tokens", "est_usd", "worst_case_usd") if k in v}
        if "est_usd_from_prior_actual" in v:
            show["est_usd_from_prior_actual"] = v["est_usd_from_prior_actual"]
            show["worst_case_usd_from_prior_max_tokens"] = v["worst_case_usd_from_prior_max_tokens"]
        if "key_status" in v:
            show["key"] = v["key_status"]
        print(m, show)


# ------------------------------------------------------------------ run
class Ledger:
    def __init__(self, path):
        self.path = path
        self.spent = 0.0
        if path.exists():
            for ln in path.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    self.spent += float(json.loads(ln).get("usd") or 0)

    def add(self, rec):
        self.spent += float(rec.get("usd") or 0)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def parse_resp(txt, ch):
    t = txt.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    o = json.loads(t)
    segs = o["segments"]
    got = {s["id"]: s["text"] for s in segs if isinstance(s, dict) and "id" in s and isinstance(s.get("text"), str)}
    ids = [s["id"] for s in ch["segments"]]
    return got, {"missing_ids": [i for i in ids if i not in got], "extra_ids": [i for i in got if i not in ids]}


def run(args):
    import requests
    cfg = load_cfg()
    sysp = prompt_text()
    _, chs = chunks()
    est = jl(OUT / "estimate.json")
    models = args.models.split(",") if args.models else MODELS
    if args.live:
        assert cfg["allow_external"] is True and cfg.get("approval"), "live 配置未启用 allow_external 或缺批准原话"
        dflt = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        assert dflt["allow_external"] is False, "默认 config.yaml 的 allow_external 必须保持 false"
    models = [x.strip() for x in models if x.strip()]
    allowed = approved_providers(cfg)
    led = Ledger(OUT / "ledger.jsonl")
    print(f"{'LIVE' if args.live else 'DRY-RUN'}  已花 ${led.spent:.4f} / 上限 ${cfg['hard_cap_usd']}  approved_providers={sorted(allowed)}")
    stop = False
    for m in models:
        mc = cfg["models"][m]
        key, ks = load_api_key(mc)
        print(f"{m}: provider={mc['provider']} model={mc['model']} key_env={ks['env']} key_present={ks['present']} key_len={ks['length']} key_source={ks['source']} file_mode={ks['file'].get('mode')}")
        if mc["provider"] not in allowed:
            print(f"{m}: 提供商 {mc['provider']} 不在 approved_providers，{'live 拒绝外发' if args.live else 'dry-run 仅预览'}，跳过发送")
            if args.live:
                continue
        if args.live and mc["provider"] not in allowed:
            continue
        if args.live and not key:
            src = ks["env"] or "（变量名未配置）"
            print(f"{m}: 未找到密钥（env={src} file_exists={ks['file'].get('exists')}），跳过")
            continue
        rows = {r["page"]: r for r in est["models"][m]["per_page"]}
        t_m = time.perf_counter()
        timeout = mc.get("timeout_sec") or cfg["timeout_sec"]
        sent = 0
        for ch in chs:
            rp = OUT / "results" / m / f"p{ch['page']:02d}.json"
            if rp.exists():
                continue
            r = rows[ch["page"]]
            worst = usd(mc, r["prompt_tokens"], r["max_tokens"], cfg=cfg)
            if led.spent + worst > cfg["hard_cap_usd"]:
                print(f"触顶：已花 ${led.spent:.4f} + 本次最坏 ${worst:.4f} > ${cfg['hard_cap_usd']}，停止")
                stop = True
                break
            b = body(mc, cfg, sysp, ch, r["max_tokens"])
            if not args.live or mc["provider"] not in allowed:
                print(f"[dry] {m} p{ch['page']:02d} → {mc['base_url']}/chat/completions  model={mc['model']}  est_in={r['prompt_tokens']} max_out={r['max_tokens']} worst=${worst:.4f}")
                continue
            sent += 1
            for att in range(cfg["max_retries"] + 1):
                t0 = time.perf_counter()
                when = datetime.now(timezone.utc)
                rec = {"at": when.isoformat(timespec="seconds"), "model_key": m, "model": mc["model"], "page": ch["page"], "attempt": att + 1}
                try:
                    resp = requests.post(f"{mc['base_url']}/chat/completions", json=b, timeout=timeout,
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
                    sec = round(time.perf_counter() - t0, 2)
                    rec["http"] = resp.status_code
                    rec["sec"] = sec
                    if resp.status_code != 200:
                        rec["error"] = redact(resp.text[:300], key)
                        rec["usd"] = 0.0
                        led.add(rec)
                        print(f"{m} p{ch['page']:02d} HTTP {resp.status_code}")
                        if resp.status_code in (429, 500, 502, 503, 504):
                            time.sleep(3 * (att + 1))
                            continue
                        break
                    j = resp.json()
                    u = j.get("usage") or {}
                    pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
                    cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0)
                    rec.update({"prompt_tokens": pt, "completion_tokens": ct, "cached_tokens": cached,
                                "usd": usd(mc, pt, ct, cached, when, cfg), "finish": j["choices"][0].get("finish_reason"),
                                "price_band": _price_band(mc, when, cfg),
                                "system_fingerprint": j.get("system_fingerprint"), "served_model": j.get("model")})
                    txt = j["choices"][0]["message"].get("content") or ""
                    try:
                        got, chk = parse_resp(txt, ch)
                        rec["parse_ok"] = True
                        rec.update(chk)
                    except Exception as e:  # noqa: BLE001
                        got, chk = {}, {"parse_error": redact(str(e)[:200], key)}
                        rec["parse_ok"] = False
                    led.add(rec)
                    wj(rp, {"page": ch["page"], "model": mc["model"], "prompt_sha256": PROMPT_SHA, "raw": txt, "segments": got, **chk,
                            "usage": u, "usd": rec["usd"], "sec": sec, "finish": rec["finish"]})
                    print(f"{m} p{ch['page']:02d} ok {pt}/{ct} tok ${rec['usd']:.5f} {sec}s parse={rec['parse_ok']} 累计 ${led.spent:.4f}")
                    break
                except requests.RequestException as e:
                    rec.update({"sec": round(time.perf_counter() - t0, 2), "error": type(e).__name__, "usd": 0.0})
                    led.add(rec)
                    print(f"{m} p{ch['page']:02d} 网络异常 {type(e).__name__}")
                    time.sleep(3 * (att + 1))
        print(f"{m} 墙钟 {time.perf_counter() - t_m:.1f}s")
        if stop:
            break
    print(f"累计 ${led.spent:.4f}")


# ------------------------------------------------------------------ score
def is_punct(ch):
    return ch in set("~～-—–·•|") or unicodedata.category(ch).startswith(("P", "Z")) or ch.isspace()


def digits(s):
    return re.findall(r"\d+", unicodedata.normalize("NFKC", s))


def draft_diff(a, b):
    """不看参照：草稿 a → LLM 输出 b 的改动。返回 (数字是否变, 删掉的非标点串, 新增的非标点串（扣除重排挪动的字）)。"""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    dele, ins = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("delete", "replace"):
            dele.append(a[i1:i2])
        if op in ("insert", "replace"):
            ins.append(b[j1:j2])
    ca, cb = Counter(c for c in a if not is_punct(c)), Counter(c for c in b if not is_punct(c))
    lost = "".join(c * n for c, n in (ca - cb).items())
    new = "".join(c * n for c, n in (cb - ca).items())
    return digits(a) != digits(b), lost, new, dele, ins


def build_pred(draft, chs, res):
    """把 LLM 结果套回草稿结构；缺页/解析失败/缺 id 的段用草稿原文（记下来）。"""
    pr = copy.deepcopy(draft)
    fb = []
    for ch in chs:
        r = res.get(ch["page"])
        for i, s in enumerate(ch["segments"]):
            if r is None or s["id"] not in (r.get("segments") or {}):
                fb.append({"page": ch["page"], "id": s["id"], "why": "无结果" if r is None else ("解析失败" if not r.get("segments") else "缺 id")})
                continue
            pr[str(ch["page"])]["sections"][i]["text"] = r["segments"][s["id"]]
    return pr, fb


def load_results(m, root=OUT):
    d = root / "results" / m
    return {int(p.stem[1:]): jl(p) for p in sorted(d.glob("p*.json"))} if d.exists() else {}


def score_all(systems: dict, out_dir: Path, costs: dict | None = None):
    import bestconfig_run as bc
    import synth18_score as SS
    bs = bc.load_mod("blind20_score", ROOT / "scripts" / "blind20_score.py")
    bs.REF, bs.MANIFEST = S.S18 / "blind-ref.txt", S.S18 / "manifest.json"
    bs.MANUAL.clear()
    full, nosign, rj, _ = SS.ref_texts()
    refs = {"full": bs.parse_ref(full), "nosign": bs.parse_ref(nosign)}
    mpages = {int(p["output_page"]): p for p in jl(S.S18 / "manifest.json")["pages"]}
    rng = SS.special_ranges(bs, refs["full"], rj)
    hs = bc.load_mod("hybrid_score", ROOT / "scripts" / "hybrid_score.py")
    hs.load_bs = lambda: bs
    bc.SIGN = ()
    R = {}
    for name, pr in systems.items():
        sc = {rk: bs.score_system(pr, rv, mpages, None) for rk, rv in refs.items()}
        tot, rows = Counter(), []
        spans = sorted(sc["full"]["error_spans"], key=lambda s: (s["page"], s["ref_start"]))
        for i, sp in enumerate(spans):
            p = sp["page"]
            rs, _ = hs.ref_page_string(bs, refs["full"], p, None)
            ps = bs.norm("".join(s["text"] for s in pr[str(p)]["sections"]))
            seal_chars = set(bs.norm(pr[str(p)].get("seal") or "")) | set(bs.norm("".join(refs["full"][p]["seals"])))
            same = [o for j, o in enumerate(spans) if o["page"] == p and j != i]
            c, why = bc.categorize(sp, ps, rs, seal_chars, set(), "".join(o["ref"] for o in same), "".join(o["pred"] for o in same))
            a, b = sp["ref_start"], sp["ref_end"]
            sk = next((k for (x0, x1, k) in rng.get(p, []) if (a < x1 and b > x0) or (a == b and x0 <= a <= x1)), None)
            if c == "星号":
                bk = "星号"
            elif sk == "签名":
                bk = "签名"
            elif sk == "竖排" or why == "发票竖排小字漏读":
                bk = "竖排"
            elif c in ("顺序", "标点", "真错字", "印章混入"):
                bk = c
            else:
                bk = "漏读和杂字"
            tot[bk] += sp["cost"]
            rows.append({**{k: sp[k] for k in ("page", "section", "ref", "pred", "cost", "context", "ref_start", "ref_end")}, "category": bk, "detail": why})
        R[name] = {"summary": {rk: bc.summ(v) for rk, v in sc.items()}, "cats": dict(tot), "rows": rows}
    # LLM 新引入的错误（对参照）：LLM 的错误段落在 B 原本正确的位置上
    base_rows = R["B"]["rows"]
    bad = defaultdict(set)
    ins_at = defaultdict(set)
    for r in base_rows:
        bad[r["page"]].update(range(r["ref_start"], r["ref_end"]))
        if r["ref_start"] == r["ref_end"]:
            ins_at[r["page"]].add(r["ref_start"])
    for name in systems:
        if name == "B":
            continue
        new = []
        for r in R[name]["rows"]:
            p, a, b = r["page"], r["ref_start"], r["ref_end"]
            fresh = (a == b and a not in ins_at[p] and a not in bad[p]) or (b > a and any(x not in bad[p] for x in range(a, b)))
            if not fresh:
                continue
            rp, pp_ = bs.norm(r["ref"]), bs.norm(r["pred"])
            if rp and not pp_:
                k = "删除"
            elif pp_ and not rp and any(not is_punct(c) for c in pp_):
                k = "编造/插入"
            elif digits(rp) != digits(pp_) and (re.search(r"\d", rp + pp_)):
                k = "改数字/金额/日期"
            elif all(is_punct(c) for c in rp + pp_):
                k = "标点改错"
            else:
                k = "改错字/替换"
            new.append({**{x: r[x] for x in ("page", "section", "ref", "pred", "cost", "context", "category")}, "kind": k})
        R[name]["new_vs_ref"] = new
    return R


def draft_checks(chs, res):
    out = []
    for ch in chs:
        r = res.get(ch["page"])
        if not r:
            continue
        for s in ch["segments"]:
            if s["id"] not in (r.get("segments") or {}):
                continue
            b = r["segments"][s["id"]]
            dchg, lost, new, _, _ = draft_diff(s["text"], b)
            if dchg or lost or new:
                out.append({"page": ch["page"], "id": s["id"], "digits_changed": dchg, "deleted_chars": lost, "added_chars": new,
                            "draft": s["text"][:200], "llm": b[:200]})
    return out


def write_report(R, chk, fb, est, led, models, path, extra_note=""):
    o = lambda n, s="overall", rk="full": R[n]["summary"][rk][s]  # noqa: E731
    names = ["B"] + models
    L = ["# synth18 纯文本 LLM 纠错测试", "", f"生成：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}（伦敦时间 UTC+0）。脚本 `scripts/synth18_llm.py`。",
         "", f"- 草稿：B 文档解析标准版在 synth18 上的原始输出（不加任何后处理），即上一轮 `B 原始` {B_BASE[0]}%/{B_BASE[1]}%。不重调百度。",
         "- 分块（固定）：每页一个请求；页内一段 = 计分用 pred_pages 的一个 section（body 一块 / table 一张表），id `pXX-sYY`；印章文字不送、原样保留。",
         f"- 提示词：`output/synth18_llm/prompt_v1.txt`，sha256 `{PROMPT_SHA}`（冻结，运行中逐次校验）。温度 0；JSON 输出。看结果后未调参。",
         "- 计分：`blind20_score` 的 parse_ref/score_system 指向 synth18 参照与 manifest，不套 blind20 修正；分类沿用 synth18 那一轮的口径。", extra_note, "",
         "## 1. 总表（签名入参照，主口径）", "",
         "### 五列对照（B / gpt-4o / DeepSeek / Qwen / Ernie）", ""]
    def cell(n, fn):
        if n not in R:
            return "—"
        return fn(n)
    def fee(n):
        if n == "B":
            return "—"
        val = sum(r.get("usd") or 0 for r in led if r.get("model_key") == n)
        return f"${val:.4f}" if any(r.get("model_key") == n and r.get("http") == 200 for r in led) else "—"
    headers = [REPORT_LABELS.get(n, n) for n in REPORT_COLS]
    L += ["| 指标 | " + " | ".join(headers) + " |", "|---|" + "---|" * len(REPORT_COLS)]
    L.append("| 读出率 | " + " | ".join(cell(n, lambda x: f"{o(x)['readout']:.2f}%") for n in REPORT_COLS) + " |")
    L.append("| CER | " + " | ".join(cell(n, lambda x: f"{o(x)['cer']:.2f}%") for n in REPORT_COLS) + " |")
    L.append("| 删/替/插 | " + " | ".join(cell(n, lambda x: f"{o(x)['D']}/{o(x)['S']}/{o(x)['I']}") for n in REPORT_COLS) + " |")
    L.append("| 正文 读出/CER | " + " | ".join(cell(n, lambda x: f"{o(x,'body')['readout']:.2f}% / {o(x,'body')['cer']:.2f}%") for n in REPORT_COLS) + " |")
    L.append("| 表格 读出/CER | " + " | ".join(cell(n, lambda x: f"{o(x,'table')['readout']:.2f}% / {o(x,'table')['cer']:.2f}%") for n in REPORT_COLS) + " |")
    L.append("| 费用 | " + " | ".join(fee(n) for n in REPORT_COLS) + " |")
    L += ["", "各系统明细（含 gpt-4o-mini 等未列入五列的对照）：", "",
          "| 系统 | 读出率 | CER | 删/替/插 | 正文 读出/CER | 表格 读出/CER | 相对 B CER |", "|---|---|---|---|---|---|---|"]
    for n in names:
        x, bo, ta = o(n), o(n, "body"), o(n, "table")
        L.append(f"| {n} | **{x['readout']:.2f}%** | **{x['cer']:.2f}%** | {x['D']}/{x['S']}/{x['I']} | {bo['readout']:.2f}% / {bo['cer']:.2f}% | {ta['readout']:.2f}% / {ta['cer']:.2f}% | {x['cer'] - o('B')['cer']:+.2f}pp |")
    L += ["", "剔除签名口径（blind20 口径）：", "", "| 系统 | 读出率 | CER | 删/替/插 |", "|---|---|---|---|"]
    for n in names:
        x = o(n, rk="nosign")
        L.append(f"| {n} | {x['readout']:.2f}% | {x['cer']:.2f}% | {x['D']}/{x['S']}/{x['I']} |")
    buckets = ["星号", "竖排", "顺序", "标点", "签名", "真错字", "印章混入", "漏读和杂字"]
    L += ["", "## 2. 错字分类（签名入参照，按编辑代价计字）", "", "| 类别 | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for b in buckets:
        L.append(f"| {b} | " + " | ".join(f"{R[n]['cats'].get(b, 0)}" + ("" if n == "B" else f" ({R[n]['cats'].get(b, 0) - R['B']['cats'].get(b, 0):+d})") for n in names) + " |")
    L.append("| **合计** | " + " | ".join(str(sum(R[n]["cats"].values())) for n in names) + " |")
    L += ["", "## 3. LLM 新引入的错误", "", "### 3a. 对参照：B 原本读对、LLM 改错的位置", "", "| 模型 | 改数字/金额/日期 | 删除 | 编造/插入 | 改错字/替换 | 标点改错 | 合计（字） |", "|---|---|---|---|---|---|---|"]
    kinds = ["改数字/金额/日期", "删除", "编造/插入", "改错字/替换", "标点改错"]
    for n in models:
        nv = R[n]["new_vs_ref"]
        cnt = Counter()
        for r in nv:
            cnt[r["kind"]] += r["cost"]
        L.append(f"| {n} | " + " | ".join(f"{sum(1 for r in nv if r['kind'] == k)} 处 / {cnt[k]} 字" for k in kinds) + f" | {sum(cnt.values())} |")
    for n in models:
        nv = sorted(R[n]["new_vs_ref"], key=lambda r: (kinds.index(r["kind"]), -r["cost"]))
        if not nv:
            continue
        L += ["", f"{n}（全部列出）：", "", "| 类型 | 页 | 参照 | LLM 输出 | 字 |", "|---|---|---|---|---|"]
        for r in nv:
            L.append(f"| {r['kind']} | {r['page']} | {r['ref'][:40] or '∅'} | {r['pred'][:40] or '∅'} | {r['cost']} |")
    L += ["", "### 3b. 对草稿（不看参照）：数字序列变动、删掉的字、新增的字", "", "| 模型 | 被标记段数 | 数字序列变动段（含仅重排） | 有删字的段（字数） | 有新增字的段（字数） |", "|---|---|---|---|---|"]
    for n in models:
        c = chk[n]
        L.append(f"| {n} | {len(c)} | {sum(1 for r in c if r['digits_changed'])} | {sum(1 for r in c if r['deleted_chars'])}（{sum(len(r['deleted_chars']) for r in c)}） | {sum(1 for r in c if r['added_chars'])}（{sum(len(r['added_chars']) for r in c)}） |")
    for n in models:
        rows = [r for r in chk[n] if r["digits_changed"] or r["deleted_chars"] or r["added_chars"]]
        if rows:
            L += ["", f"{n}：", "", "| id | 数字变 | 删掉 | 新增 | 草稿 → LLM（截 60 字） |", "|---|---|---|---|---|"]
            for r in rows:
                L.append(f"| {r['id']} | {'是' if r['digits_changed'] else ''} | {r['deleted_chars'][:30]} | {r['added_chars'][:30]} | {r['draft'][:60]} → {r['llm'][:60]} |".replace("\n", " "))
    if any(fb.values()):
        L += ["", "回退到草稿的段（无结果 / 解析失败 / 缺 id）：" + "；".join(f"{n} {len(v)} 段" for n, v in fb.items())]
    L += ["", "## 4. 调用、tokens 与费用", "", "| 模型 | 成功请求 | 失败尝试 | 输入 tokens（缓存） | 输出 tokens | 费用 | 墙钟（请求耗时和） | 估算费用 |", "|---|---|---|---|---|---|---|---|"]
    for n in models:
        ok = [r for r in led if r["model_key"] == n and r.get("http") == 200]
        bad_ = [r for r in led if r["model_key"] == n and r.get("http") != 200]
        L.append(f"| {n}（{ok[0]['served_model'] if ok else ''}） | {len(ok)} | {len(bad_)} | {sum(r['prompt_tokens'] for r in ok)}（{sum(r['cached_tokens'] for r in ok)}） | {sum(r['completion_tokens'] for r in ok)} | "
                 f"${sum(r.get('usd') or 0 for r in led if r['model_key'] == n):.4f} | {sum(r.get('sec') or 0 for r in led if r['model_key'] == n):.1f}s | ${est['models'].get(n, {}).get('est_usd', 0):.4f} |")
    L.append(f"| **合计** | | | | | **${sum(r.get('usd') or 0 for r in led):.4f}** | | ${est['est_usd_total']:.4f}（上限 ${est['cap_usd']}） |")
    L += ["", "费用按 API 返回的 usage × 官方价计算（OpenAI：gpt-4o-mini $0.15/$0.60、gpt-4o $2.50/$10 每百万输入/输出；DeepSeek deepseek-flash 峰时 $0.30/$1.20、谷时半价，按调用时刻判定；Qwen qwen-plus 华北2 非思考 ≤128K：输入 0.8 / 输出 2 元/百万 tokens；Ernie ernie-5.1 输入≤32K：输入 4 / 输出 18 元/百万 tokens。人民币价按配置 cny_per_usd=7.2 换算美元）。",
          "", "## 5. 约束", "", "- 外发只有：B 原始输出文字（按页，含第 18 页合成身份证页的 B 文字）+ 冻结提示词；未发 PDF、图片、参照、manifest、synth18-textlayer.pdf。",
          "- ledger / 日志只记 token、费用、耗时、HTTP 状态，不记 key；错误文本写盘前把 key 替换为 ***。",
          "- 未改 frozen20、synth18/、`/workspace/pdf-test/ocr/config.yaml`；默认 `config.yaml` 的 allow_external 仍为 false；外发只经 `config_llm_synth18_live.yaml`。",
          "- 千问/文心密钥只从配置的本地 600 文件或环境变量在进程内读取，不复制、不打印、不写日志。"]
    Path(path).write_text("\n".join(L) + "\n", encoding="utf-8")


def score(args, root=OUT, models=None, note=""):
    models = models or [m for m in MODELS if load_results(m, root)]
    draft, chs = chunks()
    systems = {"B": draft}
    fb, chk = {}, {}
    for m in models:
        res = load_results(m, root)
        systems[m], fb[m] = build_pred(draft, chs, res)
        chk[m] = draft_checks(chs, res)
    R = score_all(systems, root)
    led = [json.loads(l) for l in (root / "ledger.jsonl").read_text().splitlines() if l.strip()] if (root / "ledger.jsonl").exists() else []
    est = jl(OUT / "estimate.json")
    wj(root / "scores.json", {n: {"summary": R[n]["summary"], "cats": R[n]["cats"], "new_vs_ref": R[n].get("new_vs_ref")} for n in R})
    wj(root / "draft_checks.json", chk)
    wj(root / "diff_vs_ref.json", {n: R[n]["rows"] for n in R})
    wj(root / "pred_pages.json", systems)
    write_report(R, chk, fb, est, led, models, root / "report.md", note)
    for n in R:
        x = R[n]["summary"]["full"]["overall"]
        print(n, x, R[n]["cats"], "new:", Counter(r["kind"] for r in R[n].get("new_vs_ref") or []))
    return R


def selftest(args):
    st = OUT / "selftest"
    import shutil
    shutil.rmtree(st, ignore_errors=True)
    _, chs = chunks()
    for m, mut in (("identity", None), ("broken", "x")):
        for ch in chs:
            segs = {s["id"]: s["text"] for s in ch["segments"]}
            if mut and ch["page"] == 2 and segs:
                ks = list(segs)
                kd = next(k for k in ks if re.search(r"\d", segs[k]))
                segs[kd] = re.sub(r"\d", "9" if "9" not in segs[kd][:1] else "8", segs[kd], count=1)
                kn = [k for k in ks if not re.search(r"\d", segs[k]) and len(segs[k]) > 6]
                segs[kn[0]] = segs[kn[0]][: len(segs[kn[0]]) // 2]
                segs[kn[1]] = segs[kn[1]] + "此处为编造内容"
            wj(st / "results" / m / f"p{ch['page']:02d}.json", {"page": ch["page"], "segments": segs})
    R = score(args, st, ["identity", "broken"], "（自检：假结果，未联网）")
    a, b = R["identity"]["summary"]["full"]["overall"], R["B"]["summary"]["full"]["overall"]
    assert (a["readout"], a["cer"]) == (b["readout"], b["cer"]) == B_BASE, (a, b)
    kinds = Counter(r["kind"] for r in R["broken"]["new_vs_ref"])
    assert kinds["改数字/金额/日期"] and kinds["删除"] and kinds["编造/插入"], kinds
    # 密钥读取：只用临时文件，绝不读取或打印真实密钥
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fp = Path(td) / "keys.env"
        fp.write_text("QWEN_API_KEY=fake-qwen-key-for-selftest\nERNIE_API_KEY=fake-ernie-key-for-selftest\n", encoding="utf-8")
        fp.chmod(0o600)
        k, info = load_api_key({"api_key_env": "QWEN_API_KEY", "api_key_file": str(fp)})
        assert k == "fake-qwen-key-for-selftest" and info["present"] and info["length"] == 26 and info["source"] == "file"
        assert redact("token=fake-qwen-key-for-selftest", k) == "token=***"
        k2, info2 = load_api_key({"api_key_env": "ERNIE_API_KEY", "api_key_file": str(fp)})
        assert k2 == "fake-ernie-key-for-selftest" and info2["length"] == 27
        missing, info3 = load_api_key({"api_key_env": "MISSING_KEY_XYZ", "api_key_file": ""})
        assert missing is None and not info3["present"]
    # cap：已花 0.1574 + 最坏不超过 2
    spent = already_spent()
    assert abs(spent - 0.157394) < 1e-5 or spent >= 0
    cfg = load_cfg()
    assert cfg["hard_cap_usd"] == 2.0
    # 五列表头写入自检报告
    rpt = (st / "report.md").read_text(encoding="utf-8")
    assert "五列对照" in rpt and "Qwen" in rpt and "Ernie" in rpt
    print("selftest OK", kinds)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("prepare")
    r = sp.add_parser("run")
    r.add_argument("--live", action="store_true")
    r.add_argument("--models", default="")
    sp.add_parser("score")
    sp.add_parser("selftest")
    a = ap.parse_args()
    {"prepare": prepare, "run": run, "score": score, "selftest": selftest}[a.cmd](a)
