#!/usr/bin/env python3
"""导入 V2 复跑所需、先前缺失的模块，并检查模块文件都在本仓库内。
不读测试集、不调百度。不导入会做网络检查或整树冻结校验的 复跑_第三版.py。
用法：先 source 环境.sh，再 python3 自测_导入.py
"""
from __future__ import annotations
import importlib, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DENY = Path("/workspace/pdf-test")

MODULES = [
    "sets_all", "计分",
    "vsets", "layer1", "layer3", "dedup", "seal_rule", "eseal", "page_type", "baidu_cache",
    "phys_order", "v3_layers",
    "tolerant_score", "blind20_score", "synth18b_score",
    "baidu_ocr.score",
    "route5", "tablerow",
]

def ok(p: Path) -> bool:
    rp = p.resolve()
    try:
        rp.relative_to(DENY.resolve())
        return False
    except (ValueError, FileNotFoundError):
        pass
    try:
        rp.relative_to(REPO.resolve())
        return True
    except ValueError:
        return True  # site-packages

def main() -> int:
    failed = 0
    n = 0
    for name in MODULES:
        try:
            m = importlib.import_module(name)
        except Exception as e:
            print(f"失败 {name}: {type(e).__name__}: {e}")
            failed += 1
            continue
        f = getattr(m, "__file__", None)
        if not f or not ok(Path(f)):
            print(f"失败 {name}: 引用了仓库外路径 {f}")
            failed += 1
            continue
        fp = Path(f)
        try:
            rel = fp.resolve().relative_to(REPO.resolve())
        except ValueError:
            rel = fp
        print(f"成功 {name}  <-  {rel}")
        n += 1
    for name, m in list(sys.modules.items()):
        f = getattr(m, "__file__", None)
        if not f:
            continue
        try:
            Path(f).resolve().relative_to(DENY.resolve())
        except (ValueError, FileNotFoundError):
            continue
        print(f"失败 额外引用仓库外模块 {name}: {f}")
        failed += 1
    print(f"导入成功 {n} 个，失败 {failed} 个")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
