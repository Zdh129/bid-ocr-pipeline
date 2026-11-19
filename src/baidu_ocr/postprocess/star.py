"""★/☆ 与序号点规则（只改预测正文与表格单元格，不改印章）。

R1 「☆」后紧跟数字（允许空白）→「★」。blind20 参照中 ☆ 0 次、★ 30 次且都紧跟条款号。
R2 「★」+条款号（\\d+(.\\d+)*）后紧跟的「。」「，」「,」→「.」。
R3 行首 1–2 位序号后紧跟「。」且下一个字不是数字 →「.」（如「3。使用温度」→「3.使用温度」）。
与离线模拟 analysis-opt/sim.py 的 star() 的差别：R1 只改「☆+数字」，不把所有 ☆ 改成 ★（更保守）。
"""
from __future__ import annotations

import re

R1 = re.compile(r"☆(?=\s*\d)")
R2 = re.compile(r"(★\s*\d+(?:\s*[.．]\s*\d+)*)\s*[。，,]")
R3 = re.compile(r"^(\s*\d{1,2})。(?=\D)", re.M)

RULE_TEXT = ("R1 ☆+数字→★；R2 ★+条款号后的「。/，/,」→「.」；R3 行首 1–2 位序号后的「。」（后面不是数字）→「.」。"
             "只改正文和表格单元格，不改印章。")


def apply_text(text: str, use_r3: bool = True) -> tuple[str, int]:
    if not text:
        return text, 0
    t, n1 = R1.subn("★", text)
    t, n2 = R2.subn(lambda m: m.group(1) + ".", t)
    n3 = 0
    if use_r3:
        t, n3 = R3.subn(lambda m: m.group(1) + ".", t)
    return t, n1 + n2 + n3


def apply(doc: dict, cfg: dict, log: list) -> int:
    use_r3 = bool(cfg.get("serial_dot", True))
    n = 0
    for b in doc.get("blocks") or []:
        if b.get("type") == "seal" or b.get("verification_status") == "rejected":
            continue
        if b.get("text"):
            new, k = apply_text(b["text"], use_r3)
            if k:
                log.append({"step": "star", "page": b["page"], "block_id": b["id"], "before": b["text"], "after": new})
                b["text"], n = new, n + k
        for c in ((b.get("table") or {}).get("cells") or []):
            new, k = apply_text(c.get("text") or "", use_r3)
            if k:
                log.append({"step": "star", "page": b["page"], "block_id": b["id"], "cell": [c["row"], c["col"]], "before": c["text"], "after": new})
                c["text"], n = new, n + k
    return n
