#!/usr/bin/env python3
"""synth18d：表格段发深度求索纠错（由 scripts/blind20_frozen_llm.py 改写；只调用冻结代码，prompt_v1 按冻结清单 sha 校验）。
要发的表格段 = 推荐组合（含纠错）及两个去掉单项版本（去掉变体一乙／变体三）在深度求索之前的表格段，按内容去重。
只发表格文字；不发图片和正文；不用 gpt-4o、6.2。上限：synth18d 深度求索累计 $1（按最坏情况预检）。日志不写密钥。
缓存：output/synth18d/synth18d/llm/cache/<sha256(模型+段落 json)[:20]>.json（冻结流水线按这个键读取）。
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import synth18d_run as R  # noqa: E402
import synth18d_score as SC  # noqa: E402

LDIR = R.OUT / "synth18d" / "llm"
CAP = 1.0
PROMPT_SHA = "86ae3d1c77ba3276cc209077ec04166ae878031a09fc285057c5b345430d231a"


def ds_tokens(t):
    import unicodedata
    cjk = sum(1 for ch in t if unicodedata.east_asian_width(ch) in ("W", "F"))
    return math.ceil(cjk * 0.6 + (len(t) - cjk) * 0.3)


def main(live=False):
    import requests
    R.verify()
    prompt_p = ROOT / "output" / "synth18_llm" / "prompt_v1.txt"
    assert R.sha(prompt_p) == PROMPT_SHA == R.jl(R.FROZEN_REC)["prompt_sha256"], "prompt_v1 与冻结不符"
    sysp = prompt_p.read_text(encoding="utf-8")
    cfg = yaml.safe_load(R.LLM_CFG.read_text(encoding="utf-8"))["llm"]
    assert float(cfg["hard_cap_usd"]) == CAP
    mc = cfg["models"]["deepseek"]
    F, c = SC.ctx()
    led_p = LDIR / "ledger.jsonl"
    spent = sum(float(json.loads(l).get("usd") or 0) for l in led_p.read_text().splitlines() if l.strip()) if led_p.exists() else 0.0
    todo, seen, scale = [], set(), {"pages": set(), "segments": 0, "chars": 0}
    for v in SC.LLM_VARIANTS:
        pr, _, _ = c.base(v)
        for ch in F.tables_of(pr):
            k = F.chunk_key("deepseek", ch)
            if k in seen:
                continue
            seen.add(k)
            scale["pages"].add(ch["page"])
            scale["segments"] += len(ch["segments"])
            scale["chars"] += sum(len(s["text"]) for s in ch["segments"])
            if (LDIR / "cache" / f"{k}.json").exists():
                continue
            um = json.dumps({"page": ch["page"], "segments": ch["segments"]}, ensure_ascii=False)
            exp = json.dumps({"segments": [{"id": s["id"], "text": s["text"]} for s in ch["segments"]]}, ensure_ascii=False)
            pt, ct = ds_tokens(sysp) + ds_tokens(um) + 10, math.ceil(ds_tokens(exp) * 1.1)
            mo = int(min(16384, max(1024, ct * 2 + 256)))
            pk = mc["price_usd_per_1m_peak"]
            todo.append({"key": k, "chunk": ch, "user": um, "mo": mo, "worst": (pt * pk["input"] + mo * pk["output"]) / 1e6})
    worst = sum(x["worst"] for x in todo)
    summ = {"unique_chunks": len(seen), "pages": sorted(scale["pages"]), "segments": scale["segments"], "chars": scale["chars"],
            "to_call": len(todo), "worst_usd": round(worst, 5), "spent_before": round(spent, 6), "cap": CAP}
    R.wj(LDIR / "规模.json", summ)
    print(json.dumps(summ, ensure_ascii=False), flush=True)
    if spent + worst > CAP:
        sys.exit("最坏情况超过 $1 上限，停止")
    if not live:
        return
    assert cfg["allow_external"] is True and cfg.get("approval")
    key = os.environ.get(mc["api_key_env"])
    assert key, "缺深度求索密钥环境变量"
    LDIR.mkdir(parents=True, exist_ok=True)
    for x in todo:
        body = {"model": mc["model"], "temperature": cfg["temperature"], "max_tokens": x["mo"],
                "messages": [{"role": "system", "content": sysp}, {"role": "user", "content": x["user"]}], "response_format": {"type": "json_object"}}
        body.update(mc.get("extra_body") or {})
        for att in range(3):
            when = datetime.now(timezone.utc)
            rec = {"at": when.isoformat(timespec="seconds"), "model": "deepseek", "key": x["key"], "page": x["chunk"]["page"], "attempt": att + 1}
            try:
                resp = requests.post(f"{mc['base_url']}/chat/completions", json=body, timeout=180,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            except requests.RequestException as e:
                rec.update({"error": type(e).__name__, "usd": 0.0})
                with led_p.open("a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                time.sleep(3)
                continue
            if resp.status_code != 200:
                rec.update({"http": resp.status_code, "error": resp.text[:200].replace(key, "***"), "usd": 0.0})
                with led_p.open("a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                time.sleep(3)
                continue
            j = resp.json()
            u = j.get("usage") or {}
            pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
            cached = int(u.get("prompt_cache_hit_tokens") or 0)
            peak = when.weekday() < 5 and any(s <= when.hour < e for s, e in mc["peak_hours_utc"])
            p_ = mc["price_usd_per_1m_peak" if peak else "price_usd_per_1m_offpeak"]
            usd = round(((pt - cached) * p_["input"] + cached * p_["cached_input"] + ct * p_["output"]) / 1e6, 6)
            txt = j["choices"][0]["message"].get("content") or ""
            try:
                o = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip()))
                segs = {s["id"]: s["text"] for s in o["segments"] if isinstance(s, dict) and isinstance(s.get("text"), str)}
                ok = True
            except Exception:  # noqa: BLE001
                segs, ok = {}, False
            rec.update({"http": 200, "prompt_tokens": pt, "completion_tokens": ct, "usd": usd, "parse_ok": ok,
                        "missing_ids": [s["id"] for s in x["chunk"]["segments"] if s["id"] not in segs]})
            with led_p.open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            R.wj(LDIR / "cache" / f"{x['key']}.json", {"model": "deepseek", "page": x["chunk"]["page"], "segments": segs, "raw": txt,
                                                       "draft": {s["id"]: s["text"] for s in x["chunk"]["segments"]}, "usage": u, "usd": usd})
            print(f"p{x['chunk']['page']:02d}", pt, ct, f"${usd:.5f}", flush=True)
            break
