#!/usr/bin/env python3
"""blind20 视觉复核：gpt-6.2-astra。

子命令：
  prepare   选块（路线 2 规则、阈值 0.75，不看参照）+ 印章小测 4 块 + 费用估算 → output/astra/manifest.json, estimate.json（不联网）
  run       --live 才外发：优先 Batch API；不可用或超 60 分钟则取消未完成部分，改走标准接口（已完成的不重发）。累计（含所有尝试）达 $5 即停。
  score     合并进「最优解」底稿（output/bestconfig/blind20-scan.json），计分、出报告 output/astra/report.md（不联网）
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from baidu_ocr.hybrid import astra  # noqa: E402
from baidu_ocr.hybrid.crops import prompt_for, prompt_hash, text_tokens  # noqa: E402
from baidu_ocr.hybrid.rules import lowconf_only_inactive  # noqa: E402

OUT = ROOT / "output" / "astra"
MAN = ROOT / "output" / "hybrid" / "manifest_route2.json"
THR = 0.75
SEAL_PICK = (0, 7, 14, 21)  # 印章小测：按 id 排序后等距取 4 块（不看参照）
API = "https://api.openai.com/v1"


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def wj(p, o):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def category(i: dict) -> str:
    if i["kind"] == "table_band":
        return "table"
    if i["kind"] == "missing_line":
        return "missing"
    if i["kind"] == "seal":
        return "seal"
    return "mismatch" if any(r.startswith("ab_mismatch") for r in i["reasons"]) else "lowconf"


def load_cfg(path):
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))["vlm"]


# ------------------------------------------------------------------ prepare
def prepare(args):
    cfg = load_cfg(args.config)
    man = jl(MAN)
    main = [i for i in man["items"] if i["kind"] != "seal" and not lowconf_only_inactive(i, THR)]
    seals = sorted([i for i in man["items"] if i["kind"] == "seal"], key=lambda i: i["id"])
    seal_test = [seals[k] for k in SEAL_PICK if k < len(seals)]
    items = []
    for i in main + seal_test:
        crop = ROOT / i["crop_path"]
        b = crop.read_bytes()
        assert hashlib.sha256(b).hexdigest() == i["sha256"], i["id"]
        from PIL import Image
        w, h = Image.open(io.BytesIO(b)).size
        assert max(w, h) <= 1600 and (w, h) == (i["crop_w"], i["crop_h"]), i["id"]
        pk, prompt = prompt_for(i)
        img = astra.image_tokens(w, h, cfg["detail"])
        p_tok = img + text_tokens(prompt) + 12
        exp = int((i.get("context") or {}).get("expected_chars") or 0)
        n_cells = int((i.get("context") or {}).get("n_cells") or 0)
        out_text = math.ceil(exp * 1.15 + n_cells * 2 + 20) if i["kind"] == "table_band" else math.ceil(exp * 1.15 + 10)
        c_tok = out_text + 400  # reasoning_effort=low 的推理 token：未知，按 400 保守估
        items.append({**i, "model": cfg["model"], "detail": cfg["detail"], "category": category(i), "test": "seal_small" if i["kind"] == "seal" else "main",
                      "prompt_kind": pk, "prompt_sha12": prompt_hash(prompt), "est_image_tokens": img, "est_prompt_tokens": p_tok,
                      "est_completion_tokens": c_tok, "est_usd_batch": astra.usd(p_tok, c_tok, 0, "batch"),
                      "est_usd_standard": astra.usd(p_tok, c_tok, 0, "standard")})
    from collections import Counter
    P = sum(i["est_prompt_tokens"] for i in items)
    C = sum(i["est_completion_tokens"] for i in items)
    est = {"created_at": now(), "model": cfg["model"], "detail": cfg["detail"], "reasoning_effort": cfg["reasoning_effort"],
           "n_requests": len(items), "by_category": dict(Counter(i["category"] for i in items)),
           "est_prompt_tokens": P, "est_image_tokens": sum(i["est_image_tokens"] for i in items), "est_completion_tokens": C,
           "est_usd_batch": astra.usd(P, C, 0, "batch"), "est_usd_standard": astra.usd(P, C, 0, "standard"),
           "worst_case_usd_batch": astra.worst_case_usd(len(items), P, cfg["max_completion_tokens"], "batch"),
           "worst_case_usd_standard": astra.worst_case_usd(len(items), P, cfg["max_completion_tokens"], "standard"),
           "max_completion_tokens": cfg["max_completion_tokens"], "cap_usd": cfg["hard_cap_usd"],
           "prices": astra.PRICES, "price_source": "OpenAI 官方定价页与模型页（2026-11-18 WebFetch 核实）",
           "image_token_rule": "32px 补丁数 × 1.2（按官方 vision 文档 gpt-6-astra 规则；gpt-6.2-astra 未单列，属推定）",
           "selection": f"路线 2 规则（{man['rules_version']}），离线阈值 {THR}：去掉只因 D3 低置信且 B score ≥{THR} 的行；不使用任何参照。印章小测：30 个印章区按 id 排序取第 {[k + 1 for k in SEAL_PICK]} 个。"}
    est["go"] = est["est_usd_standard"] <= cfg["hard_cap_usd"]
    wj(OUT / "manifest.json", {"source_manifest": str(MAN.relative_to(ROOT)), "items": items})
    wj(OUT / "estimate.json", est)
    print(json.dumps({k: v for k, v in est.items() if k not in ("prices",)}, ensure_ascii=False, indent=1))


# ------------------------------------------------------------------ run
class Ledger:
    def __init__(self, path: Path):
        self.path = path
        self.spent = 0.0
        if path.exists():
            for ln in path.read_text(encoding="utf-8").splitlines():
                r = json.loads(ln)
                self.spent += float(r.get("usd") or 0)

    def add(self, rec: dict):
        self.spent += float(rec.get("usd") or 0)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({**rec, "cum_usd": round(self.spent, 6), "at": now()}, ensure_ascii=False) + "\n")


def _req(method, path, key, **kw):
    import requests
    r = requests.request(method, API + path, headers={"Authorization": f"Bearer {key}"}, timeout=kw.pop("timeout", 120), **kw)
    return r


def save_result(item, text, usage, fin, mode, elapsed, extra=None):
    ok, why = astra.result_ok(item["kind"], text, fin)
    rec = {"item_id": item["id"], "page": item["page"], "kind": item["kind"], "category": item["category"], "test": item["test"],
           "model": item["model"], "detail": item["detail"], "mode": mode, "ok": ok, "error": why or None, "text": text, **usage,
           "usd": astra.usd(usage["prompt_tokens"], usage["completion_tokens"], usage["cached_tokens"], mode),
           "finish_reason": fin, "elapsed_sec": elapsed, "crop": item["crop_path"], "crop_sha256": item["sha256"],
           "prompt_sha12": item["prompt_sha12"], "bbox_pt": item["bbox_pt"], "created_at": now(), **(extra or {})}
    wj(OUT / "vlm" / "results" / f"{item['id']}.json", rec)
    return rec


def run(args):
    cfg = load_cfg(args.config)
    est = jl(OUT / "estimate.json")
    if not args.live:
        print("dry-run：未加 --live，不外发。估算：", est["est_usd_batch"], est["est_usd_standard"])
        return
    if cfg.get("allow_external") is not True:
        sys.exit("拒绝外发：配置 allow_external 不是 true")
    key = os.environ.get(cfg.get("api_key_env") or "OPENAI_API_KEY")
    if not key:
        sys.exit("拒绝外发：环境变量缺 key（不打印）")
    if not est["go"]:
        sys.exit(f"估算 ${est['est_usd_standard']} 超过上限 ${cfg['hard_cap_usd']}，不开跑")
    cap = float(cfg["hard_cap_usd"])
    items = jl(OUT / "manifest.json")["items"]
    by_id = {i["id"]: i for i in items}
    led = Ledger(OUT / "vlm" / "ledger.jsonl")
    done = {p.stem for p in (OUT / "vlm" / "results").glob("*.json") if jl(p).get("ok") is not None} if (OUT / "vlm" / "results").exists() else set()
    todo = [i for i in items if i["id"] not in done]
    state_p = OUT / "vlm" / "batch_state.json"
    state = jl(state_p) if state_p.exists() else {}
    t_start = time.time()
    # ---------- Batch ----------
    if todo and not args.standard_only and state.get("status") not in ("fallback",):
        worst = astra.worst_case_usd(len(todo), sum(i["est_prompt_tokens"] for i in todo), cfg["max_completion_tokens"], "batch")
        if led.spent + worst > cap:
            print(f"Batch 最坏情况 ${worst:.3f} + 已花 ${led.spent:.3f} > ${cap}，不提交 Batch")
        else:
            if not state.get("batch_id"):
                lines = []
                for i in todo:
                    _, prompt = prompt_for(i)
                    body = astra.build_body(cfg["model"], prompt, (ROOT / i["crop_path"]).read_bytes(), cfg["detail"], cfg["reasoning_effort"], cfg["max_completion_tokens"])
                    lines.append(astra.batch_line(i["id"], body))
                jsonl = ("\n".join(lines) + "\n").encode("utf-8")
                (OUT / "vlm").mkdir(parents=True, exist_ok=True)
                r = _req("POST", "/files", key, files={"file": ("astra_batch.jsonl", jsonl, "application/jsonl")}, data={"purpose": "batch"})
                if r.status_code != 200:
                    print("Batch 文件上传失败：", r.status_code, r.text[:300].replace(key, "***"))
                    state = {"status": "fallback", "why": f"files upload HTTP {r.status_code}"}
                else:
                    fid = r.json()["id"]
                    r = _req("POST", "/batches", key, json={"input_file_id": fid, "endpoint": "/v1/chat/completions", "completion_window": "24h",
                                                            "metadata": {"job": "blind20-astra-review"}})
                    if r.status_code != 200:
                        print("Batch 创建失败：", r.status_code, r.text[:300].replace(key, "***"))
                        state = {"status": "fallback", "why": f"batch create HTTP {r.status_code}", "input_file_id": fid}
                    else:
                        b = r.json()
                        state = {"status": "submitted", "batch_id": b["id"], "input_file_id": fid, "submitted_at": now(), "submitted_ts": time.time(),
                                 "n": len(todo), "bytes": len(jsonl)}
                wj(state_p, state)
            if state.get("batch_id") and state.get("status") == "submitted":
                deadline = state["submitted_ts"] + 60 * float(cfg["batch_timeout_min"])
                b = None
                while True:
                    r = _req("GET", f"/batches/{state['batch_id']}", key)
                    b = r.json()
                    st = b.get("status")
                    print(datetime.now().strftime("%H:%M:%S"), st, b.get("request_counts"), flush=True)
                    if st in ("completed", "failed", "expired", "cancelled"):
                        break
                    if time.time() > deadline and st not in ("cancelling",):
                        _req("POST", f"/batches/{state['batch_id']}/cancel", key)
                        state["cancel_requested_at"] = now()
                        wj(state_p, state)
                    time.sleep(20)
                state.update({"status": "finished:" + b.get("status"), "final": {k: b.get(k) for k in ("status", "request_counts", "output_file_id", "error_file_id", "errors",
                                                                                                      "created_at", "in_progress_at", "completed_at", "cancelled_at", "failed_at")},
                              "finished_at": now(), "wall_sec": round(time.time() - state["submitted_ts"], 1)})
                wj(state_p, state)
                for fkey in ("output_file_id", "error_file_id"):
                    fid = b.get(fkey)
                    if not fid:
                        continue
                    content = _req("GET", f"/files/{fid}/content", key).content
                    (OUT / "vlm" / f"batch_{fkey}.jsonl").write_bytes(content)
                    for ln in content.decode("utf-8").splitlines():
                        if not ln.strip():
                            continue
                        rec = json.loads(ln)
                        it = by_id.get(rec["custom_id"])
                        resp = rec.get("response") or {}
                        if it is None:
                            continue
                        if resp.get("status_code") == 200:
                            text, usage, fin = astra.parse_chat(resp["body"])
                            r2 = save_result(it, text, usage, fin, "batch", None, {"batch_id": state["batch_id"]})
                            led.add({"item_id": it["id"], "mode": "batch", "ok": r2["ok"], "usd": r2["usd"], "prompt_tokens": usage["prompt_tokens"],
                                     "completion_tokens": usage["completion_tokens"], "reasoning_tokens": usage["reasoning_tokens"]})
                        else:
                            err = json.dumps(rec.get("error") or resp.get("body"), ensure_ascii=False)[:300]
                            led.add({"item_id": it["id"], "mode": "batch", "ok": False, "usd": 0.0, "error": err})
    # ---------- 标准接口补跑 ----------
    done = {p.stem for p in (OUT / "vlm" / "results").glob("*.json") if jl(p).get("ok")} if (OUT / "vlm" / "results").exists() else set()
    todo = [i for i in items if i["id"] not in done]
    if args.no_fallback:
        todo = []
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import requests
    lock = threading.Lock()
    stop = {"why": ""}

    def one(i):
        worst_next = astra.usd(i["est_prompt_tokens"], cfg["max_completion_tokens"], 0, "standard")
        with lock:  # 预留最坏情况额度，保证并发下累计不超上限
            if stop["why"] or led.spent + reserved[0] + worst_next > cap:
                stop["why"] = stop["why"] or f"已花 ${led.spent:.4f}+在途预留 ${reserved[0]:.4f}，下一次最坏 ${worst_next:.4f} 会超 ${cap}"
                led.add({"item_id": i["id"], "mode": "standard", "ok": False, "usd": 0.0, "skipped": "cap"})
                return
            reserved[0] += worst_next
        try:
            _, prompt = prompt_for(i)
            body = astra.build_body(cfg["model"], prompt, (ROOT / i["crop_path"]).read_bytes(), cfg["detail"], cfg["reasoning_effort"], cfg["max_completion_tokens"])
            for att in range(1, int(cfg["max_retries"]) + 2):
                t0 = time.perf_counter()
                try:
                    r = requests.post(API + "/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=cfg["timeout_sec"])
                    if r.status_code != 200:
                        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                    text, usage, fin = astra.parse_chat(r.json())
                    rec = save_result(i, text, usage, fin, "standard", round(time.perf_counter() - t0, 2), {"attempt": att})
                    with lock:
                        led.add({"item_id": i["id"], "mode": "standard", "ok": rec["ok"], "usd": rec["usd"], "prompt_tokens": usage["prompt_tokens"],
                                 "completion_tokens": usage["completion_tokens"], "reasoning_tokens": usage["reasoning_tokens"], "attempt": att})
                    if rec["ok"] or rec.get("error", "").startswith("finish_reason=length"):
                        break
                except Exception as e:  # noqa: BLE001
                    with lock:
                        led.add({"item_id": i["id"], "mode": "standard", "ok": False, "usd": 0.0, "attempt": att, "error": str(e)[:200].replace(key, "***")})
                    time.sleep(2 * att)
        finally:
            with lock:
                reserved[0] -= worst_next

    reserved = [0.0]
    t_std = time.time()
    with ThreadPoolExecutor(max_workers=int(cfg.get("workers", 8))) as ex:
        list(ex.map(one, todo))
    if todo:
        wj(OUT / "vlm" / "standard_state.json", {"n": len(todo), "wall_sec": round(time.time() - t_std, 1), "stop": stop["why"], "finished_at": now()})
    print("累计费用 $%.4f，总墙钟 %.1f s" % (led.spent, time.time() - t_start))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prepare", "run", "score"])
    ap.add_argument("--config", default=str(ROOT / "config_astra_live.yaml"))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--standard-only", action="store_true")
    ap.add_argument("--no-fallback", action="store_true")
    a = ap.parse_args()
    if a.cmd == "prepare":
        prepare(a)
    elif a.cmd == "run":
        run(a)
    else:
        sys.path.insert(0, str(ROOT / "scripts"))
        import astra_score
        astra_score.main()
