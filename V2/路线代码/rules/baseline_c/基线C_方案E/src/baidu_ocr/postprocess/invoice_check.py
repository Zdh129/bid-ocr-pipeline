"""发票金额校验（f1_round2 变体 N4；通用规则，不看参照、不按页号/具体字符写死）。

在最终表格文字上找发票明细行（以「*类别*」开头、末尾为 数量 单价 金额 税率% 税额）、合计行（「合计 ¥金额 ¥税额」）、
价税合计行（大写 + 「(小写)¥金额」），校验：
  R1 数量 × 单价 ≈ 金额（四舍五入到分，容差 0.01）；R2 金额 × 税率 ≈ 税额（容差 0.01）；
  R3 各行金额之和 = 合计金额；R4 各行税额之和 = 合计税额；R5 合计金额 + 合计税额 = 价税合计（小写）；R6 大写 = 小写。
数字粘连时（如「台1165044.25165044.2513%21455.75」）枚举所有切分，满足 R1 的切分才算读通。
改数字只在算式能唯一确定时做：某个数只有一种取值能让涉及它的全部算式同时成立，且其它数都由别的算式交叉印证；
并且新旧数字位数相同、只差 ≤ 2 位。否则只在日志里标记（「需复核」），不改文字、不编造。
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

CN = "零壹贰叁肆伍陆柒捌玖"
UNITS = ["", "拾", "佰", "仟"]


def upper(amount: Decimal) -> str:
    """金额小写 → 大写（常见写法；只用于比对）。"""
    fen = int((amount * 100).quantize(Decimal(1), ROUND_HALF_UP))
    yuan, jiao, f = fen // 100, fen // 10 % 10, fen % 10

    def four(n):
        s, zero = "", False
        for i in range(3, -1, -1):
            d = n // 10 ** i % 10
            if d == 0:
                zero = bool(s)
            else:
                s += ("零" if zero else "") + CN[d] + UNITS[i]
                zero = False
        return s
    parts, s = [], ""
    for i, unit in enumerate(["", "万", "亿"]):
        n = yuan // 10 ** (4 * i) % 10 ** 4
        parts.append((n, unit))
    need_zero = False
    for n, unit in reversed(parts):
        if n == 0:
            need_zero = bool(s)
            continue
        if s and (need_zero or n < 1000):
            s += "零"
        s += four(n) + unit
        need_zero = False
    s = (s or "零") + "元"
    if jiao == 0 and f == 0:
        return s + "整"
    if jiao:
        s += CN[jiao] + "角"
    elif yuan:
        s += "零"
    if f:
        s += CN[f] + "分"
    return s


def D(x):
    return Decimal(x)


def q2(x):
    return x.quantize(Decimal("0.01"), ROUND_HALF_UP)


ITEM_START = re.compile(r"\*[^*\n]{1,20}\*")


def parse_item(line):
    """返回 {parses:[{qty,price,amount,rate,tax}], ...}；只取满足 R1 的切分。"""
    m = ITEM_START.search(line)
    if not m or "%" not in line[m.start():]:
        return None
    seg = line[m.start():]
    k = seg.rfind("%")
    tax = seg[k + 1:].strip().lstrip("¥")
    if not re.fullmatch(r"(0|[1-9]\d*)\.\d{2}|\*+|免税|不征税", tax):
        return {"parses": [], "raw": line, "tax": tax, "why": "税额读不出"}
    run = re.search(r"([\d.\s]+)$", seg[:k])
    if not run:
        return {"parses": [], "raw": line, "tax": tax, "why": "数字段读不出"}
    toks = run.group(1).split()
    s_ = "".join(toks)
    parses = []
    for r in range(1, min(4, len(s_) - 3) + 1):
        rate = s_[len(s_) - r:]
        if not re.fullmatch(r"\d{1,2}(\.\d)?", rate):
            continue
        body = s_[: len(s_) - r]
        for i in range(1, len(body)):
            amt = body[i:]
            if not re.fullmatch(r"(0|[1-9]\d*)\.\d{2}", amt):
                continue
            rest = body[:i]
            for j in range(1, len(rest)):
                qty, price = rest[:j], rest[j:]
                if not re.fullmatch(r"[1-9]\d*(\.\d+)?", qty) or not re.fullmatch(r"(0|[1-9]\d*)(\.\d{1,10})?", price):
                    continue
                if abs(q2(D(qty) * D(price)) - D(amt)) <= Decimal("0.01"):
                    parses.append({"qty": qty, "price": price, "amount": amt, "rate": rate,
                                   "by_spaces": len(toks) >= 4 and [qty, price, amt, rate] == toks[-4:]})
    if any(p["by_spaces"] for p in parses):   # 有空格分隔且分隔给出的切分能读通时，只认它；否则空格不可靠，看全部切分
        parses = [p for p in parses if p["by_spaces"]]
    return {"parses": parses, "raw": line, "tax": tax}


def check_page(text):
    """在整段表格文字上找明细（名称可能折行）、合计、价税合计；返回位置用原文偏移（line 字段存的是偏移）。"""
    lines = text
    items, total, grand = [], None, None
    tot_m = re.search(r"(?m)^\s*合计\s*¥?\s*([\d.]+)\s*¥\s*([\d.]+)", text) or re.search(r"合计\s*¥\s*([\d.]+)\s*¥\s*([\d.]+)", text)
    grand_m = re.search(r"价税合计.*?[（(]?大写[)）]?\s*[⊗ⓧ]?\s*([零壹贰叁肆伍陆柒捌玖拾佰仟万亿元角分整]+).*?[（(]小写[)）]\s*¥\s*([\d.]+)", text, re.S)
    stop = min([m.start() for m in (tot_m, grand_m) if m] + [len(text)])
    starts = [m.start() for m in ITEM_START.finditer(text, 0, stop)]
    for i, a in enumerate(starts):
        b = starts[i + 1] if i + 1 < len(starts) else stop
        seg = text[a:b].rstrip()
        it = parse_item(seg.replace("\n", ""))
        if it is not None:
            it["line"], it["end"] = a, a + len(seg)
            items.append(it)
    if tot_m:
        total = {"line": tot_m.start(), "amount": tot_m.group(1), "tax": tot_m.group(2)}
    if grand_m:
        grand = {"line": grand_m.start(), "upper": grand_m.group(1), "lower": grand_m.group(2)}
    return lines, items, total, grand


def apply_text(text, log, page):
    lines, items, total, grand = check_page(text)
    if not items:
        return text, 0
    notes, fixes = [], []
    chosen = []
    for it in items:
        if not re.fullmatch(r"(0|[1-9]\d*)\.\d{2}", it["tax"]):
            chosen.append((it, None, None))
            notes.append({"line": it["line"], "rule": "R1", "msg": it.get("why") or "税额不是数字"})
            continue
        both = [p for p in it["parses"] if abs(q2(D(p["amount"]) * D(p["rate"]) / 100) - D(it["tax"])) <= Decimal("0.01")]
        if len(both) == 1:
            chosen.append((it, both[0], D(it["tax"])))
        elif len(both) > 1:
            chosen.append((it, None, None))
            notes.append({"line": it["line"], "rule": "R1/R2", "msg": f"能读通的切分有 {len(both)} 种，不唯一"})
        elif len(it["parses"]) == 1:
            p = it["parses"][0]
            exp_tax = q2(D(p["amount"]) * D(p["rate"]) / 100)
            chosen.append((it, p, None))
            notes.append({"line": it["line"], "rule": "R2", "msg": f"金额×税率={exp_tax} 与税额 {it['tax']} 不符", "candidate_tax": str(exp_tax)})
        else:
            chosen.append((it, None, None))
            notes.append({"line": it["line"], "rule": "R1", "msg": f"满足 数量×单价=金额 的切分有 {len(it['parses'])} 种（需恰好 1 种）"})
    amounts_ok = all(p is not None for _, p, _ in chosen)
    if total and amounts_ok:
        s_am = sum(D(p["amount"]) for _, p, _ in chosen)
        if s_am != D(total["amount"]):
            notes.append({"line": total["line"], "rule": "R3", "msg": f"行金额之和 {s_am} ≠ 合计金额 {total['amount']}"})
        # R2 失败的行：若改成 金额×税率 后 R4 恰好成立，且只差 ≤2 位、位数相同 → 唯一确定，改
        bad = [(it, p) for it, p, t in chosen if t is None]
        if len(bad) == 1:
            it, p = bad[0]
            new = q2(D(p["amount"]) * D(p["rate"]) / 100)
            s_tax = sum((t for _, _, t in chosen if t is not None), Decimal(0)) + new
            old = it["tax"]
            diff = sum(1 for a, b in zip(str(new), old) if a != b) + abs(len(str(new)) - len(old))
            if s_tax == D(total["tax"]) and len(str(new)) == len(old) and diff <= 2:
                lines = lines[: it["end"] - len(old)] + str(new) + lines[it["end"]:]
                fixes.append({"line": it["line"], "rule": "R2+R4", "from": old, "to": str(new)})
        elif not bad:
            s_tax = sum(t for _, _, t in chosen)
            if s_tax != D(total["tax"]):
                notes.append({"line": total["line"], "rule": "R4", "msg": f"行税额之和 {s_tax} ≠ 合计税额 {total['tax']}"})
    if total and grand:
        if D(total["amount"]) + D(total["tax"]) != D(grand["lower"]):
            notes.append({"line": grand["line"], "rule": "R5", "msg": f"合计金额+合计税额 ≠ 价税合计 {grand['lower']}"})
    if grand:
        u = upper(D(grand["lower"]))
        if u != grand["upper"]:
            notes.append({"line": grand["line"], "rule": "R6", "msg": f"大写「{grand['upper']}」与小写 {grand['lower']} 的大写「{u}」不符"})
    log.append({"step": "invoice_check", "page": page, "items": len(items), "items_read_through": sum(1 for _, p, _ in chosen if p is not None),
                "total_found": bool(total), "grand_found": bool(grand), "fixes": fixes, "needs_review": notes})
    return lines, len(fixes)


def apply_pred(pr, log):
    n = 0
    for p, pg in pr.items():
        for s in pg["sections"]:
            if s["section"] == "table":
                s["text"], k = apply_text(s["text"], log, int(p))
                n += k
    return n
