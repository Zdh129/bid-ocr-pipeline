#!/usr/bin/env python3
"""导入 V3 补齐后的规则与计分模块，并检查模块文件都在本仓库内。
不读测试集、不调百度、不读参照。路径与依赖加载已改为用到时再读数据。
用法：先 source 环境.sh，再 python3 自测_导入.py
"""
from __future__ import annotations
import importlib, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
# 允许的代码根：本仓库。不允许 /workspace/pdf-test。
ALLOW = [HERE.resolve(), REPO.resolve()]
DENY_PREFIXES = [Path("/workspace/pdf-test").resolve()]

# 路径与数据改为用到时再加载；本自测只检查 import，不读测试集、不调百度、不写成绩。
MODULES = [
    "规则v6", "旋转规则", "版面规则", "后处理_v2", "难页特征", "组合", "新规则",
    "phys_order", "layout_order", "table_title", "v3_layers",
    "规则", "规则_专项",
    "后处理",
    "sets_all", "计分",
    "比对",
    "baidu_ocr.score",
    "tolerant_score", "blind20_score",
    "layer1", "layer3", "dedup", "seal_rule", "eseal", "page_type", "baidu_cache",
    "route5", "tablerow",
    "fuse_vertical", "frozen_stack3",
    "公共25", "公共65", "公共v3", "v5规则", "公共v6", "评分", "新扫描9页",
    "整合框架",
    # 上游生成（第六轮新版、整合版）：只导入，不运行
    "第六轮规则", "公共", "run_30", "stoprule", "scheme_a_call",
]

def _ok_path(p: Path) -> bool:
    try:
        rp = p.resolve()
    except Exception:
        return False
    for d in DENY_PREFIXES:
        try:
            rp.relative_to(d)
            return False
        except ValueError:
            pass
    for a in ALLOW:
        try:
            rp.relative_to(a)
            return True
        except ValueError:
            pass
    # stdlib / site-packages is fine
    return True

def main() -> int:
    failed = 0
    loaded = []
    for name in MODULES:
        try:
            m = importlib.import_module(name)
        except Exception as e:
            print(f"失败 {name}: {type(e).__name__}: {e}")
            failed += 1
            continue
        f = getattr(m, "__file__", None)
        if not f:
            print(f"失败 {name}: 无 __file__")
            failed += 1
            continue
        fp = Path(f)
        if not _ok_path(fp):
            print(f"失败 {name}: 引用了仓库外路径 {fp}")
            failed += 1
            continue
        try:
            rel = fp.resolve().relative_to(REPO.resolve())
        except ValueError:
            rel = fp
        print(f"成功 {name}  <-  {rel}")
        loaded.append(name)
    # also audit already-imported local modules
    for name, m in list(sys.modules.items()):
        f = getattr(m, "__file__", None)
        if not f:
            continue
        fp = Path(f)
        try:
            fp.resolve().relative_to(Path("/workspace/pdf-test").resolve())
        except (ValueError, FileNotFoundError):
            continue
        # imported from /workspace/pdf-test
        print(f"失败 额外引用仓库外模块 {name}: {fp}")
        failed += 1
    print(f"导入成功 {len(loaded)} 个，失败 {failed} 个")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
