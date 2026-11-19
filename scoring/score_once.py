#!/usr/bin/env python3
"""一次性计分：主口径 tolerant_score，严格口径 bs.score_system。

默认：若目标目录已有 SCORED 标记则拒绝再计。不调用百度。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# 允许直接 python scoring/score_once.py
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeline.paths import REPO_ROOT, alias_baidu_keys, dataset_dir, prepare_sys_path


KEYS = ("ref_chars", "matches", "deletions", "substitutions", "insertions")


def _err(o: dict) -> int:
    return int(o["deletions"]) + int(o["substitutions"]) + int(o["insertions"])


def _agg(sc: dict, pages: list[int]) -> dict:
    return {k: sum(sc["per_page"][p]["overall"][k] for p in pages) for k in KEYS}


def _fmt(block: dict) -> dict:
    n = block["ref_chars"]
    err = _err(block)
    readout = (n - block["deletions"]) / n if n else 1.0
    cer = err / n if n else 0.0
    return {**block, "错字数": err, "读出率": round(readout, 6), "错字率": round(cer, 6)}


def score_one(pred: dict, dataset: Path, sign: str = "in") -> dict:
    prepare_sys_path()
    import synth18b_score as SB
    import tolerant_score as T

    bc, bs, hs, refs, rj, mp, rng = SB.load_bs(dataset)
    inv = sorted(p for p in mp if mp[p]["doc_type"] == "发票")
    pages = list(pred)
    # pred 键可能是 str 页码
    pred_norm = {int(k) if str(k).isdigit() else k: v for k, v in pred.items()}
    # score_system / tolerant 通常用 str 或 int？沿用历史脚本直接传 pred
    tol = T.score_tolerant(bs, pred, rj, mp, sign)
    strict = bs.score_system(pred, refs["full"], mp, None)
    non = [p for p in tol["per_page"] if p not in inv]
    all_pages = list(tol["per_page"])
    return {
        "发票页": inv,
        "主口径_全套": _fmt(_agg(tol, all_pages)),
        "主口径_发票页": _fmt(_agg(tol, inv)),
        "主口径_非发票页": _fmt(_agg(tol, non)) if non else None,
        "严格_全套": _fmt(_agg(strict, list(strict["per_page"]))),
        "严格_发票页": _fmt(_agg(strict, inv)),
        "主口径_逐页错字": {p: _err(tol["per_page"][p]["overall"]) for p in tol["per_page"]},
        "严格_逐页错字": {p: _err(strict["per_page"][p]["overall"]) for p in strict["per_page"]},
    }


def _extract_preds(obj) -> dict[str, dict]:
    if not isinstance(obj, dict):
        raise SystemExit("预测文件应为 JSON 对象")
    # 单份预测：页码键
    keys = list(obj)
    if keys and all(str(k).isdigit() for k in keys):
        return {"pred": obj}
    # 已是 {C: ..., 冻结路线: ...} 或带 pred 包装
    out = {}
    if "pred" in obj and isinstance(obj["pred"], dict) and (
        not obj["pred"] or all(str(k).isdigit() for k in obj["pred"])
    ):
        out["pred"] = obj["pred"]
    for k, v in obj.items():
        if k in {"log", "steps", "route", "方案E步骤", "计分时间_伦敦", "pred_sha256", "发票页", "结果"}:
            continue
        if isinstance(v, dict) and v and all(str(p).isdigit() for p in v):
            out[k] = v
        elif k == "C" and isinstance(v, dict):
            out["C"] = v
    if not out:
        raise SystemExit("未能在预测 JSON 中识别页级结果")
    return out


def main(ns: argparse.Namespace | None = None) -> int:
    alias_baidu_keys()
    prepare_sys_path()
    if ns is None:
        ap = argparse.ArgumentParser(description="主口径与严格口径一次性计分，默认遇到 SCORED 则拒绝")
        ap.add_argument("--pred", required=True, help="预测 JSON")
        ap.add_argument("--dataset", required=True, help="data/<集名> 或目录")
        ap.add_argument("--out", required=True, help="计分结果 JSON")
        ap.add_argument("--scored-dir", default="", help="SCORED 标记目录，默认与 --out 同目录")
        ap.add_argument("--force", action="store_true")
        ap.add_argument("--sign", default="in", choices=["in", "out"])
        ns = ap.parse_args()
    if not getattr(ns, "pred", None) or not getattr(ns, "dataset", None) or not getattr(ns, "out", None):
        # 给 python -m pipeline score --help 之后若缺参
        if not getattr(ns, "pred", None):
            raise SystemExit("score 需要 --pred、--dataset、--out")
    pred_path = Path(ns.pred)
    out_path = Path(ns.out)
    scored_dir = Path(ns.scored_dir) if getattr(ns, "scored_dir", None) else out_path.parent
    scored_dir.mkdir(parents=True, exist_ok=True)
    marker = scored_dir / "SCORED"
    if marker.exists() and not getattr(ns, "force", False):
        print(f"已计过分（存在 {marker}），拒绝再跑。如确需覆盖请加 --force。")
        return 2
    obj = json.loads(pred_path.read_text(encoding="utf-8"))
    preds = _extract_preds(obj)
    ds = dataset_dir(ns.dataset)
    results = {}
    for name, pr in preds.items():
        results[name] = score_one(pr, ds, getattr(ns, "sign", "in"))
    payload = {
        "计分时间_伦敦": datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m-%d %H:%M:%S"),
        "pred_sha256": hashlib.sha256(pred_path.read_bytes()).hexdigest(),
        "dataset": str(ds),
        "结果": results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    with marker.open("x" if not getattr(ns, "force", False) else "w", encoding="utf-8") as f:
        f.write(f"{payload['计分时间_伦敦']} 一次性计分 pred_sha256={payload['pred_sha256']}\n")
    # 简要打印，不含任何密钥
    brief = {}
    for name, r in results.items():
        brief[name] = {
            "发票页_错字": r["主口径_发票页"]["错字数"] if r.get("主口径_发票页") else None,
            "发票页_读出率": r["主口径_发票页"]["读出率"] if r.get("主口径_发票页") else None,
            "发票页_错字率": r["主口径_发票页"]["错字率"] if r.get("主口径_发票页") else None,
            "全套_错字": r["主口径_全套"]["错字数"],
        }
    print(json.dumps(brief, ensure_ascii=False))
    print(f"已写入 {out_path}，并标记 {marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
