#!/usr/bin/env python3
"""导入 V3 补齐后的规则与计分模块，并检查模块文件都在本仓库内。
不读测试集、不调百度、不读参照。会跳过在 import 时就要读缓存或做网络/冻结校验的入口
（公共v3、公共v6、评分、整合框架、复跑_第五轮 等）。
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

# 这些模块在 import 时会读测试集、缓存，或做网络/整树冻结校验，完整复跑需要本地缓存。
# 本自测不导入它们。
SKIP_REASON = {
    "公共v3": "import 时加载测试集清单与缓存路径（冻结文件，未改）",
    "公共v6": "依赖 公共v3",
    "v5规则": "依赖 公共v3",
    "新扫描9页": "import 时读清单.json",
    "公共25": "import 时读测试集清单",
    "公共65": "import 时读已有预测",
    "整合框架": "import 时 exec 复跑_第五轮 头部（网络检查与原机冻结校验）",
    "公共": "依赖 方法/框架 的试验机数据挂钩",
    "方法": "import 时挂接开发集路径并依赖可选第三方库",
}

MODULES = [
    "规则v6", "旋转规则", "版面规则", "后处理_v2", "难页特征", "组合", "新规则",
    "phys_order", "layout_order", "table_title", "v3_layers",
    "规则", "规则_专项",
    "后处理",
    "sets_all", "计分",
    # 比对.py 在 import 时读取测试集 清单.json，那是数据，未入库，本自测不导入
    "baidu_ocr.score",
    "tolerant_score", "blind20_score",
    "layer1", "layer3", "dedup", "seal_rule", "eseal", "page_type", "baidu_cache",
    "route5", "tablerow",
    # frozen_stack3 会经 fuse_vertical.py 在 import 时按原机路径加载 vertical_margin.py；完整底座需要本地冻结规则缓存，本自测不导入
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
