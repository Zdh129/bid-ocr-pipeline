# -*- coding: utf-8 -*-
"""方法4：版式结构先验（第5轮试算，纯本地，不调用百度）。

表格几何已知时，约束每个栏位应出现的字符类型（数字、日期、金额、税号、
发票号码、税率等），在词表层面去掉不可能的错字（数字栏 O→0、l/I→1），
并把不合格式的字符标为可疑；几何与格式能唯一恢复时才改写。

本模块可独立引用，按页输入、按页输出，便于之后与其他方法整合。
不读取参照答案，不依赖某一页的具体字串，规则按栏位类型与几何位置编写。

接口
----
apply_page(page, achars=None, is_invoice=None) -> (new_page, log)
    对单页预测做版式先验约束。
    page: 与计分器相同的一页，{'sections': [{'section': str, 'text': str}, ...], ...}
    achars: 可选，该页高精度含位置字框列表，每项 {'ch': str, 'bbox': [x0,y0,x1,y1]}。
            来自已有缓存（如 scheme_a 的 a_chars_for_page），不是现场调用。
    is_invoice: True/False 明确是否发票页；None 时按页内栏位标签推断。
    返回: (新 page（深拷贝，只改 section['text']，不改结构）, 改写/可疑日志)

apply_pred(pred, achars_by_page=None, invoice_pages=None) -> (new_pred, log)
    对整份 pred（{页: page}）逐页调用 apply_page。
    achars_by_page: {页: achars}，页号 int 或 str。
    invoice_pages: 可选的发票页集合。

apply_text(text, achars=None, is_invoice=None) -> (new_text, log)
    对一段已识别文本做同样约束，供单测或按段调用。

日志条目（字典）常见键：
    规则, 栏位, 原, 改, 可疑, 依据
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

Log = List[Dict[str, Any]]
BBox = Sequence[float]
AChar = Dict[str, Any]

DIGIT_MAP = str.maketrans({
    'O': '0', 'o': '0',
    'I': '1', 'i': '1', 'l': '1',
})

# 标准表头集合（最长标签优先匹配）。顺序为版式先验的缺省从左到右顺序。
HEADER_SETS: List[Tuple[str, List[str]]] = [
    ('发票明细', ['项目名称', '规格型号', '单位', '数量', '单价', '金额', '税率/征收率', '税额']),
    ('销货清单', ['序号', '货物（劳务）名称', '规格型号', '单位', '数量', '单价', '金额', '税率', '税额']),
    ('销货清单简', ['序号', '货物名称', '规格型号', '单位', '数量', '单价', '金额', '税率', '税额']),
    ('合同设备', ['序号', '设备名称', '规格型号', '单位', '数量', '单价（元）', '金额（元）']),
]

# 数字型栏（用于几何分栏后的字符集约束）
NUMERIC_COLS = {
    '数量', '单价', '金额', '税额', '税率', '税率/征收率',
    '单价（元）', '金额（元）', '序号',
}

# 金额：整数部分 1～12 位，两位小数。允许前缀 ¥/￥ 与千分位逗号。
MONEY_RE = re.compile(r'(?:¥|￥)?\d{1,3}(?:,\d{3})*(?:\.\d{2})|(?:¥|￥)?\d+\.\d{2}')
MONEY_CORE_RE = re.compile(r'(?:¥|￥)?(\d{1,12})\.(\d{2})')
# 同一记号里出现两个小数点：典型的「金额被后续数字粘住」
MALFORMED_MONEY_RE = re.compile(r'\d+\.\d+\.\d+')

# 增值税税率词表（含历史税率）。只用于「已判定为税率栏」或金额-税率-税额三元组恢复。
RATE_PCT_RE = re.compile(r'(?:0|1|2|3|4|5|6|9|10|11|13|16|17)(?:\.\d{1,2})?%')
RATE_NUMS = frozenset(
    '0 1 2 3 4 5 6 9 10 11 13 16 17 0.00 1.00 3.00 6.00 9.00 13.00'.split()
)

SEAL_WORD_RE = re.compile(r'发票专用章?')
SEP_RE = re.compile(r'[\s|丨│、，,./]*')

INV_HINT_RE = re.compile(
    r'发票号码|发票代码|价税合计|开票日期|增值税(?:专用|普通)?发票|电子发票'
)

# 数字栏允许的字符（映射后再检查）
DIGIT_FIELD_OK = set('0123456789.,%¥￥-－ ')


def _append(log: Log, **kw: Any) -> None:
    log.append({k: v for k, v in kw.items() if v is not None})


def _map_digits(s: str) -> str:
    return s.translate(DIGIT_MAP)


def _is_money(s: str) -> bool:
    s = (s or '').strip()
    if not s:
        return False
    t = s.replace(',', '').replace('¥', '').replace('￥', '')
    return bool(re.fullmatch(r'\d+\.\d{2}', t))


def _is_rate(s: str) -> bool:
    s = (s or '').strip()
    if RATE_PCT_RE.fullmatch(s):
        return True
    if s.endswith('%') and s[:-1] in RATE_NUMS:
        return True
    return s in RATE_NUMS


def _with_pct(rate: str) -> str:
    rate = rate.strip()
    return rate if rate.endswith('%') else rate + '%'


def _looks_invoice(text: str, is_invoice: Optional[bool]) -> bool:
    if is_invoice is True:
        return True
    if is_invoice is False:
        return bool(INV_HINT_RE.search(text or ''))
    return bool(INV_HINT_RE.search(text or ''))


# ---------------------------------------------------------------------------
# 几何：把字框聚成行，定位表头栏位的 x 带
# ---------------------------------------------------------------------------
def cluster_lines(achars: Optional[Sequence[AChar]], ytol: float = 4.0) -> List[Dict[str, Any]]:
    """把 achars 按 y 聚成行，行内按 x 排序。"""
    if not achars:
        return []
    items = sorted(
        achars,
        key=lambda c: (((c['bbox'][1] + c['bbox'][3]) / 2), c['bbox'][0]),
    )
    lines: List[Dict[str, Any]] = []
    for c in items:
        y = (c['bbox'][1] + c['bbox'][3]) / 2
        if not lines or abs(y - lines[-1]['y']) > ytol:
            lines.append({'y': y, 'chars': [c]})
        else:
            lines[-1]['chars'].append(c)
            n = len(lines[-1]['chars'])
            lines[-1]['y'] = (lines[-1]['y'] * (n - 1) + y) / n
    for ln in lines:
        ln['chars'].sort(key=lambda c: c['bbox'][0])
        ln['text'] = ''.join(c['ch'] for c in ln['chars'])
        ln['x0'] = ln['chars'][0]['bbox'][0]
        ln['x1'] = ln['chars'][-1]['bbox'][2]
    return lines


def _label_xs_on_line(ln: Dict[str, Any], labels: Sequence[str]) -> Optional[List[Tuple[float, str]]]:
    t = ln['text']
    if not all(lab in t for lab in labels):
        return None
    xs: List[Tuple[float, str]] = []
    used = [False] * len(t)
    for lab in sorted(labels, key=len, reverse=True):
        start = 0
        found = False
        while True:
            i = t.find(lab, start)
            if i < 0:
                break
            if not any(used[i:i + len(lab)]):
                for k in range(i, i + len(lab)):
                    used[k] = True
                x0 = ln['chars'][i]['bbox'][0]
                xs.append((x0, lab))
                found = True
                break
            start = i + 1
        if not found:
            return None
    xs.sort()
    return xs


def header_order_from_achars(achars: Optional[Sequence[AChar]], labels: Sequence[str]) -> Optional[List[str]]:
    """在字框行里找到同一套表头标签，按 x 从左到右排序。找不到返回 None。"""
    for ln in cluster_lines(achars):
        xs = _label_xs_on_line(ln, labels)
        if xs:
            return [lab for _, lab in xs]
    return None


def column_bands(achars: Optional[Sequence[AChar]], labels: Optional[Sequence[str]] = None) -> Dict[str, Tuple[float, float]]:
    """表头各栏的 x 区间（相邻标签中点分界）。"""
    lines = cluster_lines(achars)
    if not lines:
        return {}
    chosen = None
    if labels:
        for ln in lines:
            xs = _label_xs_on_line(ln, labels)
            if xs:
                chosen = xs
                break
    if chosen is None:
        for _name, labs in HEADER_SETS:
            for ln in lines:
                xs = _label_xs_on_line(ln, labs)
                if xs:
                    chosen = xs
                    break
            if chosen is not None:
                break
    if not chosen:
        return {}
    bands: Dict[str, Tuple[float, float]] = {}
    for i, (x, lab) in enumerate(chosen):
        left = (chosen[i - 1][0] + x) / 2 if i else x - 40
        right = (x + chosen[i + 1][0]) / 2 if i + 1 < len(chosen) else x + 80
        bands[lab] = (left, right)
    return bands


def _chars_in_band(ln: Dict[str, Any], band: Tuple[float, float]) -> str:
    x0, x1 = band
    out = []
    for c in ln['chars']:
        cx = (c['bbox'][0] + c['bbox'][2]) / 2
        if x0 <= cx <= x1:
            out.append(c['ch'])
    return ''.join(out)


# ---------------------------------------------------------------------------
# 1. 表头标签按几何（或标准顺序）复位
# ---------------------------------------------------------------------------
def _find_header_span(line: str, labels: Sequence[str]) -> Optional[Tuple[int, int, List[str]]]:
    """若某行的一段只由给定标签加分隔符组成，返回 (start, end, 出现顺序)。"""
    used = [False] * len(line)
    found: List[Tuple[int, int, str]] = []
    for lab in sorted(labels, key=len, reverse=True):
        start = 0
        hit = False
        while True:
            i = line.find(lab, start)
            if i < 0:
                break
            if not any(used[i:i + len(lab)]):
                for k in range(i, i + len(lab)):
                    used[k] = True
                found.append((i, i + len(lab), lab))
                hit = True
                break
            start = i + 1
        if not hit:
            return None
    if {f[2] for f in found} != set(labels):
        return None
    found.sort()
    span_s, span_e = found[0][0], found[-1][1]
    pos = span_s
    for a, b, _lab in found:
        if not SEP_RE.fullmatch(line[pos:a] or ''):
            return None
        pos = b
    if not SEP_RE.fullmatch(line[pos:span_e] or ''):
        return None
    return span_s, span_e, [lab for _, _, lab in found]


def _join_labels(labels: Sequence[str], orig_span: str) -> str:
    """尽量沿用原片段的分隔风格。"""
    if ' ' in orig_span or '\t' in orig_span:
        return ' '.join(labels)
    if '|' in orig_span:
        return '|'.join(labels)
    return ''.join(labels)


def fix_headers(text: str, achars: Optional[Sequence[AChar]], log: Log) -> str:
    out_lines = []
    for line in text.split('\n'):
        changed = False
        for _name, labels in HEADER_SETS:
            span = _find_header_span(line, labels)
            if not span:
                continue
            s, e, order = span
            geo = header_order_from_achars(achars, labels)
            target = geo or list(labels)
            if order == target:
                break
            new_span = _join_labels(target, line[s:e])
            new_line = line[:s] + new_span + line[e:]
            _append(
                log, 规则='表头按几何复位', 栏位='表头',
                原=line[s:e], 改=new_span,
                依据='字框x序' if geo else '标准栏序',
            )
            line = new_line
            changed = True
            break
        out_lines.append(line)
        del changed
    return '\n'.join(out_lines)


# ---------------------------------------------------------------------------
# 2. 印章文字混入非印章栏：标可疑并去掉
# ---------------------------------------------------------------------------
def fix_seal_in_typed_fields(text: str, log: Log) -> str:
    def party_repl(m: re.Match) -> str:
        _append(log, 规则='栏位去印章字', 栏位=m.group(1), 原=m.group(0), 改=m.group(1) + ' ' + m.group(2))
        return m.group(1) + ' ' + m.group(2)

    t = re.sub(
        r'((?:销售方|购买方)(?:信息)?)\s*发票专用章\s*(名称)',
        party_repl,
        text,
    )

    def remark_repl(m: re.Match) -> str:
        _append(log, 规则='栏位去印章字', 栏位='备注', 原=m.group(0), 改=m.group(1))
        return m.group(1)

    # 备注行末粘上的「发票专用章」（单独一行的印章文字不动）
    t = re.sub(r'(备注[^\n]*?)\s*发票专用章\s*$', remark_repl, t, flags=re.M)
    return t


# ---------------------------------------------------------------------------
# 3. 价税合计栏格式：大写金额之后应为（小写）¥…
# ---------------------------------------------------------------------------
def fix_total_in_words(text: str, log: Log) -> str:
    out = []
    for line in text.split('\n'):
        if '价税合计' not in line:
            out.append(line)
            continue
        m = re.search(r'^(.*)([¥￥]\d[\d,]*\.\d{2}(?:\s.*)?)$', line)
        if not m:
            out.append(line)
            continue
        left, money = m.group(1), m.group(2)
        m2 = re.search(r'(.*)(元|角|分|整)(.*)$', left)
        if not m2:
            out.append(line)
            continue
        prefix, unit, mid = m2.group(1), m2.group(2), m2.group(3)
        mid_n = mid.strip()
        if mid_n in ('(小写)', '（小写）', '(小写)', '（小写）'):
            out.append(line)
            continue
        if mid_n == '':
            out.append(line)
            continue
        if '发票专用' in mid or re.search(r'写\s*[）)]', mid):
            new = prefix + unit + '(小写)' + money
            _append(log, 规则='价税合计格式', 栏位='价税合计', 原=line, 改=new)
            out.append(new)
        else:
            _append(log, 规则='价税合计格式', 栏位='价税合计', 可疑=mid_n, 依据='大写与小写金额之间夹了不合格式的字')
            out.append(line)
    return '\n'.join(out)


# ---------------------------------------------------------------------------
# 4. 机器编号等标签后的格式（缺冒号则补）
# ---------------------------------------------------------------------------
LABEL_COLON_FIELDS = (
    ('机器编号', r'[A-Za-z0-9]{6,}'),
    ('校验码', r'[A-Za-z0-9]{4,}'),
)


def fix_labeled_colon(text: str, log: Log) -> str:
    t = text
    for name, val_pat in LABEL_COLON_FIELDS:
        pat = re.compile(rf'({name})(\s*)({val_pat})')

        def repl(m: re.Match, name=name) -> str:
            # 已有冒号则不动
            return m.group(0)

        # 只匹配「标签与值之间没有冒号」
        pat2 = re.compile(rf'({name})(?![:：])(\s*)({val_pat})')

        def repl2(m: re.Match, name=name) -> str:
            new = f'{m.group(1)}:{m.group(3)}'
            _append(log, 规则='栏位标点格式', 栏位=name, 原=m.group(0), 改=new)
            return new

        t = pat2.sub(repl2, t)
    return t


# ---------------------------------------------------------------------------
# 5. 明细行金额 / 税率 / 税额：用栏位类型词表恢复被粘连或污染的格子
# ---------------------------------------------------------------------------
def _extract_monies(s: str) -> List[str]:
    return MONEY_CORE_RE.findall(s) and re.findall(r'(?:¥|￥)?\d+\.\d{2}', s.replace(',', '')) or re.findall(r'(?:¥|￥)?\d+\.\d{2}', s)


def _first_money_prefix(s: str) -> Optional[Tuple[str, str]]:
    """从开头取一个合法金额，返回 (金额, 剩余)。"""
    m = re.match(r'\s*((?:¥|￥)?\d+\.\d{2})', s)
    if m:
        return m.group(1), s[m.end():]
    return None


def _last_money_suffix(s: str) -> Optional[Tuple[str, str]]:
    m = re.search(r'((?:¥|￥)?\d+\.\d{2})\s*$', s)
    if m:
        return s[:m.start()], m.group(1)
    return None


def _rate_from_leftover(leftover: str, a_hint: Optional[str]) -> Optional[str]:
    """从残留串或几何栏提示里取唯一合理的税率。"""
    if a_hint:
        hm = RATE_PCT_RE.search(_map_digits(a_hint))
        if hm:
            return hm.group(0)
        # 几何栏末尾是词表内数字且紧邻 %
        hm2 = re.search(r'(?:0|1|2|3|4|5|6|9|10|11|13|16|17)%', _map_digits(a_hint))
        if hm2:
            return hm2.group(0)
    # 残留里已有带 % 的税率
    ms = list(RATE_PCT_RE.finditer(leftover))
    if len(ms) == 1:
        return ms[0].group(0)
    if len(ms) > 1:
        return ms[-1].group(0)
    # 残留末尾是词表内税率数字（金额粘连后税率常被挤在末尾）
    m = re.search(r'(\d{1,2}(?:\.\d{1,2})?)$', leftover.strip())
    if m and m.group(1) in RATE_NUMS:
        return _with_pct(m.group(1))
    return None


def _a_rate_hint(line: str, a_lines: List[Dict[str, Any]], bands: Dict[str, Tuple[float, float]]) -> Optional[str]:
    band = bands.get('税率/征收率') or bands.get('税率')
    if not band or not a_lines:
        return None
    # 用行里一个稳定锚（详见、合计、或第一个金额）找对应字框行
    keys = []
    if '详见' in line:
        keys.append('详见')
    hm = re.search(r'\d{4,}', line)
    if hm:
        keys.append(hm.group(0)[:6])
    best = None
    for ln in a_lines:
        if any(k and k in ln['text'] for k in keys):
            if '价税合计' in ln['text']:
                continue
            best = ln
            break
    if best is None:
        return None
    return _chars_in_band(best, band)


def _recover_amount_rate_tax(payload: str, a_hint: Optional[str], log: Log, line_orig: str) -> Optional[str]:
    """把污染后的「金额+税率+税额」恢复成三个合法栏位。无法唯一确定则返回 None。"""
    raw = payload
    # 先去掉印章字（数字栏不应出现）
    if SEAL_WORD_RE.search(payload):
        _append(log, 规则='数字栏去印章字', 栏位='金额/税率/税额', 可疑=SEAL_WORD_RE.search(payload).group(0))
        payload = SEAL_WORD_RE.sub('', payload)
    payload = payload.strip()
    # 已经合法：金额 税率% 税额
    good = re.fullmatch(
        r'\s*((?:¥|￥)?\d+\.\d{2})\s*(\d{1,2}(?:\.\d{1,2})?%)\s*((?:¥|￥)?\d+\.\d{2})\s*',
        payload,
    )
    if good:
        if raw != payload:
            return f'{good.group(1)} {good.group(2)} {good.group(3)}'
        return None  # 无需改

    # 需要恢复：出现双小数点、印章残留、或拆不出三个合法栏
    if not (MALFORMED_MONEY_RE.search(raw) or SEAL_WORD_RE.search(raw) or not good):
        return None

    head = _first_money_prefix(payload)
    tail = _last_money_suffix(payload)
    if not head or not tail:
        # 标可疑后放弃
        if MALFORMED_MONEY_RE.search(raw):
            _append(log, 规则='金额格式', 栏位='金额', 可疑=raw, 依据='同一记号出现多个小数点，无法唯一拆分')
        return None
    amt, rest1 = head
    # tail 基于原 payload；若开头金额与末尾金额重叠则失败
    mid_and = payload
    left, tax = tail
    # leftover 应是开头金额之后、末尾金额之前
    if not payload.startswith(amt) or not payload.endswith(tax.strip() if False else tax):
        pass
    leftover = payload[len(amt):]
    if leftover.endswith(tax):
        leftover = leftover[:len(leftover) - len(tax)]
    else:
        return None
    if amt == tax and leftover.strip() == '':
        return None
    leftover_s = leftover.strip()
    # 若中间还藏着另一个两位小数金额，说明这更像「单价+金额+税率+税额」粘连，不是「金额+垃圾+税额」
    mid_monies = re.findall(r'(?:¥|￥)?\d+\.\d{2}(?!\d)', leftover_s)
    if mid_monies:
        _append(log, 规则='金额格式', 栏位='金额', 可疑=raw.strip(), 依据='中间仍有合法金额，可能是单价与金额粘连，不拆')
        return None
    rate = _rate_from_leftover(leftover_s, a_hint)
    # 残留里除税率和不可能属于金额/税率的碎片外应被丢掉
    if rate is None:
        _append(log, 规则='税率格式', 栏位='税率', 可疑=leftover_s, 依据='金额与税额之间的残留无法唯一对应税率词表')
        return None
    new = f'{amt} {rate} {tax}'
    if new.replace(' ', '') == raw.replace(' ', ''):
        return None
    _append(
        log, 规则='金额税率税额分栏恢复', 栏位='金额/税率/税额',
        原=raw.strip(), 改=new, 依据='栏位类型词表+几何税率栏' if a_hint else '栏位类型词表',
    )
    return new


def _split_jianhuo_payload(line: str) -> Optional[Tuple[str, str, str]]:
    """仅处理「（详见销货清单）」这类单行明细：中间数量/单价栏为空，载荷应为 金额+税率+税额。

    返回 (前缀含详见括号, 数字载荷, 行尾其它栏如合计)。一般明细行的单价可能是两位或更多位小数，
    再与金额粘连后也会出现多个小数点，不能当成「坏金额」去拆，否则会丢掉合法的单价或金额。
    """
    m = re.search(r'([（(]详见[^)）]{1,20}[)）])\s*(.*)$', line)
    if not m or not re.search(r'\d', m.group(2) or ''):
        return None
    prefix, rest = m.group(1), m.group(2)
    # 同行后面若已写出「合计…」，必须留在行尾，不能并进金额栏
    m2 = re.search(r'(?<![税价])(合计)\s*(?![（(])', rest)
    if m2:
        return prefix, rest[:m2.start()], rest[m2.start():]
    return prefix, rest, ''


def _item_triplet_ok(payload: str) -> bool:
    """载荷是否已经是「金额 + 税率% + 税额」（可粘连、可空格）。"""
    return bool(re.fullmatch(
        r'\s*((?:¥|￥)?\d+\.\d{2})\s*(\d{1,2}(?:\.\d{1,2})?%)\s*((?:¥|￥)?\d+\.\d{2})\s*',
        payload or '',
    ))


def fix_amount_columns(text: str, achars: Optional[Sequence[AChar]], log: Log) -> str:
    a_lines = cluster_lines(achars)
    bands = column_bands(achars)
    out = []
    for line in text.split('\n'):
        if '价税合计' in line or '开票日期' in line:
            out.append(line)
            continue
        parts = _split_jianhuo_payload(line)
        if not parts:
            out.append(line)
            continue
        name, payload, tail = parts
        if _item_triplet_ok(payload):
            out.append(line)
            continue
        hint = _a_rate_hint(line, a_lines, bands)
        rec = _recover_amount_rate_tax(payload, hint, log, line)
        if rec is None:
            out.append(line)
        else:
            sep = ' ' if (line[len(name):].startswith(' ') or (name and not name.endswith(' '))) else ''
            out.append(name + sep + rec + tail)
    return '\n'.join(out)


# ---------------------------------------------------------------------------
# 6. 合计行：第二金额不合格式时，用几何栏内合法金额补回
# ---------------------------------------------------------------------------
def _heji_a_monies(a_lines: List[Dict[str, Any]], bands: Dict[str, Tuple[float, float]]) -> Optional[List[str]]:
    for ln in a_lines:
        if '合计' in ln['text'] and '价税合计' not in ln['text']:
            monies: List[str] = []
            # 优先按金额/税额栏取
            for col in ('金额', '金额（元）', '税额'):
                if col in bands:
                    raw = _map_digits(_chars_in_band(ln, bands[col]))
                    ms = re.findall(r'(?:¥|￥)?\d+\.\d{2}', raw)
                    monies.extend(ms)
            if len(monies) >= 2:
                return monies[:2]
            ms = re.findall(r'(?:¥|￥)?\d+\.\d{2}', _map_digits(ln['text']))
            if ms:
                return ms
    return None


def _norm_money_num(s: str) -> str:
    return re.sub(r'[¥￥,]', '', s)


def fix_heji_line(text: str, achars: Optional[Sequence[AChar]], log: Log) -> str:
    a_lines = cluster_lines(achars)
    bands = column_bands(achars)
    a_ms = _heji_a_monies(a_lines, bands)

    def repl(m: re.Match) -> str:
        prefix, rest = m.group(1), m.group(2)
        # 不处理「合计（含税）」等合同表述（前缀已用负向预查）
        monies = re.findall(r'(?:¥|￥)?\d+\.\d{2}', rest)
        # 残骸：合法金额之后还挂着 ¥?\d+（无小数，过短）
        leftover_bad = re.search(r'((?:¥|￥)\d{1,3})$', rest.strip())
        second_ok = len(monies) >= 2 and _is_money(monies[1])
        first_ok = len(monies) >= 1 and _is_money(monies[0])
        if first_ok and second_ok and not leftover_bad:
            return m.group(0)
        if not a_ms or len(a_ms) < 2:
            if first_ok and not second_ok:
                _append(log, 规则='合计金额格式', 栏位='合计', 可疑=rest, 依据='第二金额不合金额格式，且几何栏无法提供合法值')
            return m.group(0)
        # 第一个金额若已合法，须与几何栏第一个一致（或数值相同）才采用几何第二金额，避免误用别的行
        a0, a1 = a_ms[0], a_ms[1]
        if first_ok and _norm_money_num(monies[0]) != _norm_money_num(a0):
            _append(log, 规则='合计金额格式', 栏位='合计', 可疑=rest, 依据='几何栏合计金额与已识别第一金额不一致，不改')
            return m.group(0)
        # 保留原前缀和原第一金额的货币符号风格，补第二金额
        if first_ok:
            first = monies[0]
        else:
            first = a0 if _is_money(a0) else None
            if not first:
                return m.group(0)
        if not _is_money(a1):
            return m.group(0)
        # 货币符号跟原文走，不用字框里的 ￥ 去替换已识别的 ¥
        style = rest + (first or '')
        if '¥' in style:
            sym = '¥'
        elif '￥' in style:
            sym = '￥'
        else:
            sym = ''
        second = sym + _norm_money_num(a1)
        if leftover_bad:
            new_rest = re.sub(r'(?:¥|￥)\d{1,3}\s*$', second, rest)
        else:
            sep = '' if re.search(r'\.\d{2}[¥￥]', rest) else ' '
            new_rest = first + sep + second
        new = prefix + new_rest
        if new != m.group(0):
            _append(log, 规则='合计金额按几何补全', 栏位='合计', 原=m.group(0), 改=new, 依据='合计税额栏几何字框为合法金额')
        return new

    # 空白留在前缀里，避免把「合计 ¥…」写成「合计¥…」。排除「价税合计」「合计（」
    return re.sub(r'(?<![税价])(合计\s*)(?![（(])([^\n]*)', repl, text)


# ---------------------------------------------------------------------------
# 7. 数字型栏位的形近字：O/o→0、I/i/l→1（词表约束，不改型号/税号字母）
# ---------------------------------------------------------------------------
# 独立数字记号：前不是字母数字（避免 PH-880、DO-560、税号里的字母）
NUM_TOKEN_RE = re.compile(
    r'(?<![A-Za-z0-9])((?:¥|￥)?[0-9OolIiIl]+(?:[.,][0-9OolIiIl]+)*%?)(?![A-Za-z0-9])'
)


def fix_digit_lookalikes(text: str, log: Log) -> str:
    def repl(m: re.Match) -> str:
        raw = m.group(1)
        new = _map_digits(raw)
        if new == raw:
            return m.group(0)
        core = new.replace(',', '').replace('¥', '').replace('￥', '')
        ok = bool(
            re.fullmatch(r'\d+\.\d{2}', core)
            or re.fullmatch(r'\d+%', core)
            or re.fullmatch(r'\d{1,12}', core)
        )
        if not ok:
            _append(log, 规则='数字栏形近字', 栏位='数字', 可疑=raw, 依据='映射后仍不合数字/金额/税率格式，不改')
            return m.group(0)
        _append(log, 规则='数字栏形近字', 栏位='数字', 原=raw, 改=new)
        return new

    return NUM_TOKEN_RE.sub(repl, text)


def fix_labeled_digit_fields(text: str, log: Log) -> str:
    """带标签的发票号码、代码、日期、账号、电话：数字栏形近字。税号只改 O/I/l。"""
    def map_keep(field: str, prefix: str, raw: str, ok_re: str) -> str:
        new = _map_digits(raw)
        if new == raw:
            return prefix + raw
        if not re.fullmatch(ok_re, new):
            _append(log, 规则='数字栏形近字', 栏位=field, 可疑=raw)
            return prefix + raw
        _append(log, 规则='数字栏形近字', 栏位=field, 原=raw, 改=new)
        return prefix + new

    def inv_repl(m: re.Match) -> str:
        return map_keep('发票号码或代码', m.group(1), m.group(2), r'\d{8,24}')

    t = re.sub(r'(发票(?:号码|代码)\s*[:：]?\s*)([0-9A-Za-zOIl]{8,24})(?![0-9A-Za-z])', inv_repl, text)

    def date_repl(m: re.Match) -> str:
        raw = m.group(2)
        new = _map_digits(raw)
        if new == raw:
            return m.group(0)
        if not re.search(r'\d', new):
            return m.group(0)
        _append(log, 规则='数字栏形近字', 栏位='日期', 原=raw, 改=new)
        return m.group(1) + new

    t = re.sub(
        r'((?:开票日期|开票时间)\s*[:：]?\s*)([0-9OIloIl年月日./\-－]{6,20})(?![0-9A-Za-z])',
        date_repl,
        t,
    )

    # 税号 15～18 位：只改 O/I/l，保留合法字母（S/Z 等）
    def tax_repl(m: re.Match) -> str:
        raw = m.group(2)
        new = _map_digits(raw)
        if new == raw:
            return m.group(0)
        _append(log, 规则='数字栏形近字', 栏位='税号', 原=raw, 改=new)
        return m.group(1) + new

    t = re.sub(
        r'((?:统一社会信用代码|纳税人识别号|纳税识别号|税号)\s*[/／]?\s*[:：]?\s*)([0-9A-Za-zOIl]{15,18})(?![0-9A-Za-z])',
        tax_repl,
        t,
    )
    return t


# ---------------------------------------------------------------------------
# 8. 扫描并记录仍不合格式的数字栏（只标可疑，不改）
# ---------------------------------------------------------------------------
def mark_suspicious(text: str, log: Log) -> None:
    for m in MALFORMED_MONEY_RE.finditer(text):
        s = m.group(0)
        # 末尾可能还粘着税率数字（如 13 或 13%）
        core = re.sub(r'(?<=\d\.\d{2})(?:0|1|2|3|4|5|6|9|10|11|13|16|17)(?:\.\d{1,2})?%?$', '', s)
        # 两个两位小数粘连，或「多位小数单价 + 两位金额」粘连：常见排版，不标可疑
        if re.fullmatch(r'\d+\.\d{2}\d+\.\d{2}', core):
            continue
        if re.fullmatch(r'\d+\.\d{3,8}\d+\.\d{2}', core):
            continue
        _append(log, 规则='金额格式', 栏位='金额', 可疑=s, 依据='同一记号出现多个小数点，且不像单价与金额粘连')
    # 税率栏里出现字母（已排除型号）
    for m in re.finditer(r'(税率(?:/征收率)?\s*[:：]?\s*)([^\s]{1,12})', text):
        val = m.group(2)
        if val in ('税额',):
            continue
        if not (_is_rate(val) or val.startswith('税额')):
            if re.search(r'[A-Za-z]', val):
                _append(log, 规则='税率格式', 栏位='税率', 可疑=val)


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def apply_text(
    text: str,
    achars: Optional[Sequence[AChar]] = None,
    is_invoice: Optional[bool] = None,
) -> Tuple[str, Log]:
    """对一段已识别文本按栏位类型与表格几何做约束。返回 (新文本, 日志)。"""
    if not text:
        return text, []
    log: Log = []
    t = text
    t = fix_headers(t, achars, log)
    t = fix_seal_in_typed_fields(t, log)
    if _looks_invoice(t, is_invoice):
        t = fix_total_in_words(t, log)
        t = fix_labeled_colon(t, log)
        t = fix_amount_columns(t, achars, log)
        t = fix_heji_line(t, achars, log)
    t = fix_labeled_digit_fields(t, log)
    t = fix_digit_lookalikes(t, log)
    mark_suspicious(t, log)
    return t, log


def apply_page(
    page: Dict[str, Any],
    achars: Optional[Sequence[AChar]] = None,
    is_invoice: Optional[bool] = None,
) -> Tuple[Dict[str, Any], Log]:
    """按页输入、按页输出。只改 sections[i]['text']，不改段结构和其他键。"""
    out = copy.deepcopy(page)
    all_log: Log = []
    sections = out.get('sections') or []
    page_text = ''.join(s.get('text') or '' for s in sections)
    inv = is_invoice if is_invoice is not None else _looks_invoice(page_text, None)
    for i, sec in enumerate(sections):
        old = sec.get('text') or ''
        new, log = apply_text(old, achars=achars, is_invoice=inv)
        if new != old:
            sec['text'] = new
        if log:
            rec = {'段': i, 'section': sec.get('section'), '记录': log}
            all_log.append(rec)
    return out, all_log


def apply_pred(
    pred: Dict[Any, Any],
    achars_by_page: Optional[Dict[Any, Sequence[AChar]]] = None,
    invoice_pages: Optional[Iterable[Any]] = None,
) -> Tuple[Dict[Any, Any], List[Dict[str, Any]]]:
    """对整份 pred（{页: page}）逐页约束。返回 (新 pred, 日志)。"""
    inv = {int(p) for p in invoice_pages} if invoice_pages is not None else None
    achars_by_page = achars_by_page or {}

    def _achars(p: Any) -> Optional[Sequence[AChar]]:
        if p in achars_by_page:
            return achars_by_page[p]
        if str(p) in achars_by_page:
            return achars_by_page[str(p)]
        try:
            if int(p) in achars_by_page:
                return achars_by_page[int(p)]
        except (TypeError, ValueError):
            pass
        return None

    out = copy.deepcopy(pred)
    all_log: List[Dict[str, Any]] = []
    for p, page in out.items():
        is_inv = None if inv is None else (int(p) in inv)
        new_page, log = apply_page(page, achars=_achars(p), is_invoice=is_inv)
        out[p] = new_page
        for rec in log:
            all_log.append({'页': int(p), **rec})
    return out, all_log


# ---------------------------------------------------------------------------
# 自检：只用类型化短例，不用某一测试集的整页原文
# ---------------------------------------------------------------------------
def _self_check() -> int:
    failed = 0

    def ok(name: str, cond: bool, detail: str = '') -> None:
        nonlocal failed
        if cond:
            print('OK', name)
        else:
            print('FAIL', name, detail)
            failed += 1

    # 表头乱序（标准栏序，无字框时）
    src = '规格型号数量项目名称 单位 单价 金额 税率/征收率 税额'
    got, log = apply_text(src, achars=None, is_invoice=True)
    ok('表头复位', '项目名称' in got and got.find('项目名称') < got.find('规格型号') < got.find('单位') < got.find('数量'), got)

    # 字框 x 序与标准一致时，应按 x 复位
    ach = []
    x = 10.0
    for ch in '项目名称规格型号单位数量单价金额税率/征收率税额':
        ach.append({'ch': ch, 'bbox': [x, 20, x + 8, 28]})
        x += 9
    got, log = apply_text(src, achars=ach, is_invoice=True)
    ok('表头按字框x序', got.startswith('项目名称'), got)

    # 数字栏形近字；型号不动
    got, log = apply_text('金额¥1O0.00 型号DO-560 税率l3%', is_invoice=True)
    ok('金额O→0', '¥100.00' in got, got)
    ok('型号不动', 'DO-560' in got, got)
    ok('税率l→1', '13%' in got, got)

    # 金额粘连 + 印章字 + 缺 %：应拆出金额、税率、税额并丢掉中间碎片
    src = '(详见销货清单) 4967507.2275775.95413发票专用章 645775.95'
    got, log = apply_text(src, is_invoice=True)
    ok('粘连金额恢复', '4967507.22' in got and '13%' in got and '645775.95' in got and '75775' not in got and '发票专用章' not in got, got)

    # 销售方栏混入印章
    got, log = apply_text('销售方 发票专用章名称：某某公司', is_invoice=True)
    ok('购销方去章', '销售方 名称：某某公司' == got or '销售方 名称：某某公司' in got, got)

    # 价税合计：印章把「(小写)」挤坏
    src = '价税合计（大写）⊗壹元整发票专用写)¥1.00'
    got, log = apply_text(src, is_invoice=True)
    ok('价税合计小写恢复', '(小写)¥1.00' in got and '发票专用' not in got, got)

    # 机器编号缺冒号
    got, log = apply_text('机器编号JNJ6GAJB6Z22', is_invoice=True)
    ok('机器编号冒号', got == '机器编号:JNJ6GAJB6Z22', got)

    # 按页接口不改结构
    page = {'sections': [{'section': 'table', 'text': '机器编号ABC12345'}], 'seal': 'X'}
    newp, log = apply_page(page, is_invoice=True)
    ok('按页保留seal', newp.get('seal') == 'X' and newp['sections'][0]['section'] == 'table', str(newp))

    # 一般明细：单价与金额粘连（两个两位小数）不得当成坏金额去拆
    src = '*设备*阀门 ZD-40 台 1 7830.427830.42 13%1017.95'
    got, log = apply_text(src, is_invoice=True)
    ok('单价金额粘连不拆', '7830.427830.42' in got, got)

    # 多位小数单价与金额粘连不得拆掉金额
    src = '套 3 33020.8230088599062.47 13% 12878.12'
    got, log = apply_text(src, is_invoice=True)
    ok('多位单价不拆', '33020.8230088599062.47' in got and '12878.12' in got, got)

    # 详见清单与合计写在同一行：合计必须保留
    src = '(详见销货清单)12687596.5113%1649387.54合计¥12687596.51¥1649387.54'
    got, log = apply_text(src, is_invoice=True)
    ok('同行合计保留', '合计¥12687596.51¥1649387.54' in got and '12687596.51' in got and '13%' in got, got)

    return failed


if __name__ == '__main__':
    raise SystemExit(_self_check())
