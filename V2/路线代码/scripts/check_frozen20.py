#!/usr/bin/env python3
"""校验 /workspace/pdf-test/frozen20/ 中的 PDF 与 ChatGPT 参照 SHA256。"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from baidu_ocr.config import load_config
from baidu_ocr.frozen import check_frozen20


def main() -> int:
    cfg = load_config()
    result = check_frozen20(cfg)
    print(json.dumps({k: v for k, v in result.items() if k != "manifest" or result["manifest"]}, ensure_ascii=False, indent=2))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
