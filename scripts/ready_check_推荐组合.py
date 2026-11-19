#!/usr/bin/env python3
"""就绪检查（以冻结清单 output/f1_round2/frozen_推荐组合.json 为准）。

原 scripts/synth18_ready_check.py 的第 5 项写死核对旧清单 output/blindset2_prep/frozen_config_v2.json（bestconfig_v2），
推荐组合新增的 star_filter / invoice_header / vertical_margin / invoice_check / invoice_reorder_v3 等不在旧清单里，会被判为
「新增未冻结文件」。本包装脚本不改原脚本、不改旧清单，只把第 5 项换成核对新清单：
  · 清单内每个文件 sha256 与当前一致；
  · src/baidu_ocr/**/*.py 下没有清单外的新文件（与旧检查同一口径）；
  · 默认 config.yaml 仍是 allow_external: false。
其余检查（PDF、参照格式、计分兼容）原样调用 synth18_ready_check.main()。

  --frozen-only    只做冻结清单这一项（不打开任何数据集目录）
  其余参数同 synth18_ready_check.py（--dir/--pdf/--ref/--expect-pages/--out）
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "output" / "f1_round2" / "frozen_推荐组合.json"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def verify() -> list[str]:
    fz = json.loads(FROZEN.read_text(encoding="utf-8"))
    bad = []
    for rel, h in fz["code_sha256"].items():
        p = ROOT / rel
        if not p.exists():
            bad.append(f"缺失 {rel}")
        elif sha(p) != h:
            bad.append(f"已改动 {rel}")
    extra = {str(p.relative_to(ROOT)) for p in ROOT.glob("src/baidu_ocr/**/*.py") if "__pycache__" not in p.parts} - set(fz["code_sha256"])
    bad += [f"新增未冻结文件 {r}" for r in sorted(extra)]
    if yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")).get("allow_external") is not False:
        bad.append("config.yaml allow_external 不是 false")
    return bad


class _Shim:
    OUT = FROZEN
    verify = staticmethod(verify)


def main() -> int:
    if "--frozen-only" in sys.argv:
        out = ROOT / "output" / "f1_round2" / "ready_check_推荐组合_仅冻结项.json"
        bad = verify()
        R = {"checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "frozen_list": str(FROZEN),
             "frozen_list_sha256": sha(FROZEN), "intact": not bad, "diffs": bad, "ready_frozen_item": not bad,
             "note": "只检查冻结清单这一项；未打开任何数据集目录"}
        out.write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(R, ensure_ascii=False, indent=1))
        return 0 if not bad else 1
    if not any(x.startswith("--out") for x in sys.argv):
        sys.argv += ["--out", str(ROOT / "output" / "f1_round2" / "ready_check_推荐组合.json")]
    spec = importlib.util.spec_from_file_location("synth18_ready_check", ROOT / "scripts" / "synth18_ready_check.py")
    rc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rc)
    orig = rc.load_mod
    rc.load_mod = lambda name, path: _Shim if name == "freeze_config_v2" else orig(name, path)
    return rc.main()


if __name__ == "__main__":
    sys.exit(main())
