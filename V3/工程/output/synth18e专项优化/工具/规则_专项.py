"""synth18e 专项规则（加在组合一之后）。全部只看本页识别文字、本页百度坐标／置信度、本页红章检测框（call_summary 的 seal_boxes）
和流水线本来就有的本页印章文字字段（seal），不看参照，不按文件名或页码写。
  乙1 发票印章文字移出正文（删字，删的是监制章／发票专用章里的文字，不是正文）
  乙2 印章区超高字块移出正文（删字，删的是弧形章文字）
  乙3 无百度文字块支撑的页尾短行移出正文（删字，删的是文档解析从骑缝章／印章里读出的碎片）
  丙  发票页边竖排注记回补（加字，加的是百度高精度已读出、但上游丢掉的页边竖排注记，原样追加到页尾）
  丁  第三版 D 层正文保护（在第三版 D 层判定里排除长正文块；作用是少删字）
  戊  行尾括号截断回补（加字，只补右括号，来源是同一行的百度高精度文字块）"""
import json, re, inspect
from statistics import median
import phys_order as PO
import 规则 as G
SEALCHK = re.compile(r'[:：]')
CJK = lambda c: '\u4e00' <= c <= '\u9fff'
PAGE_NO = re.compile(r'^第\d+页(共\d+页)?$|^\d+/\d+$|^-?\d+-?$')
def lcs_sub(a, b):
    """最长公共子串长度"""
    best = 0; prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]: cur[j] = prev[j - 1] + 1; best = max(best, cur[j])
        prev = cur
    return best
def in_box(u, boxes):
    cx, cy = u['x'] + u['w'] / 2, u['y'] + u['h'] / 2
    return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)
def line_info(pg, units):
    lines, P, lid, own = PO.locate(pg, units); info = []
    for li, ln in enumerate(lines):
        idx = [i for i in range(len(P)) if lid[i] == li]; ow = [own[i] for i in idx if own[i] >= 0]
        info.append({'文字': PO.norm(ln), '字数': len(idx), '认领': len(ow), '块': sorted(set(ow))})
    return lines, info
def drop_lines(pg, drop):
    out, k = [], 0
    for s in pg.get('sections') or []:
        ls = (s.get('text') or '').split('\n'); keep = [l for j, l in enumerate(ls) if (k + j) not in drop]; k += len(ls)
        if keep and any(l.strip() for l in keep): out.append({**s, 'text': '\n'.join(keep)})
        elif not (s.get('text') or '').strip(): out.append(s)
    return {**pg, 'sections': out}
# ---------------- 乙 印章碎片 ----------------
def r_印章(pg, units, boxes, opt):
    seal = PO.norm(pg.get('seal') or ''); lines, info = line_info(pg, units); kind = G.版式(pg); drop = {}; hs = [u['h'] for u in units if not u['vert']] or [1]; med = median(hs)
    for li, x in enumerate(info):
        t = x['文字']
        if not t or PAGE_NO.match(t): continue
        if opt.get('乙1') and kind == '发票' and boxes and seal and x['块'] and x['认领'] >= 0.6 * x['字数'] and not SEALCHK.search(t):
            if all(in_box(units[u], boxes) for u in x['块']) and lcs_sub(t, seal) >= max(4, 0.6 * len(t)): drop[li] = '乙1'; continue
        if opt.get('乙2') and boxes and seal and x['块'] and x['认领'] >= 0.6 * x['字数'] and not SEALCHK.search(t):
            us = [units[u] for u in x['块']]
            if all(in_box(u, boxes) and not u['vert'] and u['h'] >= 2 * med and (u['prob'] or 1) < 0.97 for u in us) and lcs_sub(t, seal) >= max(3, 0.5 * len(t)): drop[li] = '乙2'; continue
    if opt.get('乙3') and boxes:
        for li, x in enumerate(info):
            t = x['文字']
            if not t or x['认领'] or not (1 <= len(t) <= 8) or not all(CJK(c) for c in t): continue
            if any(t in PO.norm(u['t']) for u in units): continue
            rest = [(j, y) for j, y in enumerate(info) if j > li and y['文字']]
            if all(PAGE_NO.match(y['文字']) or j in drop or (not y['认领'] and len(y['文字']) <= 8) for j, y in rest): drop[li] = '乙3'
    if not drop: return pg, None
    return drop_lines(pg, set(drop)), {'删除行': [(lines[i], r) for i, r in sorted(drop.items())]}
# ---------------- 丙 发票页边竖排注记回补 ----------------
MARGIN = re.compile(r'^[\u4e00-\u9fff]{2,8}[:：][0-9A-Za-z\-]{2,}$')
def r_页边回补(pg, units):
    if G.版式(pg) != '发票': return pg, None
    Wd = max(u['x'] + u['w'] for u in units); body = PO.norm(''.join(s.get('text') or '' for s in pg['sections'])); add = []
    for u in units:
        cx = u['x'] + u['w'] / 2
        if u['vert'] and (cx < 0.06 * Wd or cx > 0.94 * Wd) and (u['prob'] or 0) >= 0.95 and MARGIN.match(PO.norm(u['t'])) and PO.norm(u['t']) not in body:
            add.append(u['t'])
    if not add: return pg, None
    return {**pg, 'sections': (pg.get('sections') or []) + [{'section': 'body', 'text': t} for t in add]}, {'回补': add}
# ---------------- 戊 行尾括号截断回补 ----------------
CLOSE = {'(': ')', '（': '）'}
def r_括号回补(pg, units):
    secs = []; log = []
    for s in pg.get('sections') or []:
        ls = (s.get('text') or '').split('\n'); new = []
        for ln in ls:
            t = PO.norm(ln)
            if t and t.count('(') > t.count(')'):
                for u in units:
                    ut = PO.norm(u['t'])
                    if not u['vert'] and (u['prob'] or 0) >= 0.98 and ut.startswith(t) and len(ut) > len(t) and set(ut[len(t):]) <= {')'}:
                        lastopen = [c for c in ln if c in CLOSE][-1]; ln = ln.rstrip() + CLOSE[lastopen] * (len(ut) - len(t)); log.append(ln); break
            new.append(ln)
        secs.append({**s, 'text': '\n'.join(new)})
    if not log: return pg, None
    return {**pg, 'sections': secs}, {'回补': log}
# ---------------- 丁 第三版 D 层正文保护：生成带保护的 noise_page ----------------
def make_guarded_noise(V):
    src = inspect.getsource(V.noise_page)
    old = "if not u['vert'] and u['h'] >= 2.5 * med and u['prob'] is not None and u['prob'] < 0.9: flag.setdefault(i, 'D')"
    assert old in src
    src = src.replace(old, "if not u['vert'] and u['h'] >= 2.5 * med and u['prob'] is not None and u['prob'] < 0.9 and not _正文长块(u): flag.setdefault(i, 'D')")
    ns = dict(V.__dict__); ns['_正文长块'] = lambda u: len(u['t']) >= 12 and sum(map(CJK, u['t'])) >= 0.5 * len(u['t'])
    exec(src, ns); return ns['noise_page']
def apply(pg, units, boxes, cfg):
    if units is None or not pg.get('sections'): return pg, {}
    log = {}
    if any(cfg.get(k) for k in ('乙1', '乙2', '乙3')): pg, log['印章'] = r_印章(pg, units, boxes, cfg)
    if cfg.get('丙'): pg, log['页边回补'] = r_页边回补(pg, units)
    if cfg.get('戊'): pg, log['括号回补'] = r_括号回补(pg, units)
    return pg, {k: v for k, v in log.items() if v}
