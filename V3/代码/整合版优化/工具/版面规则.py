"""整合版内部的两条通用规则（不看参照、不按文件名或页码；只用本页输出文字、本页去章图高精度行框、本地印章框）。
规则一 按坐标分栏重排（行序）：把输出的每一行对到去章图高精度的行框，按纵向重叠（≥50% 较矮行高）聚成物理行，
       物理行之间自上而下，同一物理行内自左而右输出。双栏签字区（甲方｜乙方）、发票截图里左右并排的字段会按“同一行左→右”读出。
       对不上行框的输出行跟在前一行后面，不丢字、不增字（只改顺序）。表格行连成的块整体移动，块内顺序不变。
规则二 章影过滤：输出行对应的行框有 ≥50% 面积落在本地检测的红章框内，且该行框置信度 <0.90，则删掉该行。
规则二b（可选变体）：落在红章框内、且整行是印章用语（以 专用章／公章／合同章／财务章／发票章 结尾，≤12 字）的行，不论置信度都删掉。"""
from copy import deepcopy
import re
def _bg(s): return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else ({s} if s else set())
def _rows(pg):
    return [(s.get('section') or 'body', ln) for s in pg.get('sections') or [] for ln in (s.get('text') or '').split('\n')]
def _regroup(rows, pg):
    secs = []
    for lab, l in rows:
        if secs and secs[-1]['section'] == lab: secs[-1]['text'] += '\n' + l
        else: secs.append({'section': lab, 'text': l})
    out = deepcopy(pg); out['sections'] = secs; return out
def match(rows, units, norm):
    """每个输出行 → 最佳行框下标（字二元组覆盖率≥0.6），否则 None。"""
    ub = [(_bg(u['t']), u) for u in units]
    res = []
    for _, l in rows:
        n = norm(l); b = _bg(n)
        if not n: res.append(None); continue
        best, bi = 0.0, None
        for i, (x, u) in enumerate(ub):
            if u['vert']: continue
            if n == u['t']: best, bi = 1.0, i; break
            o = len(b & x) / max(1, min(len(b), len(x)))
            if o > best: best, bi = o, i
        res.append(bi if best >= 0.6 else None)
    return res
def reorder(pg, units, norm, protect_table=True):
    """protect_table：表格行（section=table 或含制表符）连成的块整体移动、块内顺序不变（只按块首行定位）。"""
    if not units or not pg.get('sections'): return pg, None
    rows = _rows(pg); m = match(rows, units, norm)
    if sum(x is not None for x in m) < 4: return pg, None
    blocks = []
    for k, ((lab, l), i) in enumerate(zip(rows, m)):
        tab = protect_table and (lab == 'table' or '\t' in l)
        if tab and blocks and blocks[-1]['tab']:
            blocks[-1]['rows'].append((lab, l))
            if blocks[-1]['u'] is None and i is not None: blocks[-1]['u'] = i
        else:
            blocks.append({'rows': [(lab, l)], 'u': i, 'tab': tab, 'k': k})
    items, last = [], None
    for b in blocks:
        i = b['u']
        if i is not None:
            u = units[i]; last = (u['y'], u['h'], u['x']); items.append([u['y'], u['h'], u['x'], b['k'], b['rows'], True])
        elif last is None: items.append([-1, 1, -1, b['k'], b['rows'], False])
        else: items.append([last[0], last[1], last[2] + 0.001 * b['k'], b['k'], b['rows'], False])
    items.sort(key=lambda t: (t[0] + t[1] / 2, t[3]))
    groups = []
    for it in items:
        if groups:
            g = groups[-1][0]
            lo, hi = max(g[0], it[0]), min(g[0] + g[1], it[0] + it[1])
            if (it[5] and g[5] and hi - lo >= 0.5 * min(g[1], it[1])) or (not it[5] and it[0] == g[0]):
                groups[-1].append(it); continue
        groups.append([it])
    new = [r for g in groups for it in sorted(g, key=lambda t: (t[2], t[3])) for r in it[4]]
    if new == rows: return pg, None
    assert sorted(norm(l) for _, l in new) == sorted(norm(l) for _, l in rows)
    return _regroup(new, pg), {'行数': len(rows), '对上行框': sum(x is not None for x in m)}
SEALW = re.compile(r'(专用章|公章|合同章|财务章|发票章)$')
def _inside(u, b, thr=0.5):
    x0, y0, x1, y1 = b[:4]
    ix = max(0, min(u['x'] + u['w'], x1) - max(u['x'], x0)); iy = max(0, min(u['y'] + u['h'], y1) - max(u['y'], y0))
    return ix * iy / max(1, u['w'] * u['h']) >= thr
def seal_filter(pg, units, boxes, norm, conf=0.90, words=False):
    if not units or not boxes or not pg.get('sections'): return pg, None
    rows = _rows(pg); m = match(rows, units, norm); keep, drop = [], []
    for (lab, l), i in zip(rows, m):
        n = norm(l)
        if i is not None and any(_inside(units[i], b) for b in boxes) and len(n) <= len(units[i]['t']) + 2:
            p = units[i]['prob'] if units[i]['prob'] is not None else 1.0
            if p < conf or (words and len(n) <= 12 and SEALW.search(n)):
                drop.append(l); continue
        keep.append((lab, l))
    if not drop: return pg, None
    return _regroup(keep, pg), drop

def reorder_by_doc(pg, doc_pg, norm, protect_table=True, thr=0.5):
    """规则一′ 按文档解析的阅读顺序重排（文档解析是整合版本来就付费调用的一步，0 次额外调用）：
    每个输出行找文档解析里最相近的行（字二元组覆盖率≥thr；同样的行按出现先后依次占用），按（文档解析行号，行内位置，原序）排序；
    对不上的行跟在前一行后面；表格行连成的块整体移动。只改顺序，不增删字。"""
    if not doc_pg or not pg.get('sections'): return pg, None
    D = [norm(l) for s in doc_pg.get('sections') or [] for l in (s.get('text') or '').split('\n')]
    D = [d for d in D if d]
    if len(D) < 3: return pg, None
    DB = [_bg(d) for d in D]; used = [0] * len(D)
    rows = _rows(pg); blocks = []
    for k, (lab, l) in enumerate(rows):
        tab = protect_table and (lab == 'table' or '\t' in l)
        if tab and blocks and blocks[-1]['tab']: blocks[-1]['rows'].append((lab, l)); continue
        blocks.append({'rows': [(lab, l)], 'tab': tab, 'k': k})
    keys, last, hit = [], (-1, -1), 0
    for b in blocks:
        key = None
        for _, l in b['rows']:
            n = norm(l)
            if not n: continue
            bg = _bg(n); best, bj = 0.0, None
            for j, x in enumerate(DB):
                o = len(bg & x) / max(1, len(bg))
                if o > best + 1e-9 or (abs(o - best) < 1e-9 and bj is not None and used[j] < used[bj]): best, bj = o, j
            if bj is not None and best >= thr:
                pos = D[bj].find(n[:2]) if n[:2] in D[bj] else 0
                used[bj] += 1; key = (bj, pos); hit += 1
                break
        if key is None: key = (last[0], last[1] + 0.001)
        last = key; keys.append((key, b['k'], b['rows']))
    if hit < 3: return pg, None
    new = [r for _, _, rs in sorted(keys, key=lambda t: (t[0], t[1])) for r in rs]
    if new == rows: return pg, None
    assert sorted(norm(l) for _, l in new) == sorted(norm(l) for _, l in rows)
    return _regroup(new, pg), {'行数': len(rows), '对上': hit}
