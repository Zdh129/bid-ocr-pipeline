"""版面约束规则（预先声明版，见 预先声明的规则.md）。纯本地：只用 C 文本、A（高精度含位置）已落盘的字框、本地 300 DPI 表格线。
字符只搬家、不增删、不换字（每次改写都断言字符多重集合不变）。"""
import re, unicodedata
from collections import Counter
from difflib import SequenceMatcher

NEW_ROW = re.compile(r'^\s*(\*|合)')
COV_MIN = 0.85          # 定位覆盖率门槛
def nch(c): 
    t = unicodedata.normalize('NFKC', c)
    return t[0] if t else c
def cx(c): return (c['bbox'][0] + c['bbox'][2]) / 2
def cy(c): return (c['bbox'][1] + c['bbox'][3]) / 2
def ch_h(cs):
    hs = sorted(c['bbox'][3] - c['bbox'][1] for c in cs) or [10.0]
    return hs[len(hs) // 2]

def phys_lines(chars):
    """A 字框按行聚类（y 中心差 < 半字高），返回 [[char...]...]，行内按 x 排；每个字加 'line' 序号。"""
    if not chars: return []
    tol = max(2.0, ch_h(chars) * 0.5)
    cs = sorted(chars, key=cy); lines, cur, y0 = [], [], None
    for c in cs:
        if cur and abs(cy(c) - y0) > tol: lines.append(cur); cur = []
        cur.append(c); y0 = sum(cy(k) for k in cur) / len(cur)
    if cur: lines.append(cur)
    lines = [sorted(l, key=lambda k: k['bbox'][0]) for l in lines]
    return lines

def locate(text, achars):
    """把 text 的非空白字逐个定位到 achars（已按阅读顺序排好的列表）。返回 [(text 下标, 字, A 字或 None)]。"""
    items = [(i, ch) for i, ch in enumerate(text) if not ch.isspace()]
    bs = ''.join(nch(ch) for _, ch in items); astr = ''.join(nch(c['ch']) for c in achars)
    loc = [None] * len(items); used = [False] * len(achars)
    for a, b, n in SequenceMatcher(None, bs, astr, autojunk=False).get_matching_blocks():
        for k in range(n): loc[a + k] = b + k; used[b + k] = True
    # 没对上的连续片段（≥2 字）：在未用的 A 字里找最长公共片段，反复直到找不到
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(items):
            if loc[i] is not None: i += 1; continue
            j = i
            while j < len(items) and loc[j] is None: j += 1
            if j - i >= 2:
                seg = bs[i:j]; best = None
                k = 0
                while k < len(achars):
                    if used[k]: k += 1; continue
                    m = k
                    while m < len(achars) and not used[m]: m += 1
                    sm = SequenceMatcher(None, seg, astr[k:m], autojunk=False).find_longest_match(0, len(seg), 0, m - k)
                    if sm.size >= 2 and (best is None or sm.size > best[2]): best = (sm.a, k + sm.b, sm.size)
                    k = m
                if best:
                    a, b, n = best
                    for t in range(n): loc[i + a + t] = b + t; used[b + t] = True
                    changed = True
            i = j
    return [(items[t][0], items[t][1], achars[loc[t]] if loc[t] is not None else None) for t in range(len(items))]

def place(located):
    """未定位的字沿用前一个已定位字的位置（没有前一个就用后一个），x 加极小偏移保持原相对顺序。"""
    out = []; last = None
    first = next((a for _, _, a in located if a is not None), None)
    for k, (i, ch, a) in enumerate(located):
        if a is not None: last = a; out.append({'i': i, 'ch': ch, 'a': a, 'inh': False, 'dx': 0.0}); continue
        ref = last or first
        out.append({'i': i, 'ch': ch, 'a': ref, 'inh': True, 'dx': 1e-3 * (k + 1)})
    return out

def hlines_in(lines, x0, x1, y0, y1, min_ov=0.6):
    """与 [x0,x1] 重叠 ≥ min_ov 的水平线 y（落在 [y0,y1]），3pt 内合并。"""
    ys = sorted(h[1] for h in lines['h'] if y0 <= h[1] <= y1 and (min(h[2], x1) - max(h[0], x0)) >= min_ov * (x1 - x0))
    out = []
    for y in ys:
        if out and y - out[-1] < 3: continue
        out.append(y)
    return out

def vlines_in(lines, y0, y1, x0, x1, min_cov=0.6):
    xs = sorted(v[0] for v in lines['v'] if x0 + 3 < v[0] < x1 - 3 and (min(v[2], y1) - max(v[1], y0)) >= min_cov * (y1 - y0))
    out = []
    for x in xs:
        if out and x - out[-1] < 3: continue
        out.append(x)
    return out

def find_seq(achars_sorted, word):
    s = ''.join(nch(c['ch']) for c in achars_sorted); i = s.find(word)
    return achars_sorted[i:i + len(word)] if i >= 0 else None

def fragments(pl, h):
    """一条物理行（已放好的字，按 x 排）切片段：相邻 A 字框间距 > 0.8 字高处切开。"""
    frs, cur, prev = [], [], None
    for c in pl:
        if cur and not c['inh'] and prev is not None and c['a'] is not prev['a'] and c['a']['bbox'][0] - prev['a']['bbox'][2] > 0.8 * h:
            frs.append(cur); cur = []
        cur.append(c)
        if not c['inh']: prev = c
    if cur: frs.append(cur)
    return frs

def check_moves(old, new):
    assert Counter(ch for ch in old if not ch.isspace()) == Counter(ch for ch in new if not ch.isspace()), '字符多重集合变了'

# ------------------------------------------------------------------ 规则一：发票明细区（甲：行带；乙：格子）
def invoice_region(text, achars, lines, mode, log):
    """text：C 里含明细区的那一段表格文字。返回新文字或 None（不改）。"""
    hs = text.find('项目名称'); vm = re.search(r'价\s*税\s*合\s*计', text[hs + 1:]) if hs >= 0 else None
    if hs < 0 or not vm: return None
    ve = text.rfind('\n', 0, hs + 1 + vm.start()) + 1
    if ve <= hs: log.append({'跳过': '价税合计与表头同一行'}); return None
    R = text[hs:ve].rstrip('\n')
    srt = [c for l in phys_lines(achars) for c in l]
    hd = find_seq(srt, '项目名称'); vt = find_seq(srt, '价税合计')
    if not hd or not vt: log.append({'跳过': 'A 里找不到 项目名称/价税合计'}); return None
    h = ch_h(achars)
    ytop0, ybot0 = min(c['bbox'][1] for c in hd), min(c['bbox'][1] for c in vt)
    if ybot0 <= ytop0: return None
    # 区域上下界：表头上方最近的横线、价税合计上方最近的横线（没有就用 A 锚点）
    x0, x1 = min(c['bbox'][0] for c in hd) - 3 * h, lines['W_pt']
    hl = hlines_in(lines, x0, x0 + 0.5 * (x1 - x0), ytop0 - 4 * h, ybot0 + 0.5 * h, 0.8)
    up = [y for y in hl if y <= ytop0 + 0.3 * h]; dn = [y for y in hl if ytop0 + h < y <= cy(vt[0])]
    ytop = up[-1] if up else ytop0 - 0.3 * h; ybot = dn[-1] if dn else ybot0 - 0.2 * h
    full = [y for y in lines['h'] if abs(y[1] - ytop) < 3]
    xl, xr = (min(y[0] for y in full) - 2, max(y[2] for y in full) + 2) if full else (0, lines['W_pt'])
    reg = [c for c in achars if ytop - 1 < cy(c) < ybot and xl < cx(c) < xr]
    pls = phys_lines(reg); asort = [c for l in pls for c in l]
    lid = {id(c): k for k, l in enumerate(pls) for c in l}
    loc = locate(R, asort); n = len(loc); m = sum(a is not None for _, _, a in loc)
    info = {'区域y': [round(ytop, 1), round(ybot, 1)], '物理行数': len(pls), '定位覆盖率': round(m / max(1, n), 3), '用到横线': bool(up or dn)}
    if n == 0 or m / n < COV_MIN: log.append({**info, '跳过': '覆盖率不足'}); return None
    P = place(loc)
    for c in P: c['line'] = lid[id(c['a'])]; c['x'] = c['a']['bbox'][0] + c['dx']
    byl = {}
    for c in P: byl.setdefault(c['line'], []).append(c)
    for k in byl: byl[k].sort(key=lambda c: (c['x'], c['i']))
    order = sorted(byl)
    if mode == '甲':
        rows = [[byl[k]] for k in order]                  # 每条物理行一行，行内按 x
        new = '\n'.join(''.join(c['ch'] for c in r[0]) for r in rows)
    else:
        hline = lid.get(id(hd[0]))
        hdr_chars = sorted(pls[hline], key=lambda c: c['bbox'][0]) if hline is not None else []
        labs, cur = [], []
        for c in hdr_chars:
            if cur and c['bbox'][0] - cur[-1]['bbox'][2] > 0.8 * h: labs.append(cur); cur = []
            cur.append(c)
        if cur: labs.append(cur)
        vx = vlines_in(lines, ytop, ybot, xl, xr)
        bounds = vx if len(vx) >= 3 else [(labs[k][-1]['bbox'][2] + labs[k + 1][0]['bbox'][0]) / 2 for k in range(len(labs) - 1)]
        info['列界来源'] = '竖线' if len(vx) >= 3 else '表头标签中点'; info['列数'] = len(bounds) + 1
        col = lambda x: sum(x >= b for b in bounds)
        lrows = []  # [[(line, [frags...])...]]
        starts = 0
        for k in order:
            frs = fragments(byl[k], h)
            c0 = ''.join(c['ch'] for f in frs if col((f[0]['a']['bbox'][0] + f[-1]['a']['bbox'][2]) / 2) == 0 for c in f)
            is_hdr = hline is not None and k <= hline
            if not lrows or (not is_hdr and NEW_ROW.match(c0)) or (is_hdr and k == order[0]):
                lrows.append([]); starts += (not is_hdr)
            lrows[-1].append((k, frs))
        if starts == 0:   # 一行都没有「*」「合」起头：退回逐物理行
            lrows = [[(k, fragments(byl[k], h))] for k in order]; info['退回逐行'] = True
        outs = []
        for lr in lrows:
            cols = {}
            for k, frs in lr:
                for f in frs:
                    cols.setdefault(col((f[0]['a']['bbox'][0] + f[-1]['a']['bbox'][2]) / 2), []).append((k, f))
            outs.append(' '.join(''.join(''.join(c['ch'] for c in f) for k, f in sorted(cols[j], key=lambda t: (t[0], t[1][0]['x']))) for j in sorted(cols)))
        new = '\n'.join(outs); info['逻辑行数'] = len(lrows)
    check_moves(R, new)
    norm = lambda s: re.sub(r'\s', '', s)
    if norm(new) == norm(R): log.append({**info, '结果': '与原顺序一致，不改'}); return None
    log.append({**info, '结果': '改写', '原': R, '新': new})
    return text[:hs] + new + ('\n' if not new.endswith('\n') else '') + text[ve:]

# ------------------------------------------------------------------ 规则二：通用有线表格·行带校验（防串行）
def ruled_table(text, achars, lines, log, skip_span=None):
    """text：C 的一段表格文字（若 skip_span=(a,b) 给出，该区间由规则一负责，不参与）。"""
    if not text.strip(): return None
    srt = [c for l in phys_lines(achars) for c in l]
    loc = locate(text, srt); n = len(loc); m = sum(a is not None for _, _, a in loc)
    if n < 8 or m / n < COV_MIN: log.append({'跳过': '覆盖率不足或太短', '定位覆盖率': round(m / max(1, n), 3)}); return None
    P = place(loc); h = ch_h(achars)
    real = [c for c in P if not c['inh']]
    X0, X1 = min(c['a']['bbox'][0] for c in real), max(c['a']['bbox'][2] for c in real)
    Y0, Y1 = min(c['a']['bbox'][1] for c in real), max(c['a']['bbox'][3] for c in real)
    hy = hlines_in(lines, X0, X1, Y0 - 2 * h, Y1 + 2 * h, 0.6)
    if len(hy) < 3: log.append({'跳过': '横线不足 3 条（不当作有线表格）'}); return None
    band = lambda y: sum(y >= b for b in hy)
    # 每条 C 行（按 \n 切）里的字落在几个行带
    lines_c = []; pos = 0
    for ln in text.split('\n'):
        lines_c.append((pos, pos + len(ln))); pos += len(ln) + 1
    flagged = []
    for a, b in lines_c:
        if skip_span and a < skip_span[1] and skip_span[0] < b: continue
        cnt = Counter(band(cy(c['a'])) for c in P if a <= c['i'] < b and not c['inh'])
        if len([v for v in cnt.values() if v >= 2]) >= 2: flagged.append((a, b, dict(cnt)))
    if not flagged: log.append({'结果': '无串行', '横线数': len(hy)}); return None
    # 只重排被标记的 C 行所涉及的行带：这些 C 行的字按行带归组，行带内按竖线分列，列内按 (物理行, x)
    pl = phys_lines([c['a'] for c in real]); lid = {id(c): k for k, l in enumerate(pl) for c in l}
    seg_a, seg_b = min(f[0] for f in flagged), max(f[1] for f in flagged)
    # 扩到连续：从第一条到最后一条被标记行之间的所有 C 行一起重排
    sel = [c for c in P if seg_a <= c['i'] < seg_b and not (skip_span and skip_span[0] <= c['i'] < skip_span[1])]
    if skip_span and seg_a < skip_span[1] and skip_span[0] < seg_b: log.append({'跳过': '与发票明细区交叠'}); return None
    old = text[seg_a:seg_b]
    groups = {}
    for c in sel: groups.setdefault(band(cy(c['a'])), []).append(c)
    outs = []
    for bd in sorted(groups):
        ys = (hy[bd - 1] if bd > 0 else Y0 - 2 * h, hy[bd] if bd < len(hy) else Y1 + 2 * h)
        vx = vlines_in(lines, ys[0], ys[1], X0 - 3, X1 + 3, 0.6)
        col = lambda x: sum(x >= v for v in vx)
        cols = {}
        for c in groups[bd]: cols.setdefault(col(cx(c['a'])), []).append(c)
        outs.append(' '.join(''.join(c['ch'] for c in sorted(cols[j], key=lambda c: (lid.get(id(c['a']), 0), c['a']['bbox'][0] + c['dx'], c['i']))) for j in sorted(cols)))
    new = '\n'.join(outs)
    check_moves(old, new)
    if re.sub(r'\s', '', new) == re.sub(r'\s', '', old): log.append({'结果': '重排后顺序不变', '标记行': len(flagged)}); return None
    log.append({'结果': '改写', '横线数': len(hy), '标记行': len(flagged), '原': old, '新': new})
    return text[:seg_a] + new + text[seg_b:]
