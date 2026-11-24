"""第三版·物理行阅读顺序重排（通用规则，不看参照，不按文件名或页码写）。
思路：第二版每页正文的字不变，只改顺序。用该页百度主识别结果里每个文字块的坐标，把正文字符分配回文字块，
再按文字块的纵向中心聚成物理行、行内按横坐标排序输出。竖排文字块（高明显大于宽）按单字坐标拆开，逐字归入所在物理行。
  1 文字块→正文定位：先长后短；先处理在正文里只出现一次的块，再按「物理上最近的已定位块」就近消歧；≤2 字的块只接受精确匹配。
  2 正文里没被任何块认领的字：同一行内的零散字并到相邻块（左括号并到右边、其余优先并到左边）；整行都没认领的（复读、补读来源）作为浮动行，
    紧跟在它前一个已定位字所在的物理行之后输出。
  3 没在正文里找到的文字块（第二版已删掉的印章字、杂字等）不输出。
只改顺序，不增删字：输出字符多重集与输入相同（自检断言）。"""
import json, unicodedata
from collections import Counter
from pathlib import Path
UNC = '【?】'
def norm(s):
    s = unicodedata.normalize('NFKC', s or '').replace(unicodedata.normalize('NFKC', UNC), '').replace(UNC, '')
    return ''.join(c for c in s if not c.isspace())
def approx_sub(u, t):
    n, m = len(u), len(t); prev = [0] * (m + 1); starts = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m; cst = [0] * (m + 1)
        for j in range(1, m + 1):
            c = 0 if u[i - 1] == t[j - 1] else 1
            best, st = prev[j - 1] + c, starts[j - 1]
            if prev[j] + 1 < best: best, st = prev[j] + 1, starts[j]
            if cur[j - 1] + 1 < best: best, st = cur[j - 1] + 1, cst[j - 1]
            cur[j], cst[j] = best, st
        prev, starts = cur, cst
    j = min(range(m + 1), key=lambda j: (prev[j], -j)); return prev[j], starts[j], j
def load_units(sa_dir, q):
    raw = Path(sa_dir) / 'raw'; fs = sorted(raw.glob(f'p{q:02d}_main_*.json'))
    if not fs: return None
    ws = list(json.load(open(fs[0])).get('words_result') or [])
    rm = raw / f'p{q:02d}_retry_mapped.json'
    if rm.exists():
        x = json.load(open(rm)); ws += x if isinstance(x, list) else (x.get('words_result') or [])
    out = []
    for w in ws:
        L = w.get('location'); t = norm(w.get('words'))
        if not L or not t: continue
        vert = L['height'] > 2.5 * L['width'] and len(t) >= 2
        chars = []
        if vert:
            ch = [c for c in (w.get('chars') or []) if norm(c.get('char'))]
            if len(ch) == len(t): chars = [(c['location']['top'], min(c['location']['height'], L['width'] * 1.5)) for c in ch]
            else: hh = L['height'] / len(t); chars = [(L['top'] + i * hh, hh) for i in range(len(t))]
        pr = (w.get('probability') or {}).get('average'); out.append(dict(t=t, x=L['left'], y=L['top'], w=L['width'], h=L['height'], vert=vert, chars=chars, prob=pr, retry=bool(w.get('_from_retry'))))
    return out
def page_lines(page):
    secs = page.get('sections') or []
    return [ln for s in secs for ln in (s.get('text') or '').split('\n')]
def locate(page, units, tol=0.34):
    """把正文字符分配给文字块。返回 (lines, P, lid, own)；own[i] 为认领该字的文字块下标，-1 表示无人认领。"""
    lines = page_lines(page)
    P, lid = [], []
    for i, ln in enumerate(lines):
        for c in norm(ln): P.append(c); lid.append(i)
    n = len(P); own = [-1] * n
    if not n or not units: return lines, P, lid, own
    span = {}
    def center(u): return (u['x'] + u['w'] / 2, u['y'] + u['h'] / 2)
    def occs(t):
        M = ''.join(c if own[i] < 0 else '\0' for i, c in enumerate(P)); res = []; s = M.find(t)
        while s >= 0: res.append((s, s + len(t))); s = M.find(t, s + 1)
        if res or len(t) <= 3: return res
        d, a, b = approx_sub(t, M)
        return [(a, b)] if b > a and d <= int(tol * len(t)) and '\0' not in M[a:b] else []
    def take(ui, a, b):
        for i in range(a, b): own[i] = ui
        span[ui] = (a, b)
    order = sorted(range(len(units)), key=lambda i: -len(units[i]['t']))
    for ui in order:
        if len(units[ui]['t']) < 2: continue
        o = occs(units[ui]['t'])
        if len(o) == 1: take(ui, *o[0])
    changed = True
    while changed:
        changed = False
        for ui in order:
            if ui in span: continue
            o = occs(units[ui]['t'])
            if not o: continue
            cx, cy = center(units[ui]); nb = [(abs(center(units[v])[1] - cy) * 2 + abs(center(units[v])[0] - cx), v) for v in span]
            if nb:
                v = min(nb)[1]; mid = sum(span[v]) / 2; a, b = min(o, key=lambda ab: abs((ab[0] + ab[1]) / 2 - mid))
            else: a, b = o[0]
            take(ui, a, b); changed = True
    return lines, P, lid, own
def reorder(page, units, k=0.3):
    lines, P, lid, own = locate(page, units)
    n = len(P); log = {'文字块': len(units), '已定位': len(set(o for o in own if o >= 0)), '浮动行': 0}
    log['未定位丢弃'] = len(units) - log['已定位']
    if not n or not units: return page, dict(log, 跳过='空页或无坐标')
    # 零散字归属
    float_lines = {}
    for li in range(len(lines)):
        idx = [i for i in range(n) if lid[i] == li]
        if not idx: continue
        if all(own[i] < 0 for i in idx):
            prev = [own[i] for i in range(idx[0]) if own[i] >= 0]; nxt = [own[i] for i in range(idx[-1] + 1, n) if own[i] >= 0]
            anc = prev[-1] if prev else (nxt[0] if nxt else None)
            float_lines.setdefault(anc, []).append(idx); log['浮动行'] += 1
            for i in idx: own[i] = -2
            continue
        j = 0
        while j < len(idx):
            if own[idx[j]] >= 0: j += 1; continue
            e = j
            while e < len(idx) and own[idx[e]] < 0: e += 1
            left = own[idx[j - 1]] if j > 0 else None; right = own[idx[e]] if e < len(idx) else None
            for t in range(j, e):
                c = P[idx[t]]
                tgt = right if (c in '([（【《“‘' and right is not None) or left is None else left
                own[idx[t]] = tgt
            j = e
    # 生成物理行
    toks = []
    for ui, u in enumerate(units):
        txt = ''.join(P[i] for i in range(n) if own[i] == ui)
        if not txt: continue
        if u['vert'] and u['chars']:
            m = len(u['chars'])
            for t, c in enumerate(txt):
                cy, ch = u['chars'][min(m - 1, int(t * m / len(txt)))]
                toks.append(dict(t=c, x=u['x'], c=cy + ch / 2, h=ch, ui=ui, v=True))
        else: toks.append(dict(t=txt, x=u['x'], c=u['y'] + u['h'] / 2, h=u['h'], ui=ui))
    # 先用普通文字块聚成物理行；竖排条带的单字再逐个归入中心最近的行（够近才归入，否则自成一行）
    norm_t = sorted([a for a in toks if not a.get('v')], key=lambda a: a['c']); vch = sorted([a for a in toks if a.get('v')], key=lambda a: a['c']); R = []
    for a in norm_t:
        if R and abs(a['c'] - R[-1]['c']) <= k * min(a['h'], R[-1]['h']):
            R[-1]['ts'].append(a); m = len(R[-1]['ts']); R[-1]['c'] = (R[-1]['c'] * (m - 1) + a['c']) / m
        else: R.append(dict(c=a['c'], h=a['h'], ts=[a]))
    for a in vch:
        best = min(R, key=lambda r: abs(r['c'] - a['c'])) if R else None
        if best is not None and abs(best['c'] - a['c']) <= k * min(a['h'], best['h']): best['ts'].append(a)
        else: R.append(dict(c=a['c'], h=a['h'], ts=[a]))
    R.sort(key=lambda r: r['c'])
    out = []
    for r in R:
        ts = sorted(r['ts'], key=lambda a: a['x']); out.append(' '.join(a['t'] for a in ts))
        for a in ts:
            for idx in float_lines.pop(a['ui'], []): out.append(''.join(P[i] for i in idx))
    for anc, L in float_lines.items():
        for idx in L: out.append(''.join(P[i] for i in idx))
    assert Counter(norm(''.join(out))) == Counter(P), '重排改变了字符'
    new = dict(page); new['sections'] = [{'section': 'body', 'text': '\n'.join(out)}]
    new.setdefault('meta', {}); new['meta'] = {**(page.get('meta') or {}), '物理行重排': True}
    return new, log

def delete_chars(page, lines, P, lid, drop):
    """按 P 下标删除字符，保留原行里其余字符（含空格、原始全半角）。返回新页。"""
    drop = set(drop); pos = 0; new_lines = []
    for ln in lines:
        keep = []
        for c in ln:
            k = len(norm(c))
            if k == 0: keep.append(c); continue
            if not all((pos + t) in drop for t in range(k)): keep.append(c)
            pos += k
        new_lines.append(''.join(keep))
    assert pos == len(P)
    # 按原分段切回去
    out, i = [], 0
    for s in page.get('sections') or []:
        m = len((s.get('text') or '').split('\n')); txt = '\n'.join(new_lines[i:i + m]); i += m
        if txt.strip() or not (s.get('text') or '').strip(): out.append({**s, 'text': txt})
    return {**page, 'sections': out}
