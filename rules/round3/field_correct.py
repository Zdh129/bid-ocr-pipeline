# -*- coding: utf-8 -*-
"""识读类栏位约束纠错（事后/试算，纯本地）。

路径：
  /workspace/pdf-test/baidu/output/盲测_版面约束/版面分析优化计划/试算/并行_栏位纠正/field_correct.py

给其他试算模块叠加时：

    import sys
    from pathlib import Path
    SYS_DIR = Path('/workspace/pdf-test/baidu/output/盲测_版面约束/版面分析优化计划/试算/并行_栏位纠正')
    sys.path.insert(0, str(SYS_DIR))
    from field_correct import correct_text, correct_pred

    new_text, log = correct_text(section_text)
    new_pred, log = correct_pred(pred)  # pred 与计分器相同：{页: {sections:[{section,text},...]}}

规则按栏位类型写，不写某一页的具体字串。
第2轮（事后/试算）：税号、发票号码/代码、日期、银行账号、电话这些数字型栏位，取到的串后面紧跟字母或数字时整串不动（串没有边界，说明栏位取错了，如电话后紧接邮箱「…1903liumeijin」）。本文件未冻结，只作试算。
不改识读之外的字；找不到栏位标签或形态对不上时不动。

栏位与纠正：
  - 税号 / 统一社会信用代码 / 纳税人识别号
        18 位：O/o→0、I/i→1、l→1（官方字符集不含 I、O；S/Z/V 本试算不做，
        因为合成集里会出现这些字母的参照）。15 位旧税号（几乎全是数字）：仅 O/o→0。
  - 发票号码、发票代码：其余都是数字时，O/o→0、I/i/l→1。
  - 开票日期 / 日期：日期形态里的 O/I/l 同上。
  - 银行账号 / 账号、电话：数字串里的 O/I/l 同上。
  - 金额 / 单价 / 税额类金额、税率（…%）：独立数字记号里的 O/I/l 同上
        （不碰 PH-880、DO-560 这类型号）。
  - 密码区：☆★＊→*，以及其中的 O/I/l。
  - 掩码栏（免税/不征税后的星号串，或金额后长度≥2 的纯星号串）：☆★＊→*。
        不改招标/技术规格里作为「必配/选配/实质性条款」标记的单颗 ★/☆。
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

Log = List[Dict[str, Any]]

# 形近：字母 → 数字。只用于「栏位类型已经是数字或税号」的情况。
# 不做 S→5、Z→2、V→？：合成税号里这些字母会作为参照出现。
DIGIT_MAP = str.maketrans({
    'O': '0', 'o': '0',
    'I': '1', 'i': '1', 'l': '1',
})
STAR_MAP = str.maketrans({'☆': '*', '★': '*', '＊': '*'})

TAX_LABELS = r'(?:统一社会信用代码|纳税人识别号|纳税识别号|税号)'
TAX_PREFIX = re.compile(
    rf'((?:{TAX_LABELS}\s*[/／]?\s*)+)[:：]?\s*',
)
# 标签后紧跟 15～18 位字母数字（税号中间一般没有空格）
TAX_CODE = re.compile(r'[0-9A-Za-z]{15,18}(?![0-9A-Za-z])')

INV_NO = re.compile(
    r'(发票(?:号码|代码)\s*[:：]?\s*)([0-9A-Za-zOIl]{8,24})(?![0-9A-Za-z])'
)
DATE_FIELD = re.compile(
    r'((?:开票日期|开票时间)\s*[:：]?\s*)([0-9OIloIl年月日./\-－]{6,20})(?![0-9A-Za-z])'
)
# 「银行账号」优先于「账号」，避免开户行名称被吃掉
ACCT_FIELD = re.compile(
    r'((?:银行账号)\s*[:：]?\s*)([0-9OIl][0-9OIl\- ]{7,31})(?![0-9A-Za-z])'
)
PHONE_FIELD = re.compile(
    r'((?:联系电话|电话)\s*[:：]?\s*)([0-9OIl][0-9OIl\-－]{6,19})(?![0-9A-Za-z])'
)
# 独立金额：可选货币符 + 整数部分 + 两位小数。前面不是字母/数字/点/连字符，以免碰到型号。
MONEY = re.compile(
    r'(?<![A-Za-z0-9.\-])((?:[¥￥]\s*)?-?(?:[0-9OIl]{1,3}(?:,[0-9OIl]{3})+|[0-9OIl]+)\.[0-9OIl]{2})(?![A-Za-z0-9.])'
)
PERCENT = re.compile(
    r'(?<![A-Za-z0-9.])([0-9OIl]{1,2}(?:\.[0-9OIl]{1,2})?\s*%)'
)
# 密码区到下一个发票栏位标签为止
PASSWORD_ZONE = re.compile(
    r'(密码区\s*[:：]?\s*)(.+?)(?=项目名称|规格型号|销售方|购买方|价税合计|$)',
    re.S,
)
# 免税 / 不征税 后的掩码
TAXFREE_MASK = re.compile(
    r'(免税|不征税)\s*([☆★*＊]{1,})'
)
# 金额后、长度不少于 2 的纯掩码（合计行税额常写成 ¥6660.00***）
MONEY_MASK = re.compile(
    r'((?:[¥￥]\s*)?-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})\s*([☆★*＊]{2,})'
)

# 发票明细表头，用来确认「金额后掩码」发生在发票表里，而不是招标报价表
INV_TABLE_HINT = re.compile(r'项目名称.{0,20}(规格型号|税率|税额|单价|金额)')


def _map_digits(s: str) -> str:
    return s.translate(DIGIT_MAP)


def _fix_digits_if_needed(s: str) -> Tuple[str, bool]:
    if not re.search(r'[OoIil]', s):
        return s, False
    return _map_digits(s), True


def _append(log: Log, field: str, old: str, new: str, **extra: Any) -> None:
    if old == new:
        return
    rec = {'栏位': field, '原': old, '改': new}
    rec.update(extra)
    log.append(rec)


def _correct_tax_id(text: str, log: Log) -> str:
    out = []
    i = 0
    for m in TAX_PREFIX.finditer(text):
        out.append(text[i:m.end()])
        rest = text[m.end():]
        cm = TAX_CODE.match(rest)
        if not cm:
            i = m.end()
            continue
        code = cm.group(0)
        i = m.end() + cm.end()
        if len(code) == 18:
            new, ok = _fix_digits_if_needed(code)
        elif len(code) == 15 and re.fullmatch(r'[0-9Oo]{15}', code):
            new, ok = _fix_digits_if_needed(code)
        else:
            new, ok = code, False
        if ok and new != code:
            _append(log, '税号', code, new, 位数=len(code))
            out.append(new)
        else:
            out.append(code)
    out.append(text[i:])
    return ''.join(out)


def _correct_password(text: str, log: Log) -> str:
    def repl(m: re.Match) -> str:
        body = m.group(2)
        new = body.translate(STAR_MAP)
        new2, ok = _fix_digits_if_needed(new)
        if ok:
            new = new2
        if new != body:
            _append(log, '密码区', body[:40], new[:40], 长度=len(body))
        return m.group(1) + new
    return PASSWORD_ZONE.sub(repl, text)


def _correct_masks(text: str, log: Log) -> str:
    """仅在发票栏位语境下把掩码里的 ☆★ 收成 *。"""

    def taxfree(m: re.Match) -> str:
        raw = m.group(2)
        new = raw.translate(STAR_MAP)
        if new != raw:
            _append(log, '税额掩码', m.group(1) + raw, m.group(1) + new)
        return m.group(1) + new

    text2 = TAXFREE_MASK.sub(taxfree, text)

    # 金额后的掩码：有发票表头，或本段已出现「免税/不征税」（价税表语境）时才开
    if INV_TABLE_HINT.search(text2) or '免税' in text2 or '不征税' in text2:
        def money_mask(m: re.Match) -> str:
            raw = m.group(2)
            new = raw.translate(STAR_MAP)
            if new != raw:
                _append(log, '税额掩码', m.group(1) + raw, m.group(1) + new)
            return m.group(1) + new
        text2 = MONEY_MASK.sub(money_mask, text2)
    return text2


def _correct_numeric_tokens(text: str, log: Log) -> str:
    def money(m: re.Match) -> str:
        raw = m.group(1)
        new, ok = _fix_digits_if_needed(raw)
        if ok and re.fullmatch(r'[¥￥\s\-]?[0-9,]+\.[0-9]{2}', new):
            _append(log, '金额', raw, new)
            return new
        return raw

    def pct(m: re.Match) -> str:
        raw = m.group(1)
        new, ok = _fix_digits_if_needed(raw)
        if ok and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?\s*%', new):
            _append(log, '税率', raw, new)
            return new
        return raw

    text = MONEY.sub(money, text)
    text = PERCENT.sub(pct, text)
    return text


def correct_text(text: str, is_invoice: Optional[bool] = None) -> Tuple[str, Log]:
    """对一段已识别文本按栏位类型做形近字纠正。返回 (新文本, 改写记录)。

    is_invoice:
      - True / False：仅影响「是否启用发票明细表里的掩码规则」的推断；
        有「免税/不征税」或发票表头时仍会启用掩码规则。
      - None：完全按栏位标签和形态推断（推荐）。
    税号、发票号码、日期、账号、电话、密码区只要标签出现就会处理，
    与是否发票页无关（这些标签本身已经标明栏位类型）。
    """
    if not text:
        return text, []
    log: Log = []
    t = _correct_tax_id(text, log)

    def inv_repl(m: re.Match) -> str:
        raw = m.group(2)
        new, ok = _fix_digits_if_needed(raw)
        if not ok or not re.fullmatch(r'[0-9]+', new):
            return m.group(0)
        _append(log, '发票号码或代码', raw, new)
        return m.group(1) + new

    t = INV_NO.sub(inv_repl, t)

    def date_repl(m: re.Match) -> str:
        raw = m.group(2)
        new, ok = _fix_digits_if_needed(raw)
        if not ok:
            return m.group(0)
        _append(log, '日期', raw, new)
        return m.group(1) + new

    t = DATE_FIELD.sub(date_repl, t)

    def acct_repl(m: re.Match) -> str:
        raw = m.group(2)
        new, ok = _fix_digits_if_needed(raw)
        if not ok or not re.fullmatch(r'[0-9\- ]+', new):
            return m.group(0)
        _append(log, '银行账号', raw, new)
        return m.group(1) + new

    t = ACCT_FIELD.sub(acct_repl, t)

    def phone_repl(m: re.Match) -> str:
        raw = m.group(2)
        new, ok = _fix_digits_if_needed(raw)
        if not ok or not re.fullmatch(r'[0-9\-－]+', new):
            return m.group(0)
        _append(log, '电话', raw, new)
        return m.group(1) + new

    t = PHONE_FIELD.sub(phone_repl, t)
    t = _correct_numeric_tokens(t, log)
    t = _correct_password(t, log)
    # 掩码：有发票表语境，或调用方明确说这是发票
    if is_invoice is False and not (INV_TABLE_HINT.search(t) or '免税' in t or '不征税' in t or '密码区' in t):
        pass
    else:
        t = _correct_masks(t, log)
    return t, log


def correct_pred(
    pred: Dict[Any, Any],
    invoice_pages: Optional[Any] = None,
) -> Tuple[Dict[Any, Any], List[Dict[str, Any]]]:
    """对整份 pred（按页、按 section）做栏位纠正。不改 section 结构，只改 text。

    invoice_pages: 可选的发票页集合（int 或 str）。为 None 时按栏位标签推断；
    传入时仅作为 is_invoice 提示，有标签的非发票页仍会做税号等标签型纠正。
    """
    inv = None
    if invoice_pages is not None:
        inv = {int(p) for p in invoice_pages}
    out = copy.deepcopy(pred)
    all_log: List[Dict[str, Any]] = []
    for p, page in out.items():
        is_inv = None if inv is None else (int(p) in inv)
        for i, sec in enumerate(page.get('sections') or []):
            old = sec.get('text') or ''
            new, log = correct_text(old, is_invoice=is_inv)
            if log:
                sec['text'] = new
                all_log.append({'页': int(p), '段': i, 'section': sec.get('section'), '记录': log})
    return out, all_log


if __name__ == '__main__':
    # 自检：栏位类型规则，不用某一页的整页原文当「特判」。
    samples = [
        ('税号O', '统一社会信用代码/纳税人识别号：12860100MBOLY3726F地址', '12860100MB0LY3726F'),
        ('税号保持S', '纳税人识别号：91970307MA3SX2F58R', '91970307MA3SX2F58R'),
        ('税号保持Z', '纳税人识别号：91980211MA8HZ5N37Q', '91980211MA8HZ5N37Q'),
        ('免税掩码', '2940.00免税☆☆*', '免税***'),
        ('免税掩码带多余字', '免税☆☆*义', '免税***义'),
        ('合计掩码', '项目名称规格型号单位数量单价金额税率/征收率税额\n合计¥6660.00☆☆☆', '¥6660.00***'),
        ('招标星号不动', '说明：标注★者为核心技术参数，标注☆者为可选配置 36,000.00 ★自动清洗', '标注★者为核心技术参数，标注☆者为可选配置 36,000.00 ★自动清洗'),
        ('发票号码', '发票号码：O483O961', '04830961'),
        ('金额', '金额¥1O0.00税款', '¥100.00'),
        ('税率', '13%应是 而误成 l3%', '13%'),
        ('电话后紧接邮箱不动', '受托人电话0676-52841903liumeijin@x.example', '52841903liumeijin'),
        ('型号不动', '*仪器仪表*溶解氧分析仪DO-560 台4', 'DO-560'),
        ('类别星号不动', '*图书*水环境监测', '*图书*'),
    ]
    failed = 0
    for name, src, expect in samples:
        got, log = correct_text(src)
        if expect not in got:
            print('FAIL', name, 'expect', expect, 'got', got, 'log', log)
            failed += 1
        else:
            print('OK', name, log)
    # 招标页不应产生掩码改写
    _, log = correct_text('说明：标注★者为核心技术参数，标注☆者为可选配置 36,000.00 ★自动清洗')
    if log:
        print('FAIL 招标星号被改', log)
        failed += 1
    raise SystemExit(failed)
