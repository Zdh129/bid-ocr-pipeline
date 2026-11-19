#!/usr/bin/env python3
"""第3轮冻结路线。纯本地，不调用百度。"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.__main__ import main
if __name__ == "__main__":
    raise SystemExit(main(["round3", *sys.argv[1:]]))
