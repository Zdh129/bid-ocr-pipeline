"""加载 YAML 配置。默认 allow_external=false，不允许被空值悄悄打开。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_NAME = "config.yaml"


def _deep_update(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_update(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    if path is None:
        path = Path(__file__).resolve().parents[2] / DEFAULT_CONFIG_NAME
    path = Path(path).resolve()
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if overrides:
        cfg = _deep_update(cfg, overrides)
    root = Path(cfg.get("project_root") or path.parent).resolve()
    cfg["project_root"] = str(root)
    cfg["_config_path"] = str(path)
    if "allow_external" not in cfg:
        cfg["allow_external"] = False
    cfg["allow_external"] = bool(cfg.get("allow_external"))

    render = cfg.setdefault("render", {})
    dpi = float(render.get("dpi") or 200)
    zoom = render.get("zoom")
    if zoom is None:
        zoom = dpi / 72.0
    render["dpi"] = dpi
    render["zoom"] = float(zoom)
    return cfg


def resolve_path(cfg: dict, *parts: str) -> Path:
    root = Path(cfg["project_root"])
    p = Path(*parts) if parts else root
    if not p.is_absolute():
        p = root / p
    return p


def get_nested(cfg: dict, *keys: str, default: Any = None) -> Any:
    cur: Any = cfg
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def allow_external(cfg: dict) -> bool:
    return bool(cfg.get("allow_external"))
