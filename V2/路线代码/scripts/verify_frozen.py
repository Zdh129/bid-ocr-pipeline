#!/usr/bin/env python3
"""核对 rules/ 中冻结文件的 sha256。"""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.verify_frozen import main
import argparse
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="核对冻结规则 sha256")
    ap.add_argument("--round", dest="round_name", default="all", choices=["all", "baseline_c", "round1", "round2"])
    raise SystemExit(main(ap.parse_args()))
