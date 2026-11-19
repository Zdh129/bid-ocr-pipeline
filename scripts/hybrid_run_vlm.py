#!/usr/bin/env python3
"""按待发清单调用 OpenAI 视觉模型。默认 dry-run（不联网、不读 key）。

live 需要同时：--live、--config 指向的配置里 vlm.allow_external: true、环境变量 OPENAI_API_KEY 存在。
例：python scripts/hybrid_run_vlm.py --config config_hybrid_live.yaml --route route1 --live
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from baidu_ocr.hybrid.vlm import Runner, live_gate  # noqa: E402

OUT = ROOT / "output" / "hybrid"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config_hybrid.yaml"))
    ap.add_argument("--route", choices=["route1", "route2", "both"], default="both")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="live", action="store_false", default=False, help="默认：只列出将发送的内容")
    g.add_argument("--live", dest="live", action="store_true", help="真正调用 OpenAI（需配置开关与 key）")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-cache", action="store_true", help="两路线相同裁图不复用结果")
    ap.add_argument("--only", nargs="*", default=None, help="只跑这些 item id（调试用）")
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    if args.live:
        why = live_gate(cfg, True)
        if why:
            raise SystemExit("拒绝外发：" + "；".join(why))
    routes = ["route1", "route2"] if args.route == "both" else [args.route]
    runner = Runner(ROOT, OUT, cfg, args.live)
    for route in routes:
        man = json.loads((OUT / f"manifest_{route}.json").read_text(encoding="utf-8"))
        items = man["items"]
        if args.only:
            items = [i for i in items if i["id"] in set(args.only)]
        recs = runner.run(items, workers=args.workers, use_cache=not args.no_cache)
        if not args.live:
            p = OUT / f"dryrun_{route}.json"
            p.write_text(json.dumps({"route": route, "dry_run": True, "n": len(recs), "est_usd": round(sum(r["est_usd"] for r in recs), 4),
                                     "by_model": dict(collections.Counter(r["model"] for r in recs)), "items": recs}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[dry-run] {route}: {len(recs)} 块，预估 ${sum(r['est_usd'] for r in recs):.4f}，未发送任何请求 → {p.relative_to(ROOT)}")
        else:
            ok = sum(1 for r in recs if r.get("ok"))
            usd = sum(float(r.get("usd") or 0) for r in recs if r.get("ok") and not r.get("cache_hit"))
            print(f"[live] {route}: ok {ok}/{len(recs)}，新增费用 ${usd:.4f}，累计 ${runner.spent:.4f}" + (f"；{runner.stopped}" if runner.stopped else ""))
            if runner.stopped:
                break


if __name__ == "__main__":
    main()
