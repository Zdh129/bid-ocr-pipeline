# -*- coding: utf-8 -*-
"""仓库副本的路径与依赖加载辅助。不含识别逻辑、规则或计分公式。

路径一律从环境变量读取，或解析为相对本仓库的路径。
正式运行缺少必要输入、缓存、参照或清单时抛出 MissingInputError（中文说明缺什么、该设哪个环境变量）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_BAIDU = "OCR_BAIDU_ROOT"
ENV_PDF_TEST = "OCR_PDF_TEST_ROOT"
ENV_LAB_CHECKS = "OCR_LAB_CHECKS"
ENV_VERTICAL_MARGIN = "OCR_VERTICAL_MARGIN"
ENV_REPO = "REPO_ROOT"
ENV_V3 = "V3_ROOT"
ENV_V2 = "V2_ROOT"

_PLACEHOLDER_BAIDU = Path("__OCR_BAIDU_ROOT_未设置__")
_PLACEHOLDER_PDF = Path("__OCR_PDF_TEST_ROOT_未设置__")


class MissingInputError(RuntimeError):
    """正式运行缺少必要输入、缓存、参照或清单。"""


def repo_root() -> Path:
    raw = os.environ.get(ENV_REPO, "").strip()
    if raw:
        return Path(raw)
    here = Path(__file__).resolve()
    return here.parents[1]


def v3_root() -> Path:
    raw = os.environ.get(ENV_V3, "").strip()
    if raw:
        return Path(raw)
    p = repo_root() / "V3"
    if p.is_dir():
        return p
    raise MissingInputError(
        f"未设置环境变量 {ENV_V3}，也找不到仓库内的 V3/ 目录。请先 source V3/环境.sh，或设置 {ENV_V3}。"
    )


def v2_root() -> Path:
    raw = os.environ.get(ENV_V2, "").strip()
    if raw:
        return Path(raw)
    p = repo_root() / "V2"
    if p.is_dir():
        return p
    raise MissingInputError(
        f"未设置环境变量 {ENV_V2}，也找不到仓库内的 V2/ 目录。请先 source V2/环境.sh，或设置 {ENV_V2}。"
    )


def baidu_root(*, required: bool = False) -> Path | None:
    raw = os.environ.get(ENV_BAIDU, "").strip()
    if not raw:
        if required:
            raise MissingInputError(
                f"未设置环境变量 {ENV_BAIDU}，无法定位原试验机 baidu 工作根。"
                f"正式运行需要本地识别缓存、参照或清单，请设置 {ENV_BAIDU} 后重试。"
            )
        return None
    p = Path(raw)
    if required and not p.is_dir():
        raise MissingInputError(
            f"环境变量 {ENV_BAIDU} 指向的目录不存在：{p}。"
            f"请将 {ENV_BAIDU} 设为原试验机 baidu 工作根后重试。"
        )
    return p


def baidu_root_path() -> Path:
    """仅用于构造路径；未设置时返回占位路径，不在 import 时报错。"""
    return baidu_root(required=False) or _PLACEHOLDER_BAIDU


def pdf_test_root(*, required: bool = False) -> Path | None:
    raw = os.environ.get(ENV_PDF_TEST, "").strip()
    if not raw:
        if required:
            raise MissingInputError(
                f"未设置环境变量 {ENV_PDF_TEST}，无法定位原试验机 pdf-test 根目录。"
                f"请设置 {ENV_PDF_TEST} 后重试。"
            )
        return None
    p = Path(raw)
    if required and not p.is_dir():
        raise MissingInputError(
            f"环境变量 {ENV_PDF_TEST} 指向的目录不存在：{p}。"
            f"请将 {ENV_PDF_TEST} 设为原试验机 pdf-test 根目录后重试。"
        )
    return p


def pdf_test_root_path() -> Path:
    return pdf_test_root(required=False) or _PLACEHOLDER_PDF


def require_file(path: Path | str, what: str, env: str = ENV_BAIDU) -> Path:
    p = Path(path)
    if not p.is_file():
        raise MissingInputError(
            f"缺少{what}：{p}。请设置环境变量 {env} 指向相应根目录，并确认该文件存在后重试。"
        )
    return p


def require_dir(path: Path | str, what: str, env: str = ENV_BAIDU) -> Path:
    p = Path(path)
    if not p.is_dir():
        raise MissingInputError(
            f"缺少{what}：{p}。请设置环境变量 {env} 指向相应根目录，并确认该目录存在后重试。"
        )
    return p


def load_json(path: Path | str, what: str, env: str = ENV_BAIDU):
    import json
    p = require_file(path, what, env)
    return json.load(open(p, encoding="utf-8"))


def lab_checks_enabled() -> bool:
    v = os.environ.get(ENV_LAB_CHECKS, "").strip().lower()
    return v in ("1", "true", "yes", "on")


def skip_lab_checks_notice() -> None:
    print(
        f"已跳过原机整树冻结校验和断网检查（未设置 {ENV_LAB_CHECKS}=1）。"
        "仓库自身的 sha 自测仍会执行。若在原试验机上需要这些检查，请设置该环境变量后再跑。",
        flush=True,
    )


def find_vertical_margin() -> Path:
    raw = os.environ.get(ENV_VERTICAL_MARGIN, "").strip()
    if raw:
        p = Path(raw)
        if p.is_file():
            return p
        raise MissingInputError(
            f"环境变量 {ENV_VERTICAL_MARGIN} 指向的文件不存在：{p}。请改为 vertical_margin.py 的实际路径。"
        )
    candidates = [
        repo_root() / "src/baidu_ocr/postprocess/vertical_margin.py",
        repo_root() / "rules/baseline_c/基线C_方案E/src/baidu_ocr/postprocess/vertical_margin.py",
        repo_root() / "V2/路线代码/src/baidu_ocr/postprocess/vertical_margin.py",
        repo_root() / "V2/路线代码/rules/baseline_c/基线C_方案E/src/baidu_ocr/postprocess/vertical_margin.py",
    ]
    br = baidu_root(required=False)
    if br is not None:
        candidates.append(
            br / "output/盲测_版面约束/冻结规则/基线C_方案E/src/baidu_ocr/postprocess/vertical_margin.py"
        )
    for p in candidates:
        if p.is_file():
            return p
    raise MissingInputError(
        "缺少 vertical_margin.py。已在本仓库 src/ 与规则副本中查找，均未找到。"
        f"请设置环境变量 {ENV_VERTICAL_MARGIN} 指向该文件后重试。"
    )


def ensure_scripts_on_path() -> None:
    s = str(repo_root() / "scripts")
    if s not in sys.path:
        sys.path.insert(0, s)
