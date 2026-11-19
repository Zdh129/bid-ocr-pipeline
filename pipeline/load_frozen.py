"""在内存中加载 rules/ 下的冻结模块，不改磁盘上的冻结文件。

冻结脚本里写死的原试验目录路径会在加载后被改写成当前仓库路径。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from .paths import REPO_ROOT, prepare_sys_path, remap_lab_path

RULES = {
    "baseline_c": REPO_ROOT / "rules" / "baseline_c",
    "round1": REPO_ROOT / "rules" / "round1",
    "round2": REPO_ROOT / "rules" / "round2",
    "round3": REPO_ROOT / "rules" / "round3",
}


def _patch_lab_paths(mod: ModuleType) -> None:
    for name, val in list(vars(mod).items()):
        if isinstance(val, Path):
            setattr(mod, name, remap_lab_path(val))
        elif isinstance(val, str) and val.startswith("/workspace/pdf-test/"):
            setattr(mod, name, str(remap_lab_path(val)))


def load_frozen_module(name: str, path: Path, package_hint: str | None = None) -> ModuleType:
    prepare_sys_path(path.parent)
    # 避免与其他轮次的同名模块冲突
    mod_name = package_hint or f"_frozen_{path.parent.name}_{name}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    _patch_lab_paths(mod)
    return mod


def load_round(round_name: str) -> dict[str, ModuleType]:
    """加载某一轮冻结规则用到的核心模块。"""
    if round_name not in RULES:
        raise KeyError(f"未知轮次: {round_name}")
    prepare_sys_path(RULES[round_name], RULES["baseline_c"])
    out: dict[str, ModuleType] = {}
    if round_name == "baseline_c":
        out["variants_blind"] = load_frozen_module(
            "variants_blind", RULES["baseline_c"] / "variants_blind.py"
        )
        return out
    d = RULES[round_name]
    out["core"] = load_frozen_module("core", d / "core.py", f"_frozen_{round_name}_core")
    # core 在 import 时已把 variants_blind 等装进 sys.modules；再补丁派生路径
    _patch_lab_paths(out["core"])
    if hasattr(out["core"], "FZ"):
        out["core"].FZ = RULES["baseline_c"]
    if hasattr(out["core"], "LN") and hasattr(out["core"].LN, "D"):
        # 表格线缓存改到 output/，不写进冻结目录
        out["core"].LN.D = REPO_ROOT / "output" / "表格线缓存"
    if round_name == "round1":
        out["frozen_stack"] = load_frozen_module(
            "frozen_stack", d / "frozen_stack.py", "_frozen_round1_stack"
        )
    elif round_name == "round2":
        out["frozen_stack2"] = load_frozen_module(
            "frozen_stack2", d / "frozen_stack2.py", "_frozen_round2_stack"
        )
        out["lp2"] = load_frozen_module("lp2", d / "lp2.py", "_frozen_round2_lp2")
    else:
        # 第3轮：frozen_stack3 在本目录内 import lp3 / final_stack3 / head_fix（F4、F5）
        out["frozen_stack3"] = load_frozen_module(
            "frozen_stack3", d / "frozen_stack3.py", "_frozen_round3_stack"
        )
        out["lp3"] = sys.modules.get("lp3") or load_frozen_module("lp3", d / "lp3.py", "_frozen_round3_lp3")
    return out
