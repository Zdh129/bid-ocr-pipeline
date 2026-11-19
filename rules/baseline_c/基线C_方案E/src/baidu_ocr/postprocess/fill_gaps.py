"""按区域对齐补 B 漏字：A（高精度含位置）与去掉二维码幻觉后的 Unlimited-OCR 在同一区域、同一位置都多出同一串（≤max_len 字）时，补进 B。

区域 = B 的每个正文块（按 bbox）和每个表格单元格（按单元格 bbox）。
- A：取中心落在区域框内的字框（正文块左右各放宽 a_pad_x，默认 10pt），按行排序成串（全局对齐，首尾多出的字也算插入）。
- U：正文块取与区域重叠的 Unlimited 文本块拼接，做半全局对齐（U 首尾多出的部分只用来核对区域首尾的补字）；
  表格单元格取重叠表格里与 B 单元格最相似的 Unlimited 单元格（相似度≥min_sim），全局对齐。
- 比较前 A、U 也套同样的 ★/序号点规则（与离线模拟一致），全部 NFKC、去空白。
- 只补字，不删、不替换（离线模拟 F1/F3 证明删/替会变差）。
"""
from __future__ import annotations

from difflib import SequenceMatcher

from . import star
from .aux import chars_in, reading_sort
from .textutil import insertions, norm, norm_with_map


def _ov(a, b) -> tuple[float, float]:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy
    aa = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    ab = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
    return i / aa, i / ab


def _prep(s: str) -> str:
    return norm(star.apply_text(s)[0])


def propose(b_text: str, a_text: str, u_text: str, u_free_ends: bool, max_len: int, min_sim: float) -> list[tuple[int, str]]:
    """返回 [(B 规范串中的位置, 补入串)]。"""
    bn = norm(b_text)
    a, u = _prep(a_text), _prep(u_text)
    if not bn or not a or not u:
        return []
    if SequenceMatcher(None, bn, a, autojunk=False).ratio() < min_sim:
        return []
    ins_a, _, _ = insertions(bn, a)
    if not ins_a:
        return []
    ins_u, lead, tail = insertions(bn, u, free_ends=u_free_ends)
    if u_free_ends:
        # U 的窗口要能对上 B（窗口内相似度）
        win = u[len(lead):len(u) - len(tail)] if tail else u[len(lead):]
        if SequenceMatcher(None, bn, win, autojunk=False).ratio() < min_sim:
            return []
    out = []
    for pos, x in sorted(ins_a.items()):
        if not (1 <= len(x) <= max_len):
            continue
        ux = ins_u.get(pos)
        if u_free_ends and pos == 0 and ux is None:
            ux = lead[-len(x):] if len(lead) >= len(x) else None
        if u_free_ends and pos == len(bn) and ux is None:
            ux = tail[:len(x)] if len(tail) >= len(x) else None
        if ux == x:
            out.append((pos, x))
    return out


def insert_at(raw: str, props: list[tuple[int, str]]) -> str:
    _, idx = norm_with_map(raw)
    s = raw
    for pos, x in sorted(props, reverse=True):
        at = idx[pos] if pos < len(idx) else len(raw)
        s = s[:at] + x + s[at:]
    return s


def apply(doc: dict, cfg: dict, log: list, aux: dict) -> int:
    max_len = int(cfg.get("max_len", 2))
    min_sim = float(cfg.get("min_sim", 0.6))
    n = 0
    for b in doc["blocks"]:
        if b["type"] == "seal" or b.get("verification_status") == "rejected":
            continue
        pa = aux["pages"].get(b["page"]) or {"a_chars": [], "u_items": []}
        if b["type"] == "table":
            utabs = [it for it in pa["u_items"] if it["kind"] == "table" and max(_ov(b["bbox"], it["bbox"])) >= 0.3]
            ucells = [c for t in utabs for c in t["cells"]]
            for c in (b.get("table") or {}).get("cells") or []:
                if not c.get("bbox") or not norm(c.get("text")):
                    continue
                a_text = "".join(k["ch"] for k in reading_sort(chars_in(pa["a_chars"], c["bbox"])))
                bn = norm(c["text"])
                best = max(ucells, key=lambda u: SequenceMatcher(None, bn, _prep(u), autojunk=False).ratio(), default=None)
                if best is None or SequenceMatcher(None, bn, _prep(best), autojunk=False).ratio() < min_sim:
                    continue
                props = propose(c["text"], a_text, best, False, max_len, min_sim)
                if props:
                    new = insert_at(c["text"], props)
                    log.append({"step": "fill_gaps", "page": b["page"], "block_id": b["id"], "cell": [c["row"], c["col"]],
                                "before": c["text"], "after": new, "inserted": [x for _, x in props]})
                    c["text"] = new
                    n += sum(len(x) for _, x in props)
            continue
        if not norm(b.get("text")):
            continue
        bb = b["bbox"]
        px = float(cfg.get("a_pad_x", 10.0))  # B 的块框常把没读出的行首字（如 ★）排除在外，A 取字时左右各放宽 px
        a_text = "".join(k["ch"] for k in reading_sort(chars_in(pa["a_chars"], [bb[0] - px, bb[1], bb[2] + px, bb[3]])))
        us = sorted([it for it in pa["u_items"] if it["kind"] == "text" and max(_ov(b["bbox"], it["bbox"])) >= 0.3],
                    key=lambda it: (it["bbox"][1], it["bbox"][0]))
        u_text = "".join(it["text"] for it in us)
        props = propose(b["text"], a_text, u_text, True, max_len, min_sim)
        if props:
            new = insert_at(b["text"], props)
            log.append({"step": "fill_gaps", "page": b["page"], "block_id": b["id"], "before": b["text"], "after": new,
                        "inserted": [x for _, x in props]})
            b["text"] = new
            n += sum(len(x) for _, x in props)
    return n
