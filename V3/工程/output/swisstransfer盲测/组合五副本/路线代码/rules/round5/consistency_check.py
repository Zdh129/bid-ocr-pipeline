"""第5轮 方法2：上下文一致性校验（发票页，按页输入、按页输出；纯本地，不读参照，不按页码/文件名）。

【接口】
    check_page(page, alts=(), reread=None) -> (new_page, log)
      page   : 一页预测 {'sections': [{'section': 'body'|'table', 'text': str}, ...], 'seal': str}（与 pred_R3.json['pred'][页] 同格式）
      alts   : 另几路同页结果的纯文本列表（按优先级排；如颜色去章图高精度的物理行文本、方案E/基线C 该页文本、原图高精度物理行文本）
      reread : 可选回调 reread(page, what) -> str|None，what 为 {'类型': '合计行'|'明细行'|'价税合计', '提示': 原文片段}；
               返回裁切放大重识别的那一行文字。没有就传 None（只用 alts）。
      返回   : new_page（深拷贝；没改就与输入相同）、log（列表：每条校验失败的位置、处理方式「换用另一路」「上下文反算」「只标记」）
    check_pred(pred, alts_of=None, reread=None) -> (new_pred, logs)   整份 {页: page} 逐页调用 check_page；alts_of(页) 返回该页 alts。
    is_invoice_text(page) -> bool   文本判断发票页（含「价税合计」和「项目名称」）。

【校验关系】（金额都按分取整比较）
    c1 价税合计 大写 == 小写
    c2 合计金额 + 合计税额 == 价税合计小写
    c3 Σ明细金额 == 合计金额          （明细全部解析成功时）
    c4 Σ明细税额 == 合计税额（±0.05） （明细全部解析成功时）
    c5 每行 金额 × 税率 == 税额（±0.01）
【处理顺序】某关系对不上 → 涉及的字段标为可疑 →
    (1) 依次在 alts（再 reread）里找同一行的数，换上后全部关系成立才采用（换用另一路结果）；
    (2) 还不行：可疑字段能被两条独立关系唯一反算出同一个值、且原文里能找到这个值的数字（作为子序列）时才改（上下文反算）；
    (3) 否则只标记，不改。
只改数字片段（含 ¥、%），不动其他文字；没有可疑字段的页原样返回。"""
from __future__ import annotations
import copy, re, unicodedata

CN_D = dict(zip('零壹贰叁肆伍陆柒捌玖', range(10)))
CN_U = {'拾': 10, '佰': 100, '仟': 1000}
YEN = '[¥￥]'

def N(s): return unicodedata.normalize('NFKC', s or '')

def cn2fen(s):
    """大写金额 → 分（int）；解析不了返回 None。"""
    s = re.sub(r'[^零壹贰叁肆伍陆柒捌玖拾佰仟万亿元圆角分整正]', '', s or '')
    if not s or '元' not in s and '圆' not in s: return None
    s = s.replace('圆', '元')
    yuan, _, rest = s.partition('元')
    def sec(t):
        v, d = 0, 0
        for ch in t:
            if ch in CN_D: d = CN_D[ch]
            elif ch in CN_U: v += (d or 1) * CN_U[ch]; d = 0
        return v + d
    total = 0
    for part, mul in ((yuan.split('亿')[0] if '亿' in yuan else '', 10 ** 8),):
        if part: total += sec(part.split('万')[0]) * 10 ** 4 * mul if '万' in part else sec(part) * mul
    y = yuan.split('亿')[-1]
    if '万' in y: w, y = y.split('万', 1); total += sec(w) * 10 ** 4
    total += sec(y)
    fen = total * 100
    m = re.search(r'([零壹贰叁肆伍陆柒捌玖])角', rest); fen += CN_D[m.group(1)] * 10 if m else 0
    m = re.search(r'([零壹贰叁肆伍陆柒捌玖])分', rest); fen += CN_D[m.group(1)] if m else 0
    return fen

def fen(x):
    x = (x or '').replace(',', '')
    if not re.fullmatch(r'-?\d+\.\d{2}', x): return None
    neg = x.startswith('-'); a, b = x.lstrip('-').split('.')
    v = int(a) * 100 + int(b); return -v if neg else v
def yuan(v): return ('-' if v < 0 else '') + f'{abs(v) // 100}.{abs(v) % 100:02d}'

# ------------------------------------------------------------------ 解析
HEJI = re.compile(r'(?<![价税])合\s*计\s*(' + YEN + r')\s*([-\d.,]*)\s*(' + YEN + r')\s*([-\d.,]*)')
JSHJ = re.compile(r'价\s*税\s*合\s*计[^\n]*')
TAIL = re.compile(r'([-\d.,]*\.\d{2})\s*(\d{1,2})\s*%\s*(-?[\d,]*\.\d{2})')

def parse_jshj(t):
    m = JSHJ.search(t)
    if not m: return None
    line = m.group(0); nums = list(re.finditer(YEN + r'\s*([\d,]+\.\d{2})', line))
    return {'大写': cn2fen(line), '小写': fen(nums[-1].group(1)) if nums else None, 'span': (m.start(), m.end())}

def parse_heji(t):
    m = HEJI.search(t)
    if not m: return None
    return {'金额': fen(m.group(2)), '税额': fen(m.group(4)), 'm': m}

def split_amt(blob, rate, tax):
    """金额与前面的数量/单价粘连时，取满足 金额×税率≈税额 的最长后缀；没有就返回 None。"""
    blob = blob.replace(',', ''); best = None
    for k in range(len(blob) - 4, -1, -1):
        s = blob[k:]
        if not re.fullmatch(r'-?\d+\.\d{2}', s) or (len(s) > 4 and s[0] == '0' and s[1] != '.'): continue
        v = fen(s)
        if v is not None and tax is not None and abs(round(v * rate / 100) - tax) <= 1: best = (k, v)
    return best

def item_rows(t):
    """表头（项目名称）所在行之后、合计行之前的明细行。行首＝税收分类「*类别*」出现处（可在行中间：前一行的税额折到下一行时）；
    没有「*类别*」时，以 （ 或 ( 起头的物理行起头。返回 [(起, 止, 文本)]（起止为 t 中下标）。"""
    hs = t.find('项目名称'); hm = HEJI.search(t)
    if hs < 0 or not hm: return []
    a = t.find('\n', hs) + 1; b = hm.start()
    if a <= 0 or a >= b: return []
    starts = [a + m.start() for m in re.finditer(r'[*＊][^*＊\n]{1,12}[*＊]', t[a:b])]
    if not starts: starts = [a + m.start(1) for m in re.finditer(r'(?:^|\n)\s*([(（])', t[a:b])]
    if not starts: return []
    ends = starts[1:] + [b]
    return [(s, e, t[s:e]) for s, e in zip(starts, ends)]

def parse_row(txt):
    nt = N(txt); m = None
    for m_ in TAIL.finditer(nt.replace(' ', '')): m = m_
    if not m: return {'ok': False}
    rate = int(m.group(2)); tax = fen(m.group(3)); sp = split_amt(m.group(1), rate, tax)
    return {'ok': sp is not None, 'rate': rate, 'tax': tax, 'amt': sp[1] if sp else None}

def analyse(t):
    j, h = parse_jshj(t), parse_heji(t)
    rows = [(s, e, parse_row(x)) for s, e, x in item_rows(t)]
    fails = []
    if j and j['大写'] is not None and j['小写'] is not None and j['大写'] != j['小写']: fails.append('c1')
    if h and j and j['小写'] is not None and (h['金额'] is None or h['税额'] is None or h['金额'] + h['税额'] != j['小写']): fails.append('c2')
    if rows and all(r['ok'] for _, _, r in rows) and h:
        if h['金额'] is not None and sum(r['amt'] for _, _, r in rows) != h['金额']: fails.append('c3')
        if h['税额'] is not None and abs(sum(r['tax'] for _, _, r in rows) - h['税额']) > 5: fails.append('c4')
    for k, (_, _, r) in enumerate(rows):
        if not r['ok']: fails.append(f'c5:{k}')
    return {'价税合计': j, '合计': h, '明细': rows, '失败': fails}

# ------------------------------------------------------------------ 修
def _set_heji(t, h_m, a, b):
    s = t[:h_m.start(2)] + yuan(a) + t[h_m.end(2):h_m.start(4)] + yuan(b) + t[h_m.end(4):]
    return s

def _subseq(needle, hay):
    it = iter(hay); return all(ch in it for ch in needle)

def fix_text(t, alts, reread, log):
    A = analyse(t)
    if not A['失败']: return None
    log.append({'校验失败': A['失败']})
    j, h, rows = A['价税合计'], A['合计'], A['明细']
    total = j['小写'] if j and j['小写'] is not None and (j['大写'] is None or j['大写'] == j['小写']) else (j['大写'] if j else None)
    new = t
    # ---- 合计行
    if h and ('c2' in A['失败'] or 'c3' in A['失败'] or 'c4' in A['失败']):
        srcs = [('另一路', a) for a in alts]
        if reread: srcs.append(('裁切重识别', reread({'类型': '合计行', '提示': h['m'].group(0)}) or ''))
        done = False
        for tag, a in srcs:
            ha = parse_heji(N(a))
            if not ha or ha['金额'] is None or ha['税额'] is None or total is None: continue
            if ha['金额'] + ha['税额'] != total: continue
            if all(r['ok'] for _, _, r in rows) and rows and sum(r['amt'] for _, _, r in rows) != ha['金额']: continue
            new = _set_heji(new, h['m'], ha['金额'], ha['税额']); log.append({'合计行': '换用' + tag, '原': h['m'].group(0), '新金额': yuan(ha['金额']), '新税额': yuan(ha['税额'])}); done = True; break
        if not done and total is not None:
            # 上下文反算：两条独立关系给同一个值
            ok_rows = rows and all(r['ok'] for _, _, r in rows)
            sa, st_ = (sum(r['amt'] for _, _, r in rows), sum(r['tax'] for _, _, r in rows)) if ok_rows else (None, None)
            cand = None
            if h['金额'] is not None and (h['税额'] is None or h['金额'] + h['税额'] != total) and ok_rows and sa == h['金额'] and total - h['金额'] == st_:
                cand = (h['金额'], st_)
            elif h['税额'] is not None and ok_rows and abs(st_ - h['税额']) <= 5 and total - h['税额'] == sa:
                cand = (sa, h['税额'])
            if cand: new = _set_heji(new, h['m'], *cand); log.append({'合计行': '上下文反算', '原': h['m'].group(0), '新金额': yuan(cand[0]), '新税额': yuan(cand[1])}); done = True
        if not done: log.append({'合计行': '只标记', '原': h['m'].group(0)})
    # ---- 明细行（c5 失败的行）
    A2 = analyse(new); h2 = A2['合计']; rows2 = A2['明细']
    bad = [k for k, (_, _, r) in enumerate(rows2) if not r['ok']]
    for k in sorted(bad, reverse=True):
        s, e, _ = rows2[k]; txt = new[s:e]
        others = [r for i, (_, _, r) in enumerate(rows2) if i != k]
        fixed = None
        # (1) 另一路：按行首名称（去空白前 6 字）找同一行，解析成功、且（能核的话）合计成立
        key = re.sub(r'\s', '', N(txt))[:6]
        for tag, a in [('另一路', x) for x in alts]:
            na = N(a); i = re.sub(r'\s', '', na).find(key) if key else -1
            if i < 0: continue
            flat = re.sub(r'\s', '', na)[i:i + 200]
            m = TAIL.search(flat)
            if not m: continue
            r = parse_row(m.group(0))
            if not r['ok']: continue
            if h2 and h2['金额'] is not None and all(o['ok'] for o in others) and sum(o['amt'] for o in others) + r['amt'] != h2['金额']: continue
            fixed = (r, tag); break
        # (2) 上下文反算：其他行都好、合计可信 → 本行金额/税额唯一；原文里要能按顺序找到这几个数字
        if not fixed and h2 and h2['金额'] is not None and h2['税额'] is not None and all(o['ok'] for o in others) \
                and total is not None and h2['金额'] + h2['税额'] == total:
            am = h2['金额'] - sum(o['amt'] for o in others); tx = h2['税额'] - sum(o['tax'] for o in others)
            for rate in (13, 9, 6, 5, 3, 1, 0):
                if am > 0 and abs(round(am * rate / 100) - tx) <= 1:
                    digits = re.sub(r'[^\d]', '', N(txt))
                    if _subseq(yuan(am).replace('.', '') + str(rate) + yuan(tx).replace('.', ''), digits) and yuan(am) in N(txt).replace(' ', '') and yuan(tx) in N(txt):
                        fixed = ({'amt': am, 'tax': tx, 'rate': rate, 'ok': True}, '上下文反算')
                    break
        if not fixed:
            log.append({'明细行': '只标记', '行': txt}); continue
        r, tag = fixed
        # 改写：保留金额之前的文字（名称、规格、单位、数量、单价）与非数字杂字；金额起点到税额止点之间的数字/./% 换成「金额 税率% 」
        nt = txt; ia = nt.find(yuan(r['amt'])); it_ = nt.rfind(yuan(r['tax']))
        if ia < 0 or it_ < ia + len(yuan(r['amt'])):
            log.append({'明细行': '只标记（原文定位不到金额/税额）', '行': txt, '可采用': tag}); continue
        mid = nt[ia + len(yuan(r['amt'])):it_]
        keep = re.sub(r'[\d.,%¥￥\s]', '', mid)
        rep = nt[:ia] + yuan(r['amt']) + f" {r['rate']}%" + keep + ' ' + nt[it_:]
        new = new[:s] + rep + new[e:]
        log.append({'明细行': tag, '原': txt, '新': rep})
    return new if new != t else None

def is_invoice_text(page):
    t = ''.join(x['text'] for x in page.get('sections', []))
    return '价税合计' in N(t).replace(' ', '') and '项目名称' in N(t).replace(' ', '')

def check_page(page, alts=(), reread=None):
    out = copy.deepcopy(page); log = []
    if not is_invoice_text(page): return out, log
    for i, x in enumerate(out['sections']):
        if x['section'] != 'table' or '价税合计' not in re.sub(r'\s', '', N(x['text'])): continue
        l1 = []; new = fix_text(x['text'], list(alts), reread, l1)
        if l1: log.append({'段': i, '记录': l1})
        if new is not None: x['text'] = new
    return out, log

def check_pred(pred, alts_of=None, reread=None):
    out, logs = {}, {}
    for p in sorted(pred, key=lambda s: int(s)):
        out[p], lg = check_page(pred[p], alts_of(p) if alts_of else (), reread)
        if lg: logs[p] = lg
    return out, logs

if __name__ == '__main__':
    assert cn2fen('伍佰陆拾壹万叁仟贰佰捌拾叁元壹角柒分') == 561328317
    assert cn2fen('壹佰零玖万壹仟陆佰贰拾伍元柒角肆分') == 109162574
    assert cn2fen('叁拾伍万玖仟伍佰陆拾元伍角肆分') == 35956054
    assert split_amt('84126.7533014.00', 13, fen('4291.82'))[1] == 3301400
    print('自检通过')
