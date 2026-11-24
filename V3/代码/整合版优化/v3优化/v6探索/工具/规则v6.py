"""v6 探索规则（用户 09:28 批准的三个方向；通用，不看文件名／页码／参照；只用已付费高精度缓存；0 次调用）。
方向1 mixed_rebuild：半横半正页按区块判方向（行框方向 + 邻域多数），各方向块分别摆正、块内按行读，块间按页面位置合并。
方向3 giant_cell：表格中单个格子占全页字数过大时，用高精度行框按坐标重排该格（3a），或整页回退高精度行序（3b）。
方向5 split_cols：并排两栏（有竖向空白带）时，把跨空白带被接成一行的行框按字距切开，再按「行组内先左栏后右栏」（5a）或「整段先左后右」（5b）读；证照页不做。"""
import math, unicodedata
from copy import deepcopy
from statistics import median
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / '旋转页规则/工具'))
import 旋转规则 as RR

def _n(s): return ''.join(c for c in unicodedata.normalize('NFKC', s or '') if not c.isspace())
def _box(w):
    L = w['location']; return (L['left'], L['top'], L['left'] + L['width'], L['top'] + L['height'])
def _iou_small(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    s = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) or 1
    return ix * iy / s
def _rows(us, key='b'):
    """只按行读：纵向重叠 ≥50% 归为一行，行内左→右（不做分栏切分，证照页口径）。"""
    it = sorted(range(len(us)), key=lambda i: (us[i][key][1] + us[i][key][3]) / 2); rows = []
    for i in it:
        b = us[i][key]
        if rows:
            g = rows[-1]; y0 = min(us[j][key][1] for j in g); y1 = max(us[j][key][3] for j in g)
            if min(y1, b[3]) - max(y0, b[1]) >= 0.5 * min(y1 - y0, b[3] - b[1]): g.append(i); continue
        rows.append([i])
    return [sorted(g, key=lambda i: us[i][key][0]) for g in rows]
def _post_like(pg, lines):
    out = deepcopy(pg); out['sections'] = [{'section': 'body', 'text': '\n'.join(lines)}]; return out

# ---------------- 方向1 ----------------
def mixed_info(sa, q, min_minor=0.25, min_chars=30, radius=0.15):
    raw = RR.load(sa, q)
    if not raw: return None
    ws = [w for w in raw['words'] if w.get('vertexes_location') and len(_n(w.get('words'))) >= 1]
    votes = {}
    for w in ws: c = RR._cls(w['vertexes_location']); votes[c] = votes.get(c, 0) + len(_n(w['words']))
    n = sum(votes.values())
    if n < 2 * min_chars or len(votes) < 2: return None
    top = sorted(votes, key=votes.get, reverse=True)
    a, b = top[0], top[1]
    if votes[a] / n >= 0.6 and a != 0: return None      # 整页旋转，交给原规则
    if votes[a] / n >= 0.6 and a == 0 and votes[b] / n < min_minor: return None
    if votes[b] / n < min_minor or votes[b] < min_chars or votes[a] < min_chars or 0 not in (a, b): return None
    W, H = raw['W'], raw['H']
    if W is None: W = max(_box(w)[2] for w in ws); H = max(_box(w)[3] for w in ws)
    R = radius * max(W, H)
    cen = [((_box(w)[0] + _box(w)[2]) / 2, (_box(w)[1] + _box(w)[3]) / 2) for w in ws]
    cls = [RR._cls(w['vertexes_location']) for w in ws]
    keep = {0: [], (b if a == 0 else a): []}
    rot = b if a == 0 else a
    for i, w in enumerate(ws):
        if cls[i] not in keep: continue
        loc = {}
        for j in range(len(ws)):
            if math.dist(cen[i], cen[j]) <= R and cls[j] in keep: loc[cls[j]] = loc.get(cls[j], 0) + len(_n(ws[j]['words']))
        if max(loc, key=loc.get) == cls[i]: keep[cls[i]].append(w)
    if sum(len(_n(w['words'])) for w in keep[rot]) < min_chars or sum(len(_n(w['words'])) for w in keep[0]) < min_chars: return None
    blocks = []
    for c, lst in keep.items():
        sub = {'words': lst, 'W': W, 'H': H, 'seals': raw['seals'], 'direction': raw.get('direction')}
        us = [u for u in RR.units(sub, c) if not u['seal']]
        for u, w in zip(us, lst): pass
        # 去重：同方向、框几乎重合的保留字多的
        us.sort(key=lambda u: -len(u['t'])); ded = []
        for u in us:
            if any(_iou_small(u['b'], v['b']) >= 0.6 and (u['t'] in v['t'] or v['t'] in u['t']) for v in ded): continue
            ded.append(u)
        if not ded: continue
        pos = min(_box(w)[1] for w in lst)
        blocks.append({'方向': c, 'units': ded, 'top': pos, '字数': sum(len(u['t']) for u in ded)})
    blocks.sort(key=lambda x: x['top'])
    return {'votes': votes, 'blocks': blocks}
def mixed_rebuild(pg, sa, q):
    inf = mixed_info(sa, q)
    if not inf: return pg, None
    lines = []
    for bl in inf['blocks']:
        us = bl['units']
        for row in _rows(us): lines += [us[i]['raw'] for i in row]
    return _post_like(pg, lines), {'规则': 1, '块': [(b['方向'], b['字数']) for b in inf['blocks']]}

# ---------------- 方向3 ----------------
def _bg(s): return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else ({s} if s else set())
def giant_cell(pg, sa, q, mode='a', min_len=150, share=0.3, cover=0.6):
    """表格节里最长的格子 ≥150 字且占全页 ≥30%：整节换成高精度行框。
    3a：行框按坐标逐行读；3e：行框按其在原表格节文字中的位置排（保留原顺序、换成高精度字）；
    3b：整页回退高精度行框逐行读；3d：整页回退高精度原始返回顺序。"""
    secs = pg.get('sections') or []
    tot = sum(len(_n(s.get('text'))) for s in secs) or 1
    hit = None
    for si, s in enumerate(secs):
        if s.get('section') != 'table': continue
        L = max(len(_n(c)) for row in (s.get('text') or '').split('\n') for c in row.split(' '))
        if L >= min_len and L / tot >= share and (hit is None or L > hit[1]): hit = (si, L)
    if not hit: return pg, None
    raw = RR.load(sa, q)
    if not raw: return pg, None
    ws = [w for w in raw['words'] if len(_n(w.get('words'))) >= 2]
    def dedup(us, keep_order):
        dd = []
        for u in (us if keep_order else sorted(us, key=lambda u: -len(u['t']))):
            if any(_iou_small(u['b'], v['b']) >= 0.6 and (u['t'] in v['t'] or v['t'] in u['t']) for v in dd): continue
            dd.append(u)
        return dd
    if mode in ('b', 'd'):
        us = [{'t': _n(w['words']), 'raw': w['words'], 'b': _box(w)} for w in ws]
        dd = dedup(us, mode == 'd')
        lines = [u['raw'] for u in dd] if mode == 'd' else [dd[i]['raw'] for row in _rows(dd) for i in row]
        return _post_like(pg, lines), {'规则': '3' + mode, '格字数': hit[1]}
    ct = _n(secs[hit[0]]['text']); cb = _bg(ct); us = []
    for w in ws:
        t = _n(w['words']); b = _bg(t)
        if b and len(b & cb) / len(b) >= cover: us.append({'t': t, 'raw': w['words'], 'b': _box(w)})
    dd = dedup(us, False)
    if sum(len(u['t']) for u in dd) < 0.5 * len(ct): return pg, None
    if mode == 'e':
        pos = {}
        for i in range(len(ct) - 1): pos.setdefault(ct[i:i + 2], []).append(i)
        def off(u):
            ps = sorted(p for g in _bg(u['t']) for p in pos.get(g, [])[:3])
            return ps[len(ps) // 2] if ps else 1e9
        lines = [u['raw'] for u in sorted(dd, key=off)]
    else: lines = [dd[i]['raw'] for row in _rows(dd) for i in row]
    out = deepcopy(pg); out['sections'][hit[0]] = {'section': 'body', 'text': '\n'.join(lines)}
    return out, {'规则': '3' + mode, '格字数': hit[1], '换成行数': len(lines)}

# ---------------- 方向5 ----------------
LIC = ('营业执照', '许可证', '证书编号', '资格证书', '执业证书', '身份号码', '经营范围', '登记证', '执业资格', '注册证')
def is_licence(pg): t = ''.join(s.get('text') or '' for s in pg.get('sections') or []); return any(k in t for k in LIC)
def _band(items, W):
    """竖向空白带（局部）：取「同一物理行里有两个以上行框、且两框间有宽空白」的行，
    这些行的最大空白区间求公共交集（≥3 行），交集宽 ≥0.5×中位字高、中心位于内容 25%~75% 处。返回（带, 字高, 区域 y 范围）。"""
    ch = median([it['b'][3] - it['b'][1] for it in items])
    x0 = min(it['b'][0] for it in items); x1 = max(it['b'][2] for it in items); span = x1 - x0
    gaps = []
    for r in _rows(items):
        if len(r) < 2: continue
        bs = sorted((items[i]['b'] for i in r), key=lambda b: b[0])
        g = max(((bs[j][2], bs[j + 1][0]) for j in range(len(bs) - 1)), key=lambda g: g[1] - g[0])
        if g[1] - g[0] >= 1.5 * ch: gaps.append((g, min(b[1] for b in bs), max(b[3] for b in bs)))
    best = None
    for g, _, _ in gaps:
        grp = [x for x in gaps if min(x[0][1], g[1]) - max(x[0][0], g[0]) >= 0.5 * ch]
        if len(grp) >= 3 and (best is None or len(grp) > len(best)): best = grp
    if not best: return None
    b0 = max(x[0][0] for x in best); b1 = min(x[0][1] for x in best)
    if b1 - b0 < 0.5 * ch or not (x0 + 0.25 * span <= (b0 + b1) / 2 <= x0 + 0.75 * span): return None
    y0 = min(x[1] for x in best) - 3 * ch; y1 = max(x[2] for x in best) + 3 * ch
    return (b0, b1), ch, (y0, y1)
def split_cols(pg, sa, q, mode='a'):
    if is_licence(pg): return pg, None
    raw = RR.load(sa, q)
    if not raw or RR.detect(raw) is not None: return pg, None
    secs = pg.get('sections') or []
    lines = [(si, l) for si, s in enumerate(secs) for l in (s.get('text') or '').split('\n')]
    ws = [w for w in raw['words'] if not w['_retry'] and _n(w.get('words'))]
    wt = {}
    for w in ws: wt.setdefault(_n(w['words']), []).append(w)
    items = []
    for k, (si, l) in enumerate(lines):
        if secs[si].get('section') != 'body': continue
        t = _n(l)
        if t and wt.get(t):
            w = wt[t].pop(0); items.append({'k': k, 'w': w, 'b': _box(w), 'raw': l})
    if len(items) < 6: return pg, None
    bd = _band(items, None)
    if not bd: return pg, None
    (g0, g1), ch, (ry0, ry1) = bd
    reg = [it for it in items if ry0 <= (it['b'][1] + it['b'][3]) / 2 <= ry1]
    if len({lines[it['k']][0] for it in reg}) != 1: return pg, None      # 区域跨节：不动
    ks = sorted(it['k'] for it in reg); kmin, kmax = ks[0], ks[-1]
    if any(lines[k][0] != lines[kmin][0] for k in range(kmin, kmax + 1)): return pg, None
    tol = 0.5 * ch; segs = []; nsplit = 0
    for it in reg:
        b = it['b']
        if b[2] <= g0 + tol: segs.append((it['k'], 'L', it['raw'], b)); continue
        if b[0] >= g1 - tol: segs.append((it['k'], 'R', it['raw'], b)); continue
        chs = it['w'].get('chars') or []; cut = None
        if len(chs) >= 4 and ''.join(c['char'] for c in chs) == it['w']['words'].replace(' ', '') or (len(chs) >= 4 and _n(''.join(c['char'] for c in chs)) == _n(it['w']['words'])):
            cw = median([c['location']['width'] for c in chs])
            for i in range(len(chs) - 1):
                a = chs[i]['location']; c = chs[i + 1]['location']
                gp0, gp1 = a['left'] + a['width'], c['left']
                if gp1 - gp0 >= 0.8 * cw and g0 - tol <= (gp0 + gp1) / 2 <= g1 + tol: cut = i + 1; break
        if cut:
            lt = ''.join(c['char'] for c in chs[:cut]); rt = ''.join(c['char'] for c in chs[cut:])
            segs.append((it['k'], 'L', lt, (b[0], b[1], g0, b[3]))); segs.append((it['k'] + 0.5, 'R', rt, (g1, b[1], b[2], b[3]))); nsplit += 1
        else: segs.append((it['k'], 'S', it['raw'], b))
    segs.sort(key=lambda s: ((s[3][1] + s[3][3]) / 2, s[3][0]))
    order, cur = [], []
    def flush():
        if not cur: return
        if mode == 'b': order.extend([s for s in cur if s[1] == 'L'] + [s for s in cur if s[1] == 'R']); return
        us = [{'b': s[3]} for s in cur]; groups = []
        mL = min([s[3][0] for s in cur if s[1] == 'L'] or [0]); mR = min([s[3][0] for s in cur if s[1] == 'R'] or [0])
        def cont(s): return s[3][0] > (mL if s[1] == 'L' else mR) + 2 * ch      # 缩进的续行（折行、签名）
        for r in _rows(us):
            if not groups or not all(cont(cur[i]) for i in r): groups.append([cur[i] for i in r])
            else: groups[-1].extend(cur[i] for i in r)
        for g in groups: order.extend(sorted([s for s in g if s[1] == 'L'], key=lambda s: s[3][1]) + sorted([s for s in g if s[1] == 'R'], key=lambda s: s[3][1]))
    for sg in segs:
        if sg[1] == 'S': flush(); cur.clear(); order.append(sg)
        else: cur.append(sg)
    flush()
    regk = {it['k'] for it in reg}
    tail = {}; prev = None
    for k in range(kmin, kmax + 1):
        if k in regk: prev = k
        else: tail.setdefault(prev, []).append(lines[k][1])
    block = []
    for sg in order:
        block.append(sg[2])
        if sg[0] == int(sg[0]): block += tail.get(int(sg[0]), [])
    old = [lines[k][1] for k in range(kmin, kmax + 1)]
    if block == old: return pg, None
    assert sorted(''.join(_n(x) for x in block)) == sorted(''.join(_n(x) for x in old)), '方向5 增删了字'
    si = lines[kmin][0]
    sec_lines = [l for (s_, l) in lines if s_ == si]
    first = [k for k, (s_, _) in enumerate(lines) if s_ == si][0]
    new = sec_lines[:kmin - first] + block + sec_lines[kmax - first + 1:]
    out = deepcopy(pg); out['sections'][si] = dict(out['sections'][si], text='\n'.join(new))
    return out, {'规则': '5' + mode, '切开': nsplit, '空白带': (round(g0), round(g1)), '区域行数': len(reg)}
