#!/usr/bin/env python3
"""blind20 冻结推荐组合：表格段发深度求索纠错（冻结后新写的调用脚本；只调用冻结代码，提示词 prompt_v1 按冻结清单 sha 校验）。

  [无参数]   预演：列出要发的表格段规模与最坏费用，不联网
  --live     实发（需 config_llm_blind20_live.yaml allow_external=true；blind20 深度求索累计上限 $1，按最坏情况预检）
只发表格文字（流水线到 LLM 之前的表格段，与冻结流水线同一份输入）；不发图片和正文；不用 gpt-4o、6.2。日志不写密钥。
缓存：output/blind20_frozen/blind20/llm/cache/<sha256(模型+段落 json)[:20]>.json（冻结流水线按这个键读取）。
"""
from __future__ import annotations

import argparse
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
import blind20_frozen_run as R  # noqa: E402

CFG = ROOT / "config_llm_blind20_live.yaml"
LDIR = R.OUT / "blind20" / "llm"
CAP = 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    import requests
    fz = R.verify()
    prompt_p = ROOT / "output" / "synth18_llm" / "prompt_v1.txt"
    assert R.sha(prompt_p) == fz["prompt_sha256"], "prompt_v1 与冻结清单不符"
    sysp = prompt_p.read_text(encoding="utf-8")
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))["llm"]
    mc = cfg["models"]["deepseek"]
    F, c = R.ctx()
    pr, _, _ = c.base(R.RECOMMENDED)
    led_p = LDIR / "ledger.jsonl"
    spent = sum(float(json.loads(l).get("usd") or 0) for l in led_p.read_text().splitlines() if l.strip()) if led_p.exists() else 0.0
    todo = []
    for ch in F.tables_of(pr):
        k = F.chunk_key("deepseek", ch)
        if (LDIR / "cache" / f"{k}.json").exists():
            continue
        um = json.dumps({"page": ch["page"], "segments": ch["segments"]}, ensure_ascii=False)
        exp = json.dumps({"segments": [{"id": s["id"], "text": s["text"]} for s in ch["segments"]]}, ensure_ascii=False)
        pt, ct = R.ds_tokens(sysp) + R.ds_tokens(um) + 10, math.ceil(R.ds_tokens(exp) * 1.1)
        mo = int(min(16384, max(1024, ct * 2 + 256)))
        pk = mc["price_usd_per_1m_peak"]
        todo.append({"key": k, "chunk": ch, "user": um, "mo": mo, "worst": (pt * pk["input"] + mo * pk["output"]) / 1e6})
    worst = sum(x["worst"] for x in todo)
    print(json.dumps({"to_call": len(todo), "pages": [x["chunk"]["page"] for x in todo], "worst_usd": round(worst, 5), "spent_before": round(spent, 6), "cap": CAP}, ensure_ascii=False))
    if spent + worst > CAP:
        sys.exit("最坏情况超过 $1 上限，停止")
    if not a.live:
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


if __name__ == "__main__":
    main()
