#!/usr/bin/env python3
"""读取两方案的计分与费用，生成并排对比报告。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from baidu_ocr.report import build_report, write_report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--score-a", required=True)
    ap.add_argument("--score-b", required=True)
    ap.add_argument("--cost-a", default="")
    ap.add_argument("--cost-b", default="")
    ap.add_argument("--edition-b", default="文档解析（标准版）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--frozen-ready", action="store_true")
    args = ap.parse_args()

    sa = json.loads(Path(args.score_a).read_text(encoding="utf-8"))
    sb = json.loads(Path(args.score_b).read_text(encoding="utf-8"))
    ca = json.loads(Path(args.cost_a).read_text(encoding="utf-8")) if args.cost_a else {}
    cb = json.loads(Path(args.cost_b).read_text(encoding="utf-8")) if args.cost_b else {}
    report = build_report(
        scheme_a={"scores": sa, "cost": ca, "apis": "text_layer / general / accurate"},
        scheme_b={"scores": sb, "cost": cb, "edition": args.edition_b},
        frozen20_ready=args.frozen_ready,
    )
    md, js = write_report(report, args.out)
    print(f"wrote {md} and {js}")
    print("winner:", report["winner"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
