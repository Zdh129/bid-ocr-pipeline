"""两个方案并排对比，判断谁更接近读出率 ≥99%、CER ≤1%。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def closer(a: dict | None, b: dict | None, *, readout_thr: float, cer_thr: float) -> str:
    """返回 scheme_a / scheme_b / tie / insufficient。"""
    if not a or not b or a.get("note") == "no_chatgpt_reference" or b.get("note") == "no_chatgpt_reference":
        return "insufficient"
    def ok(m):
        return (m.get("readout") or 0) >= readout_thr and (m.get("cer") or 1) <= cer_thr
    a_ok, b_ok = ok(a), ok(b)
    if a_ok and not b_ok:
        return "scheme_a"
    if b_ok and not a_ok:
        return "scheme_b"
    # 都达标或都不达标：看与门槛的距离（读出率差 + CER 差）
    def deficit(m):
        return max(0.0, readout_thr - (m.get("readout") or 0)) + max(0.0, (m.get("cer") or 0) - cer_thr)
    da, db = deficit(a), deficit(b)
    if abs(da - db) < 1e-9 and abs((a.get("readout") or 0) - (b.get("readout") or 0)) < 1e-9 and abs((a.get("cer") or 0) - (b.get("cer") or 0)) < 1e-9:
        return "tie"
    # 若不都达标，deficit 更小更好；若都达标，CER 更低、读出率更高更好
    if a_ok and b_ok:
        key_a = (-(a.get("readout") or 0), a.get("cer") or 0)
        key_b = (-(b.get("readout") or 0), b.get("cer") or 0)
        if key_a < key_b:
            return "scheme_a"
        if key_b < key_a:
            return "scheme_b"
        return "tie"
    return "scheme_a" if da < db else "scheme_b"


def build_report(
    *,
    scheme_a: dict,
    scheme_b: dict,
    readout_thr: float = 0.99,
    cer_thr: float = 0.01,
    notes: list[str] | None = None,
    frozen20_ready: bool = False,
) -> dict[str, Any]:
    a_over = ((scheme_a.get("scores") or {}).get("overall")) or scheme_a.get("overall")
    b_over = ((scheme_b.get("scores") or {}).get("overall")) or scheme_b.get("overall")
    winner = closer(a_over, b_over, readout_thr=readout_thr, cer_thr=cer_thr)
    return {
        "threshold": {"readout": readout_thr, "cer": cer_thr},
        "frozen20_ready": frozen20_ready,
        "winner": winner,
        "scheme_a": scheme_a,
        "scheme_b": scheme_b,
        "notes": notes or [],
    }


def _fmt_metrics(m: dict | None) -> list[str]:
    if not m:
        return ["—", "—", "—", "—", "—", "—"]
    if m.get("note") == "no_chatgpt_reference":
        return ["无 ChatGPT 参照", "—", "—", "—", "—", "—"]
    return [
        f"{m.get('readout', 0):.4f}",
        f"{m.get('cer', 0):.4f}",
        str(m.get("deletions", "")),
        str(m.get("substitutions", "")),
        str(m.get("insertions", "")),
        str(m.get("ref_chars", "")),
    ]


def render_markdown(report: dict) -> str:
    thr = report.get("threshold") or {}
    a = report.get("scheme_a") or {}
    b = report.get("scheme_b") or {}
    a_scores = a.get("scores") or {}
    b_scores = b.get("scores") or {}
    winner = report.get("winner")
    winner_cn = {
        "scheme_a": "方案 A（杨工混合）更接近门槛",
        "scheme_b": "方案 B（文档解析）更接近门槛",
        "tie": "两方案持平",
        "insufficient": "缺少 ChatGPT 网页参照，不能比较准确率",
    }.get(winner, winner)
    lines = [
        "# 百度 OCR 复测对比报告",
        "",
        f"门槛：读出率 ≥ {thr.get('readout', 0.99):.0%} 且 CER ≤ {thr.get('cer', 0.01):.0%}。",
        f"结论：{winner_cn}。",
        "",
        "计分口径：NFKC、忽略空白、保留标点；参照中的【?】已排除。删除数即漏字。",
        "",
        "## 总体指标",
        "",
        "| 方案 | 读出率 | CER | 删除(漏字) | 替换 | 插入 | 参照字符 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    def row(name, rec):
        m = (rec.get("scores") or {}).get("overall") or rec.get("overall")
        return f"| {name} | " + " | ".join(_fmt_metrics(m)) + " |"
    lines.append(row("A 杨工混合", a))
    lines.append(row("B 文档解析", b))
    lines += ["", "## 分项（参照能对应时）", ""]
    lines.append("| 分区 | A 读出率 | A CER | B 读出率 | B CER |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for sec in ("body", "table", "seal"):
        am, bm = a_scores.get(sec), b_scores.get(sec)
        if not am and not bm:
            continue
        def g(m, k):
            if not m or m.get("note") == "no_chatgpt_reference":
                return "—"
            return f"{m.get(k, 0):.4f}"
        lines.append(f"| {sec} | {g(am,'readout')} | {g(am,'cer')} | {g(bm,'readout')} | {g(bm,'cer')} |")
    lines += [
        "",
        "## 耗时与费用（官方单价估算）",
        "",
        "| 方案 | 版本/接口 | 耗时（秒） | 调用次数 | 费用（元） | 费用（美元，估算汇率） |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    def cost_row(name, rec):
        cost = rec.get("cost") or {}
        edition = rec.get("edition") or rec.get("apis") or ""
        return (
            f"| {name} | {edition} | {rec.get('elapsed_sec', cost.get('elapsed_sec', '—'))} "
            f"| {cost.get('n_calls', '—')} | {cost.get('total_cny', '—')} | {cost.get('total_usd', '—')} |"
        )
    lines.append(cost_row("A", a))
    lines.append(cost_row("B", b))
    if not report.get("frozen20_ready"):
        lines += [
            "",
            "## 参照状态",
            "",
            "20 页冻结 PDF 与 ChatGPT 参照尚未到位（参照 SHA256 应以 da93f43f 开头）。",
            "现有难页样本没有 ChatGPT 网页参照，本报告**不以文字层或其他输出冒充参照，不把难页的准确率当作验收结果**。",
        ]
    notes = report.get("notes") or []
    if notes:
        lines += ["", "## 备注", ""]
        for n in notes:
            lines.append(f"- {n}")
    lines.append("")
    return "\n".join(lines)


def write_report(report: dict, out_dir: str | Path) -> tuple[Path, Path]:
    import json

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / "compare.md"
    js = out_dir / "compare.json"
    md.write_text(render_markdown(report), encoding="utf-8")
    js.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return md, js
