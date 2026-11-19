#!/usr/bin/env python3
"""入口：python -m pipeline <子命令>。默认不调用百度。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.paths import REPO_ROOT, alias_baidu_keys, dataset_dir, prepare_sys_path


def _add_common(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--pred-c", help="基线C预测 JSON（页码 -> 预测页）")
    ap.add_argument("--scheme-a-dir", help="方案A输出目录（含 call_summary.json 与字框）")
    ap.add_argument("--pdf", help="扫描 PDF 路径")
    ap.add_argument("--set-name", help="数据集名称，如 synth18f")
    ap.add_argument("--lines-dir", help="表格线缓存目录，默认 output/表格线缓存/<集名>")
    ap.add_argument("--doc-path", help="文档解析标准版中间 JSON（方案B）")
    ap.add_argument("--renders-dir", help="页面渲染图目录")
    ap.add_argument("--reread-path", help="二次识别 results.json")
    ap.add_argument("--out", help="输出 JSON 路径")


def _require(ns, *names: str) -> None:
    missing = [n for n in names if not getattr(ns, n.replace("-", "_"), None)]
    # use dest names
    dest = []
    for n in names:
        attr = n.replace("-", "_")
        if not getattr(ns, attr, None):
            dest.append("--" + n if not n.startswith("out") else "--out" if n == "out" else "--" + n)
    # simpler:
    mapping = {
        "pred_c": "--pred-c",
        "scheme_a_dir": "--scheme-a-dir",
        "pdf": "--pdf",
        "set_name": "--set-name",
        "doc_path": "--doc-path",
        "renders_dir": "--renders-dir",
        "reread_path": "--reread-path",
        "out": "--out",
    }
    miss = [flag for attr, flag in mapping.items() if attr in names and not getattr(ns, attr)]
    if miss:
        raise SystemExit("缺少参数: " + ", ".join(miss))


def _lines_dir(ns) -> Path:
    if ns.lines_dir:
        return Path(ns.lines_dir)
    name = ns.set_name or "default"
    d = REPO_ROOT / "output" / "表格线缓存" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_baseline_c(ns) -> int:
    """在已有方案E输出上套改进⊗，得到基线C。不调用百度。"""
    alias_baidu_keys()
    prepare_sys_path()
    _require(ns, "pred_c", "out")
    pred = json.loads(Path(ns.pred_c).read_text(encoding="utf-8"))
    # 允许文件是 {'方案E': ...} 或直接为页字典
    if isinstance(pred, dict) and "方案E" in pred and "sections" not in next(iter(pred.values()), {}):
        e = pred["方案E"]
    else:
        e = pred
    from pipeline.load_frozen import load_round
    mods = load_round("baseline_c")
    C = mods["variants_blind"].base_C(e)
    out = {"C": C}
    Path(ns.out).parent.mkdir(parents=True, exist_ok=True)
    Path(ns.out).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"已写入基线C预测: {ns.out}")
    return 0


def _run_stack(ns, which: str) -> int:
    alias_baidu_keys()
    prepare_sys_path()
    _require(ns, "pred_c", "scheme_a_dir", "pdf", "set_name", "doc_path", "renders_dir", "reread_path", "out")
    raw = json.loads(Path(ns.pred_c).read_text(encoding="utf-8"))
    C = raw["C"] if isinstance(raw, dict) and "C" in raw else raw
    from pipeline.load_frozen import load_round
    mods = load_round(which)
    stack = {"round1": mods.get("frozen_stack"), "round2": mods.get("frozen_stack2"), "round3": mods.get("frozen_stack3")}[which]
    st, log = stack.run_stack(
        C,
        ns.scheme_a_dir,
        ns.pdf,
        ns.set_name,
        _lines_dir(ns),
        ns.doc_path,
        ns.renders_dir,
        ns.reread_path,
    )
    key = "S6_竖排融合"
    payload = {"pred": st[key], "steps": st, "log": log, "route": which}
    Path(ns.out).parent.mkdir(parents=True, exist_ok=True)
    Path(ns.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"已写入{which}预测: {ns.out}")
    return 0


def cmd_score(ns) -> int:
    alias_baidu_keys()
    prepare_sys_path()
    from scoring.score_once import main as score_main
    return score_main(ns)


def cmd_verify(ns) -> int:
    from pipeline.verify_frozen import main as verify_main
    return verify_main(ns)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m pipeline",
        description="OCR 识别流水线入口。默认不调用百度、不产生费用。",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p0 = sub.add_parser("baseline-c", help="由方案E预测生成基线C（方案E＋改进⊗），纯本地")
    _add_common(p0)
    p0.set_defaults(func=cmd_baseline_c)

    p1 = sub.add_parser("round1", help="第1轮冻结路线（乙′＋合计/表头＋栏位纠正＋竖排融合），纯本地")
    _add_common(p1)
    p1.set_defaults(func=lambda ns: _run_stack(ns, "round1"))

    p2 = sub.add_parser("round2", help="第2轮冻结路线（在第1轮上增加合计行/表头带与栏位边界守卫），纯本地")
    _add_common(p2)
    p2.set_defaults(func=lambda ns: _run_stack(ns, "round2"))

    p2b = sub.add_parser("round3", help="第3轮冻结路线（当前推荐）：第2轮＋F4 格内折行续行归格＋F5 页首行坐标复位，纯本地")
    _add_common(p2b)
    p2b.set_defaults(func=lambda ns: _run_stack(ns, "round3"))

    p3 = sub.add_parser("score", help="主口径（tolerant_score）与严格口径（score_system）一次性计分")
    p3.add_argument("--pred", required=False, help="预测 JSON（页码 -> 页，或含 C/冻结路线 等键）")
    p3.add_argument("--dataset", required=False, help="data/<集名> 目录或集名")
    p3.add_argument("--out", required=False, help="计分结果 JSON")
    p3.add_argument("--scored-dir", help="写入 SCORED 标记的目录（与 --out 同目录，默认）")
    p3.add_argument("--force", action="store_true", help="若已有 SCORED 仍强制再计（默认拒绝）")
    p3.add_argument("--sign", default="in", choices=["in", "out"], help="手写签名口径，默认 in")
    p3.set_defaults(func=cmd_score)

    p4 = sub.add_parser("verify", help="核对 rules/ 中冻结文件的 sha256，不改文件")
    p4.add_argument("--round", dest="round_name", default="all", choices=["all", "baseline_c", "round1", "round2", "round3"])
    p4.set_defaults(func=cmd_verify)

    p5 = sub.add_parser("check-env", help="只报告密钥环境变量是否已设置，不打印值，不联网")
    p5.set_defaults(func=lambda ns: _check_env())

    args = ap.parse_args(argv)
    return int(args.func(args) or 0)


def _check_env() -> int:
    alias_baidu_keys()
    prepare_sys_path()
    from baidu_ocr.auth import key_status
    from baidu_ocr.config import load_config
    cfg = load_config(REPO_ROOT / "config.yaml")
    st = key_status(cfg)
    print("config:", cfg["_config_path"])
    print("allow_external:", st["allow_external"])
    print(f"{st['api_key_env']}:", "set" if st["api_key_set"] else "missing")
    print(f"{st['secret_key_env']}:", "set" if st["secret_key_set"] else "missing")
    if st["allow_external"]:
        print("警告: allow_external=true，只有在已获书面批准后才应出现。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
