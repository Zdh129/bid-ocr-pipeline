#!/usr/bin/env python3
"""检查环境：只报告 Key 环境变量是否已设置，不打印值。"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from baidu_ocr.auth import key_status
from baidu_ocr.config import load_config
from baidu_ocr.frozen import check_frozen20


def main() -> int:
    cfg = load_config()
    st = key_status(cfg)
    fr = check_frozen20(cfg)
    print("config:", cfg["_config_path"])
    print("allow_external:", st["allow_external"])
    print(f"{st['api_key_env']}:", "set" if st["api_key_set"] else "missing")
    print(f"{st['secret_key_env']}:", "set" if st["secret_key_set"] else "missing")
    print("frozen20 ready:", fr["ready"])
    if fr["errors"]:
        for e in fr["errors"]:
            print("frozen20:", e)
    if st["allow_external"]:
        print("警告: allow_external=true，只有在用户本人批准后才应出现。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
