"""第2轮（事后/试算）在乙′上加三条通用修改，开关 FIX = {"F1","F2","F3"}：
 F1 合计行单列：表头与第一个「合计」起头行之间、离「合计」行≥1 字高的物理行自成一个逻辑行（不再并进合计行；单行明细如「（详见销货清单）」）。
 F2 区域下沿：A 里「合计」（非价税合计）落在原下沿与价税合计行之间时，下沿放到它下面（压线金额/横线把合计行切在区外时）。
 F3 表头带：与「项目名称」所在物理行 y 差 <1.2 字高、且由表头标签组成的物理行都算表头（标签可不在同一基线）；标签按词表切分（粘连「单位数量」、拉开「单 价」都能切对），≥4 个标签才用，否则退回原做法；表头带各行都归表头。
以下为原说明：
事后修订版（乙′）：看过预先声明版的改写结果（未看分数前）后，针对三处明显缺陷做的通用修改；只作参考，需在新数据集上验证。
 (1) 定位：先按 C 里以空白分隔的片段（≥2 字，长的先）在 A 里找完全相同且未用的子串，再走原来的对齐；避免同字（如「额」）被配到别的格。
 (2) 逻辑行归属：不以「*」「合」起头的物理行，若离下一个起头行比离上一逻辑行最后一条物理行更近、且 y 中心差 < 1 字高，并入下一逻辑行（标签与数字不在同一基线时，如「合计」）。
 (4) 单字定位：剩下的单个字，在未用 A 字里取与前一个已定位字的 A 序号最近的同字。
 (3) 列界：表头里相邻的单字片段（字距拉开的标签，如「单 位」「金 额」）先两两合并，再取中点。
其余与预先声明版相同。"""
import re
from difflib import SequenceMatcher
import layout as L
FIX = {'F1', 'F2', 'F3'}   # 冻结：三条全开
LABS = ['税率/征收率', '项目名称', '规格型号', '征收率', '税率', '单位', '数量', '单价', '金额', '税额']
def seg_labels(chars):
    s = ''.join(nch(c['ch']) for c in chars); out = []; i = 0
    while i < len(s):
        lab = next((x for x in LABS if s.startswith(x, i)), None)
        if lab: out.append(chars[i:i + len(lab)]); i += len(lab)
        else: i += 1
    return out
from layout import nch, cx, cy, ch_h, phys_lines, place, hlines_in, vlines_in, find_seq, fragments, check_moves, NEW_ROW, COV_MIN

def locate2(text, achars):
    items = [(i, ch) for i, ch in enumerate(text) if not ch.isspace()]
    pos_of = {i: t for t, (i, _) in enumerate(items)}
    bs = ''.join(nch(ch) for _, ch in items); astr = ''.join(nch(c['ch']) for c in achars)
    loc = [None] * len(items); used = [False] * len(achars)
    toks = [(m.start(), m.end()) for m in re.finditer(r'\S+', text)]
    toks = [(pos_of[a], pos_of[a] + (b - a)) for a, b in toks if b - a >= 2]
    for a, b in sorted(toks, key=lambda t: -(t[1] - t[0])):
        seg = bs[a:b]; k = astr.find(seg)
        while k >= 0 and any(used[k:k + len(seg)]): k = astr.find(seg, k + 1)
        if k >= 0:
            for t in range(len(seg)): loc[a + t] = k + t; used[k + t] = True
    # 剩下的：分段（被已定位字隔开的连续未定位串）各自在未用 A 字里找最长公共片段，反复
    changed = True
    while changed:
        changed = False; i = 0
        while i < len(items):
            if loc[i] is not None: i += 1; continue
            j = i
            while j < len(items) and loc[j] is None: j += 1
            seg = bs[i:j]; best = None; k = 0
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
    for t in range(len(items)):
        if loc[t] is not None: continue
        cand = [k for k in range(len(achars)) if not used[k] and astr[k] == bs[t]]
        if not cand: continue
        nb = next((loc[u] for u in range(t - 1, -1, -1) if loc[u] is not None), None)
        if nb is None: nb = next((loc[u] for u in range(t + 1, len(items)) if loc[u] is not None), 0)
        k = min(cand, key=lambda k: abs(k - nb)); loc[t] = k; used[k] = True
    return [(items[t][0], items[t][1], achars[loc[t]] if loc[t] is not None else None) for t in range(len(items))]

def invoice_region(text, achars, lines, mode, log):
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
    x0, x1 = min(c['bbox'][0] for c in hd) - 3 * h, lines['W_pt']
    hl = hlines_in(lines, x0, x0 + 0.5 * (x1 - x0), ytop0 - 4 * h, ybot0 + 0.5 * h, 0.8)
    up = [y for y in hl if y <= ytop0 + 0.3 * h]; dn = [y for y in hl if ytop0 + h < y <= cy(vt[0])]
    ytop = up[-1] if up else ytop0 - 0.3 * h; ybot = dn[-1] if dn else ybot0 - 0.2 * h
    if 'F2' in FIX:
        _as = ''.join(nch(c['ch']) for c in srt)
        for _m in re.finditer('合计', _as):
            if _as[max(0, _m.start() - 2):_m.start()] == '价税': continue
            _cs = srt[_m.start():_m.start() + 2]
            if ybot < cy(_cs[0]) < cy(vt[0]) - 0.5 * h and cy(_cs[0]) > ytop0: ybot = max(ybot, max(c['bbox'][3] for c in _cs) + 0.2 * h)
    full = [y for y in lines['h'] if abs(y[1] - ytop) < 3]
    xl, xr = (min(y[0] for y in full) - 2, max(y[2] for y in full) + 2) if full else (0, lines['W_pt'])
    reg = [c for c in achars if ytop - 1 < cy(c) < ybot and xl < cx(c) < xr]
    pls = phys_lines(reg); asort = [c for l in pls for c in l]
    lid = {id(c): k for k, l in enumerate(pls) for c in l}
    loc = locate2(R, asort); n = len(loc); m = sum(a is not None for _, _, a in loc)
    info = {'区域y': [round(ytop, 1), round(ybot, 1)], '物理行数': len(pls), '定位覆盖率': round(m / max(1, n), 3)}
    if n == 0 or m / n < COV_MIN: log.append({**info, '跳过': '覆盖率不足'}); return None
    P = place(loc)
    for c in P: c['line'] = lid[id(c['a'])]; c['x'] = c['a']['bbox'][0] + c['dx']
    byl = {}
    for c in P: byl.setdefault(c['line'], []).append(c)
    for k in byl: byl[k].sort(key=lambda c: (c['x'], c['i']))
    order = sorted(byl)
    ly = {k: sum(cy(c['a']) for c in byl[k]) / len(byl[k]) for k in order}
    hline = lid.get(id(hd[0]))
    hdr_chars = sorted(pls[hline], key=lambda c: c['bbox'][0]) if hline is not None else []
    hband = set(); sl = None
    if 'F3' in FIX and hline is not None:
        yh = sum(cy(c) for c in pls[hline]) / len(pls[hline])
        for k, l in enumerate(pls):
            if k == hline: continue
            t_ = ''.join(nch(c['ch']) for c in l)
            if abs(sum(cy(c) for c in l) / len(l) - yh) < 1.2 * h and sum(len(x) for x in seg_labels(l)) >= 0.8 * len(t_): hband.add(k)
        sl = seg_labels(sorted([c for k in hband | {hline} for c in pls[k]], key=lambda c: c['bbox'][0]))
        if len(sl) < 4: hband = set(); sl = None
    labs, cur = [], []
    for c in hdr_chars:
        if cur and c['bbox'][0] - cur[-1]['bbox'][2] > 0.8 * h: labs.append(cur); cur = []
        cur.append(c)
    if cur: labs.append(cur)
    merged = []   # (3) 相邻单字片段两两合并
    for f in labs:
        if merged and len(merged[-1]) == 1 and len(f) == 1 and f[0]['bbox'][0] - merged[-1][-1]['bbox'][2] < 3 * h: merged[-1] = merged[-1] + f
        else: merged.append(f)
    labs = merged
    if sl: labs = sl
    vx = vlines_in(lines, ytop, ybot, xl, xr)
    bounds = vx if len(vx) >= 3 else [(labs[k][-1]['bbox'][2] + labs[k + 1][0]['bbox'][0]) / 2 for k in range(len(labs) - 1)]
    info['列界来源'] = '竖线' if len(vx) >= 3 else '表头标签中点'; info['列数'] = len(bounds) + 1
    col = lambda x: sum(x >= b for b in bounds)
    fc = lambda f: col((f[0]['a']['bbox'][0] + f[-1]['a']['bbox'][2]) / 2)
    frs_of = {k: fragments(byl[k], h) for k in order}
    is_start = {}
    for k in order:
        c0 = ''.join(c['ch'] for f in frs_of[k] if fc(f) == 0 for c in f)
        is_start[k] = hline is not None and k > hline and k not in hband and bool(NEW_ROW.match(c0))
    if not any(is_start.values()):
        lrows = [[k] for k in order]; info['退回逐行'] = True
    else:
        owner = {}; rows = []
        for k in order:
            if hline is not None and (k <= hline or k in hband): owner[k] = 'H'
            elif is_start[k]: owner[k] = k
        starts = [k for k in order if is_start[k]]
        for k in order:
            if k in owner: continue
            prev = [s for s in starts if s < k]; nxt = [s for s in starts if s > k]
            if not prev:
                c0n = ''.join(c['ch'] for f in frs_of[nxt[0]] if fc(f) == 0 for c in f) if nxt else ''
                if 'F1' in FIX and nxt and c0n.lstrip().startswith('合') and abs(ly[nxt[0]] - ly[k]) >= 1.0 * h: owner[k] = 'P'
                else: owner[k] = nxt[0] if nxt else 'H'
                continue
            last_prev = max(j for j in order if j < k and owner.get(j) == prev[-1])   # (2)
            if nxt and abs(ly[nxt[0]] - ly[k]) < min(1.0 * h, abs(ly[k] - ly[last_prev])): owner[k] = nxt[0]
            else: owner[k] = prev[-1]
        keys = ['H', 'P'] + starts
        lrows = [[k for k in order if owner[k] == o] for o in keys]; lrows = [r for r in lrows if r]
    outs = []
    for lr in lrows:
        cols = {}
        for k in lr:
            for f in frs_of[k]: cols.setdefault(fc(f), []).append((k, f))
        outs.append(' '.join(''.join(''.join(c['ch'] for c in f) for k, f in sorted(cols[j], key=lambda t: (t[0], t[1][0]['x']))) for j in sorted(cols)))
    new = '\n'.join(outs); info['逻辑行数'] = len(lrows)
    check_moves(R, new)
    if re.sub(r'\s', '', new) == re.sub(r'\s', '', R): log.append({**info, '结果': '与原顺序一致，不改'}); return None
    log.append({**info, '结果': '改写', '原': R, '新': new})
    return text[:hs] + new + '\n' + text[ve:]
