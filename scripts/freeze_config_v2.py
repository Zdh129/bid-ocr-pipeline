#!/usr/bin/env python3
"""冻结「新最优解」（bestconfig_v2）的代码、配置和变体开关 → output/blindset2_prep/frozen_config_v2.json（不联网）。

本目录不是 git 仓库，所以用逐文件 sha256 清单冻结。之后在新集（synth18）上跑之前，用
`scripts/synth18_ready_check.py`（或本脚本 --verify）核对清单，任何一处不一致即视为规则被改过。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "output" / "blindset2_prep" / "frozen_config_v2.json"

FILES_GLOBS = ["src/baidu_ocr/**/*.py", "schemas/*.json", "config.yaml", "config_astra_v2_live.yaml"]
SCRIPTS = ["scripts/bestconfig_run.py", "scripts/bestconfig_v2_run.py", "scripts/bestconfig_v2_build.py", "scripts/astra_run.py",
           "scripts/blind20_score.py", "scripts/hybrid_score.py", "scripts/run_score.py", "scripts/hybrid_prepare.py",
           "scripts/freeze_config_v2.py"]
TESTS = ["tests/test_postprocess.py", "tests/test_vlm_backfill.py", "tests/test_astra.py", "tests/test_hybrid.py", "tests/test_score.py"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def file_list() -> list[Path]:
    out = set()
    for g in FILES_GLOBS:
        out |= {p for p in ROOT.glob(g) if "__pycache__" not in p.parts}
    out |= {ROOT / s for s in SCRIPTS + TESTS}
    return sorted(out)


def build() -> dict:
    import yaml

    from baidu_ocr.hybrid.crops import prompt_for, prompt_hash
    from baidu_ocr.hybrid.rules import RULES
    from baidu_ocr.postprocess.pipeline import DEFAULT_CFG, ORDER, merged_cfg
    from baidu_ocr.postprocess.vlm_backfill import RULE_TEXT

    cfg_all = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    pp = merged_cfg(cfg_all["postprocess"])
    live = yaml.safe_load((ROOT / "config_astra_v2_live.yaml").read_text(encoding="utf-8"))["vlm"]
    vb = {**pp["vlm_backfill"], "enabled": True}
    _, line_prompt = prompt_for({"kind": "line"})
    files = {str(p.relative_to(ROOT)): sha(p) for p in file_list()}
    pip = subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "pip", "freeze"], capture_output=True, text=True).stdout
    v2 = ROOT / "output/bestconfig_v2"
    return {
        "name": "bestconfig_v2（新最优解）",
        "frozen_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frozen_at_note": "伦敦时间（UTC+0）与 UTC 相同",
        "git": "/workspace/pdf-test/baidu 不是 git 仓库 → 用逐文件 sha256 清单冻结",
        "tuned_on": "blind20（frozen20/blind20-scan.pdf）；所有规则在这 20 页上调出，不是盲测",
        "blind20_scores_corrected_ref": {"B_baseline": [98.69, 1.67], "v1_bestconfig": [99.57, 0.61], "v2": [99.75, 0.43],
                                         "v2_without_two_posthoc_rules": [99.58, 0.85], "unit": "读出率% / CER%"},
        "pipeline": {
            "main_ocr": "百度 文档解析标准版（B）原始返回 → 中间 JSON（schema 1.0.0）",
            "order": list(ORDER),
            "postprocess_default_config_yaml": cfg_all["postprocess"],
            "default_off_in_config_yaml": {"allow_external": cfg_all.get("allow_external"), "vlm_backfill.enabled": cfg_all["postprocess"]["vlm_backfill"]["enabled"]},
            "aux_inputs": "A = 通用文字识别（高精度含位置版，原走法：每页主调 + 补识）；U = 文档解析 Unlimited-OCR（剔除二维码幻觉块）",
        },
        "variants": {
            "v1": {"desc": "当前最优解（四步，无 6.2）", "postprocess_overrides": {"vlm_backfill": {"enabled": False}}},
            "v2": {"desc": "新最优解：v1 + 6.2 (a) 漏读行补漏 + (b) 手写日期复核，三条漏行规则全开", "postprocess_overrides": {"vlm_backfill": vb}},
            "v2_no_posthoc_rules": {"desc": "「不加两条护栏」：去掉看过 blind20 参照 diff 后才加的去重护栏和页边竖排放页尾，只保留开跑前就有的一致性护栏（= 第一版 v2，blind20 上 99.58%/0.85%）",
                                    "postprocess_overrides": {"vlm_backfill": {**vb, "dedup_guard": False, "margin_vertical_last": False}}},
            "v2_a_only": {"desc": "只用 (a)", "postprocess_overrides": {"vlm_backfill": {**vb, "handwritten_date": False}}},
            "v2_b_only": {"desc": "只用 (b)", "postprocess_overrides": {"vlm_backfill": {**vb, "missing_line": False}}},
            "v2_no_guards": {"desc": "参考：三条漏行规则全关（只要 6.2 输出非空就按 y 插入）",
                             "postprocess_overrides": {"vlm_backfill": {**vb, "agree_guard": False, "dedup_guard": False, "margin_vertical_last": False}}},
            "primary_for_new_set": "新集上 v2 与 v2_no_posthoc_rules 都要报；判断泛化以 v2_no_posthoc_rules 为准，v2 的额外收益只作参考",
        },
        "region_rules": {
            "missing_line": "hybrid/rules.select_route2 的 missing_line（A 有行而 B 无块覆盖的 A 行；A/B 都未覆盖、且不在印章区内的空白缺口），RULES 见下",
            "handwritten_date": RULE_TEXT,
            "hybrid_rules": RULES,
        },
        "vlm": {"model": live["model"], "detail": live["detail"], "reasoning_effort": live["reasoning_effort"],
                "max_completion_tokens": live["max_completion_tokens"], "prompt_kind": "line", "line_prompt_sha12": prompt_hash(line_prompt),
                "line_prompt": line_prompt, "crop": "200 DPI 整页渲染，line/missing_line 外扩 x10/y5 px，长边 ≤1600",
                "batch_first": True, "batch_timeout_min": live["batch_timeout_min"],
                "live_config": "config_astra_v2_live.yaml（新集上须另建外发配置并经用户批准；默认 config.yaml 不外发）"},
        "scoring": {"scorer": "scripts/blind20_score.py（parse_ref / score_system）+ bestconfig_run.ref_setup/categorize",
                    "rules": "NFKC、忽略空白、保留标点、参照【?】排除；【印章：…】单独计分；含「 | 」的行算表格并去掉竖线；逐页 Levenshtein",
                    "note": "blind20_score.py 里 REF/MANIFEST 路径写死为 frozen20；新集需用参数/包装脚本传入，不改计分规则本身"},
        "files_sha256": files,
        "python": platform.python_version(),
        "pip_freeze_sha256": hashlib.sha256(pip.encode()).hexdigest(),
        "pip_freeze": pip.splitlines(),
        "reference_outputs_sha256": {str(p.relative_to(ROOT)): sha(p) for p in (v2 / "blind20-scan.json", v2 / "pred_pages.json", v2 / "postprocess_log.json") if p.exists()},
    }


def verify(path: Path = OUT) -> list[str]:
    fz = json.loads(path.read_text(encoding="utf-8"))
    bad = []
    for rel, h in fz["files_sha256"].items():
        p = ROOT / rel
        if not p.exists():
            bad.append(f"缺失 {rel}")
        elif sha(p) != h:
            bad.append(f"已改动 {rel}")
    extra = {str(p.relative_to(ROOT)) for p in file_list()} - set(fz["files_sha256"])
    bad += [f"新增未冻结文件 {r}" for r in sorted(extra)]
    return bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    if a.verify:
        b = verify()
        print("冻结清单一致" if not b else "\n".join(b))
        sys.exit(1 if b else 0)
    if OUT.exists():
        sys.exit(f"{OUT} 已存在：冻结清单只生成一次，不覆盖（要重冻须先人工确认并改名旧文件）")
    d = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print(OUT, len(d["files_sha256"]), "files")
