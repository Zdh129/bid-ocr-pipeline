"""旋转页阅读顺序规则（T-22，通用，不看文件名／页码／参照；0 次调用）。
1. 检测：读本页高精度原始返回（main＋retry_mapped），用每个行框四个顶点里「第 1 点→第 2 点」的方向判断阅读方向
   （0＝向右读，1＝向上读＝整页逆时针转了 90°，2＝倒置，3＝向下读＝顺时针 90°）。按字数加权投票，
   非 0 方向的票数 ≥60% 且投票字数 ≥30 才算旋转页。没有顶点时退回接口返回的 direction 字段。
2. 摆正：把每个行框换算到摆正后的坐标系。
3. 分联／分栏（递归 XY 切分；高度 >4 倍中位行高的超厚框（斜水印、大块印记）和 ≥50% 落在本地红章框内的框不参与切分，事后放回中心所在的块并单独成行）：先找竖向空白带（宽 ≥3 倍中位行高，两侧各 ≥3 个行框、各 ≥10% 字数）切成左右两联或多栏；
   切不开再找横向空白带（高 ≥1.5 倍中位行高）切成上下块，块内再试竖切。叶子块内按纵向重叠（≥50% 较矮行高）聚成物理行，
   行内自左而右。这样「标签：值」在同一物理行里会挨着输出，两联先左后右。
4. 用法：
   reorder_lines(pg, info)：只重排已有输出行（对上行框的行按新顺序，对不上的跟在前一行后），不增删字（整合版用）。
   rebuild(info)：输出行与行框几乎对不上（如把横排当竖排读、字已打乱）时，直接用本路线已付费的高精度行框文字按新顺序重建本页（候选路线_v2 用）。
未旋转的页：detect 返回 None，所有函数原样返回输入。"""
import json, math
from copy import deepcopy
from pathlib import Path
from statistics import median

def _norm(s):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFKC', s or '') if not c.isspace())

def _cls(v):
    dx, dy = v[1]['x'] - v[0]['x'], v[1]['y'] - v[0]['y']
    a = math.degrees(math.atan2(-dy, dx)) % 360
    return int(((a + 45) % 360) // 90)

def load(sa_dir, q):
    raw = Path(sa_dir) / 'raw'
    fs = sorted(raw.glob(f'p{q:02d}_main_*.json'))
    if not fs: return None
    r = json.load(open(fs[0]))
    ws = [dict(w, _retry=False) for w in r.get('words_result') or []]
    rm = raw / f'p{q:02d}_retry_mapped.json'
    if rm.exists():
        x = json.load(open(rm)); ws += [dict(w, _retry=True) for w in (x if isinstance(x, list) else x.get('words_result') or [])]
    W = Hh = None; seals = []
    cs = Path(sa_dir) / 'call_summary.json'
    if cs.exists():
        for pg in json.load(open(cs))['pages']:
            if pg['page'] == q: W, Hh, seals = pg['width_px'], pg['height_px'], pg['route'].get('seal_boxes') or []
    return {'words': ws, 'direction': r.get('direction'), 'W': W, 'H': Hh, 'seals': seals}

def detect(raw, share=0.6, min_chars=30):
    if not raw: return None
    votes = {}
    for w in raw['words']:
        v = w.get('vertexes_location'); t = _norm(w.get('words'))
        if v and len(v) >= 2 and len(t) >= 3: votes[_cls(v)] = votes.get(_cls(v), 0) + len(t)
    n = sum(votes.values())
    if n >= min_chars:
        c = max(votes, key=votes.get)
        return c if c != 0 and votes[c] / n >= share else None
    d = raw.get('direction')
    return d if d in (1, 2, 3) else None

def _upright(L, c, W, H):
    x0, y0, x1, y1 = L['left'], L['top'], L['left'] + L['width'], L['top'] + L['height']
    if c == 1: return (H - y1, x0, H - y0, x1)
    if c == 3: return (y0, W - x1, y1, W - x0)
    if c == 2: return (W - x1, H - y1, W - x0, H - y0)
    return (x0, y0, x1, y1)

def _inside(L, b, thr=0.5):
    x0, y0, x1, y1 = b[:4]
    ix = max(0, min(L['left'] + L['width'], x1) - max(L['left'], x0)); iy = max(0, min(L['top'] + L['height'], y1) - max(L['top'], y0))
    return ix * iy / max(1, L['width'] * L['height']) >= thr

def units(raw, c):
    W, H = raw['W'], raw['H']
    if W is None:
        W = max(w['location']['left'] + w['location']['width'] for w in raw['words']); H = max(w['location']['top'] + w['location']['height'] for w in raw['words'])
    out = []
    for w in raw['words']:
        t = _norm(w.get('words')); L = w.get('location')
        if not t or not L: continue
        inseal = any(_inside(L, b) for b in raw['seals'])
        if w['_retry'] and inseal: continue   # 补拼图里落在章框内的字（章文）不进重建
        out.append({'t': t, 'raw': w.get('words'), 'b': _upright(L, c, W, H), 'retry': w['_retry'], 'seal': inseal})
    return out

def _gaps(iv, lo, hi):
    iv = sorted(iv); g = []; cur = lo
    for a, b in iv:
        if a > cur: g.append((cur, a))
        cur = max(cur, b)
    return [x for x in g if x[0] > lo and x[1] < hi]

def order(us):
    """返回行框下标的阅读顺序（列表的列表：每个子列表是一条物理行，行内左→右）。"""
    if not us: return []
    lh = median([u['b'][3] - u['b'][1] for u in us]) or 1
    tot = sum(len(u['t']) for u in us)
    def cut(ix):
        if len(ix) < 2: return [ix]
        xs = [(us[i]['b'][0], us[i]['b'][2]) for i in ix]; ys = [(us[i]['b'][1], us[i]['b'][3]) for i in ix]
        for axis, iv, thr in ((0, xs, 3.0 * lh), (1, ys, 1.5 * lh)):
            lo, hi = min(a for a, _ in iv), max(b for _, b in iv)
            gs = sorted(_gaps(iv, lo, hi), key=lambda g: g[0] - g[1])
            for g0, g1 in gs:
                if g1 - g0 < thr: break
                m = (g0 + g1) / 2
                A = [i for i in ix if us[i]['b'][axis] < m]; B = [i for i in ix if us[i]['b'][axis] >= m]
                if axis == 0:
                    ca, cb = sum(len(us[i]['t']) for i in A), sum(len(us[i]['t']) for i in B)
                    if len(A) < 3 or len(B) < 3 or ca < 0.1 * tot or cb < 0.1 * tot: continue
                return cut(A) + cut(B)
        return [ix]
    big = [i for i in range(len(us)) if us[i]['b'][3] - us[i]['b'][1] > 4 * lh or us[i].get('seal')]   # 超厚框（斜水印、大块印记）和落在本地红章框内的框不参与切分
    leaves = cut([i for i in range(len(us)) if i not in big])
    for i in big:   # 放回中心点所在（或最近）的块
        cx, cy = (us[i]['b'][0] + us[i]['b'][2]) / 2, (us[i]['b'][1] + us[i]['b'][3]) / 2
        def dist(leaf):
            x0 = min(us[j]['b'][0] for j in leaf); x1 = max(us[j]['b'][2] for j in leaf)
            y0 = min(us[j]['b'][1] for j in leaf); y1 = max(us[j]['b'][3] for j in leaf)
            return max(0, x0 - cx, cx - x1) + max(0, y0 - cy, cy - y1)
        if leaves and leaves[0]: min(leaves, key=dist).append(i)
        else: leaves.append([i])
    rows = []
    for leaf in leaves:
        it = sorted(leaf, key=lambda i: (us[i]['b'][1] + us[i]['b'][3]) / 2)
        grp = []
        for i in it:
            b = us[i]['b']
            if grp and i not in big and grp[-1][0] not in big:
                g = grp[-1]; y0 = min(us[j]['b'][1] for j in g); y1 = max(us[j]['b'][3] for j in g)
                ov = min(y1, b[3]) - max(y0, b[1])
                if ov >= 0.5 * min(y1 - y0, b[3] - b[1]): g.append(i); continue
            grp.append([i])
        rows += [sorted(g, key=lambda i: us[i]['b'][0]) for g in grp]
    return rows

def info(sa_dir, q):
    raw = load(sa_dir, q); c = detect(raw)
    if c is None: return None
    us = units(raw, c)
    return {'方向': c, '接口direction': raw.get('direction'), 'units': us, 'rows': order(us)}

def _bg(s): return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else ({s} if s else set())

def _rows(pg): return [(s.get('section') or 'body', ln) for s in pg.get('sections') or [] for ln in (s.get('text') or '').split('\n')]

def _regroup(rows, pg):
    secs = []
    for lab, l in rows:
        if secs and secs[-1]['section'] == lab: secs[-1]['text'] += '\n' + l
        else: secs.append({'section': lab, 'text': l})
    out = deepcopy(pg); out['sections'] = secs; return out

def match_rate(pg, inf):
    rows = _rows(pg); m = _match(rows, inf['units'])
    n = [r for r in rows if _norm(r[1])]
    return sum(x is not None for x in m) / max(1, len(n))

def _match(rows, us, thr=0.6):
    ub = [_bg(u['t']) for u in us]; res = []
    for _, l in rows:
        n = _norm(l); b = _bg(n)
        if not n: res.append(None); continue
        best, bi = 0.0, None
        for i, x in enumerate(ub):
            if n == us[i]['t']: best, bi = 1.0, i; break
            o = len(b & x) / max(1, min(len(b), len(x)))
            if o > best: best, bi = o, i
        res.append(bi if best >= thr else None)
    return res

def reorder_lines(pg, inf):
    if not inf or not pg.get('sections'): return pg, None
    rank = {i: (r, k) for r, row in enumerate(inf['rows']) for k, i in enumerate(row)}
    rows = _rows(pg); m = _match(rows, inf['units'])
    if sum(x is not None for x in m) < 4: return pg, None
    keys, last = [], (-1, -1)
    for k, ((lab, l), i) in enumerate(zip(rows, m)):
        key = rank[i] if i is not None else (last[0], last[1] + 0.001)
        last = key; keys.append((key, k, (lab, l)))
    new = [r for _, _, r in sorted(keys, key=lambda t: (t[0], t[1]))]
    if new == rows: return pg, None
    assert sorted(_norm(l) for _, l in new) == sorted(_norm(l) for _, l in rows)
    return _regroup(new, pg), {'行数': len(rows), '对上': sum(x is not None for x in m)}

def rebuild(pg, inf):
    if not inf: return pg, None
    rows = [('body', inf['units'][i]['raw']) for row in inf['rows'] for i in row if not inf['units'][i].get('seal')]   # 章框内的字不进正文（章文另在 seal 字段）
    out = deepcopy(pg); out['sections'] = [{'section': 'body', 'text': '\n'.join(l for _, l in rows)}]
    return out, {'重建行数': len(rows)}

def apply(pg, sa_dir, q, mode='reorder', min_match=0.5):
    """mode='reorder'：只重排；mode='auto'：本页输出与行框对上的比例 <min_match 时改用 rebuild，否则重排。"""
    inf = info(sa_dir, q)
    if inf is None: return pg, None
    if mode == 'auto' and match_rate(pg, inf) < min_match:
        out, lg = rebuild(pg, inf); return out, {'方式': '重建', '方向': inf['方向'], **lg}
    out, lg = reorder_lines(pg, inf)
    return out, ({'方式': '重排', '方向': inf['方向'], **lg} if lg else None)
