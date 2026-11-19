"""★/☆ 后处理规则（只作用于预测文本，绝不改参照）。

规则 R1：「☆」后面紧跟阿拉伯数字时改为「★」。
    依据：招标/合同技术条款里「★+条款号」是实质性条款标记；扫描件上实心星常被识别成空心星。
    blind20 参照中 ☆ 出现 0 次、★ 30 次且全部紧跟条款号。
规则 R2：「★」+ 条款号（数字，可带 .数字 多级）后面紧跟的「。」改为「.」。
    例：「☆3。使用温度」→ R1 →「★3。使用温度」→ R2 →「★3.使用温度」。
    只在 ★ 条款号后生效，不动其他位置的句号。
"""
from __future__ import annotations

import copy
import re

R1 = re.compile(r"☆(?=\s*\d)")
R2 = re.compile(r"(★\s*\d+(?:\s*[.．]\s*\d+)*)\s*。")

RULE_TEXT = (
    "R1：「☆」后紧跟阿拉伯数字（允许中间有空白）→ 改为「★」。"
    "R2：「★」+条款号（\\d+(\\.\\d+)*）后紧跟的「。」→ 改为「.」。"
    "只改预测文本（正文、表格单元格），不改印章文字，不改参照。"
)


def apply_star_rule(text: str) -> tuple[str, int]:
    """返回 (新文本, 改动次数)。"""
    if not text:
        return text, 0
    t1, n1 = R1.subn("★", text)
    t2, n2 = R2.subn(lambda m: m.group(1) + ".", t1)
    return t2, n1 + n2


def apply_to_pred_pages(pred_pages: dict) -> tuple[dict, dict]:
    """pred_pages: {page: {sections:[{section,text}], seal}}。返回新副本与每页改动次数。印章不动。"""
    out = copy.deepcopy(pred_pages)
    hits: dict[str, int] = {}
    for p, pg in out.items():
        n = 0
        for s in pg.get("sections") or []:
            s["text"], k = apply_star_rule(s.get("text") or "")
            n += k
        if n:
            hits[str(p)] = n
    return out, hits


def apply_to_document(doc: dict) -> tuple[dict, int]:
    """对中间 JSON 的正文块 text 与表格单元格 text 应用规则；印章块不动。"""
    out = copy.deepcopy(doc)
    n = 0
    for b in out.get("blocks") or []:
        if b.get("type") == "seal":
            continue
        if b.get("text"):
            b["text"], k = apply_star_rule(b["text"])
            n += k
        for c in ((b.get("table") or {}).get("cells") or []):
            c["text"], k = apply_star_rule(c.get("text") or "")
            n += k
    return out, n
