"""第三版之上的新通用方法（每种一个函数，都只改输出，不改计分口径；不看任何评估集参照）。
数据来源约定（防泄漏）：
  - 词典：jieba 自带通用词频表 dict.txt（通用语料，34.9 万词）。
  - 形近字：用系统字体 Noto Sans CJK 渲染字形后算位图相似度（与任何参照无关）。
  - 易错字对、阈值：只在开发集 synth18e、synth18b 上统计／选择（这两套不在八套、江苏、博凯里，不参与评分）。
"""
import json, re, sys, unicodedata
from functools import lru_cache
from pathlib import Path
from statistics import median
HERE = Path(__file__).resolve().parent; M = HERE.parent
sys.path.insert(0, str(M / '第三方库'))
sys.path.insert(0, '/workspace/pdf-test/baidu/output/第三版_规则逐条消融/工具'); import 框架 as F
PO, V = F.PO, F.V
sys.path.append('/workspace/pdf-test/baidu/output/候选路线_第二版/路线代码/rules/v2')
O = F.O
DEV = {'synth18e': (O / 'synth18e/orig/work/output/scheme_a', Path('/workspace/pdf-test/synth18e')), 'synth18b': (O / 'synth18b/orig/work/output/scheme_a', Path('/workspace/pdf-test/synth18b'))}
for s, (d, r) in DEV.items(): F.S.REFS[s] = r
_src0 = F.src
def src(s, p):
    if s in DEV: return (DEV[s][0], p)
    return _src0(s, p)
F.src = src
def cjk(c): return '\u4e00' <= c <= '\u9fff'
# ---------- 通用工具 ----------
def replace_chars(page, lines, P, rep):
    """rep: {P下标: 新字符串}（'' 表示删除）。保留原行其余字符。"""
    pos = 0; new_lines = []
    for ln in lines:
        out = []
        for c in ln:
            k = len(PO.norm(c))
            if k == 0: out.append(c); continue
            hit = [pos + t for t in range(k) if (pos + t) in rep]
            out.append(''.join(rep[h] for h in hit) if hit else c); pos += k
        new_lines.append(''.join(out))
    res, i = [], 0
    for s in page.get('sections') or []:
        m = len((s.get('text') or '').split('\n')); txt = '\n'.join(new_lines[i:i + m]); i += m
        if txt.strip() or not (s.get('text') or '').strip(): res.append({**s, 'text': txt})
    return {**page, 'sections': res}
def map_lines(page, fn):
    secs = []
    for s in page.get('sections') or []:
        secs.append({**s, 'text': '\n'.join(fn(l) for l in (s.get('text') or '').split('\n'))})
    return {**page, 'sections': secs}
# ---------- M1 通用词典＋形近字的上下文纠错 ----------
_DICT = None
def wdict():
    global _DICT
    if _DICT is None:
        _DICT = {}
        for line in open(M / '第三方库/jieba/dict.txt', encoding='utf-8'):
            w, f = line.split()[:2]; _DICT[w] = int(f)
    return _DICT
_BY2 = None
def by_pair():
    """二字词索引：(位置, 已知字) → [(另一字, 频次)]"""
    global _BY2
    if _BY2 is None:
        _BY2 = {}
        for w, f in wdict().items():
            if len(w) == 2 and cjk(w[0]) and cjk(w[1]):
                _BY2.setdefault((0, w[0]), []).append((w[1], f)); _BY2.setdefault((1, w[1]), []).append((w[0], f))
    return _BY2
@lru_cache(maxsize=200000)
def glyph(c):
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np
    f = ImageFont.truetype('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', 40, index=2) if Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc').exists() else ImageFont.truetype('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc', 40, index=2)
    im = Image.new('L', (48, 48), 0); ImageDraw.Draw(im).text((4, -6), c, fill=255, font=f)
    return (np.asarray(im) > 100)
@lru_cache(maxsize=500000)
def gsim(a, b):
    import numpy as np
    def binary_dilation(X, iterations=1):
        Y = X.copy(); Y[1:] |= X[:-1]; Y[:-1] |= X[1:]; Y[:, 1:] |= X[:, :-1]; Y[:, :-1] |= X[:, 1:]; return Y
    A, B = glyph(a), glyph(b)
    if not A.any() or not B.any(): return 0.0
    Ad, Bd = binary_dilation(A, iterations=1), binary_dilation(B, iterations=1)
    return float(((A & Bd).sum() + (B & Ad).sum()) / (A.sum() + B.sum()))
def m1_dict(s, p, pg, u, prm):
    sim_t, ratio = prm.get('形近阈值', 0.8), prm.get('频次倍数', 200)
    D = wdict(); B2 = by_pair(); lines = PO.page_lines(pg); P = [c for l in lines for c in PO.norm(l)]
    rep = {}
    for i, c in enumerate(P):
        if not cjk(c): continue
        L = P[i - 1] if i > 0 and cjk(P[i - 1]) else None; R = P[i + 1] if i + 1 < len(P) and cjk(P[i + 1]) else None
        if not L and not R: continue
        cur = max(D.get((L or '') + c, 0) if L else 0, D.get(c + (R or ''), 0) if R else 0)
        best = None
        cands = {}
        if L:
            for c2, f in B2.get((0, L), []): cands[c2] = max(cands.get(c2, 0), f)
        if R:
            for c2, f in B2.get((1, R), []): cands[c2] = max(cands.get(c2, 0), f)
        for c2, f in cands.items():
            if c2 == c or f < ratio * max(cur, 1): continue
            if gsim(c, c2) < sim_t: continue
            if not best or f > best[1]: best = (c2, f)
        if best: rep[i] = best[0]
    return replace_chars(pg, lines, P, rep) if rep else pg
# ---------- M2 易错字对（只从开发集统计） ----------
PAIRS = {}
def m2_pairs(s, p, pg, u, prm):
    if not PAIRS: return pg
    lines = PO.page_lines(pg); P = [c for l in lines for c in PO.norm(l)]
    rep = {i: PAIRS[c] for i, c in enumerate(P) if c in PAIRS}
    return replace_chars(pg, lines, P, rep) if rep else pg
# ---------- M3 阅读顺序重排的多种门槛／参数 ----------
def m3_order(s, p, pg, u, prm):
    if u is None: return pg
    gate = prm['门槛']
    if gate == '表格页':
        import page_type
        if page_type.tag_page(pg)['页面类型'] != '表格' or V.is_tax_form(pg): return pg
    elif gate == '普通文字页':
        import page_type
        if page_type.tag_page(pg)['页面类型'] != '普通文字': return pg
    elif gate == '全部页':
        if V.is_tax_form(pg): return pg
    elif gate == '税务表单_改k':
        if not V.is_tax_form(pg): return pg
        return None  # 由调用方从第二版重算
    new, _ = PO.reorder(pg, u, k=prm.get('k', 0.3))
    return new
# ---------- M4 标点与空白归一化（只改输出） ----------
def m4_punct(s, p, pg, u, prm):
    kind = prm['类型']
    if kind == '弯引号→直引号': return map_lines(pg, lambda l: l.translate(str.maketrans({'“': '"', '”': '"', '‘': "'", '’': "'"})))
    if kind == '直引号→弯引号':
        def f(l):
            out, o = [], True
            for c in l:
                if c == '"': out.append('“' if o else '”'); o = not o
                else: out.append(c)
            return ''.join(out)
        return map_lines(pg, f)
    if kind == '长横线→连字符': return map_lines(pg, lambda l: re.sub('[—–－]', '-', l))
    if kind == '中文间英文句点→句号': return map_lines(pg, lambda l: re.sub(r'(?<=[\u4e00-\u9fff])\.(?=[\u4e00-\u9fff]|$)', '。', l))
    if kind == '去行内空白': return map_lines(pg, lambda l: re.sub(r'\s+', '', l))
    if kind == '全角转半角': return map_lines(pg, lambda l: unicodedata.normalize('NFKC', l))
    if kind == '中文间英文逗号分号冒号→全角': return map_lines(pg, lambda l: re.sub(r'(?<=[\u4e00-\u9fff]),(?=[\u4e00-\u9fff])', '，', re.sub(r'(?<=[\u4e00-\u9fff]);', '；', re.sub(r'(?<=[\u4e00-\u9fff]):', '：', l))))
    if kind == '方括号统一为【】': return map_lines(pg, lambda l: l.replace('[', '【').replace(']', '】'))
    raise ValueError(kind)
# ---------- M5 百度置信度阈值 ----------
def m5_prob(s, p, pg, u, prm):
    if u is None: return pg
    t = prm['阈值']; lines, P, lid, own = PO.locate(pg, u)
    drop = [i for i in range(len(P)) if own[i] >= 0 and u[own[i]]['prob'] is not None and u[own[i]]['prob'] < t and (prm.get('仅英文') is False or not cjk(P[i]) or not prm.get('仅英文'))]
    if prm.get('仅英文'): drop = [i for i in drop if not cjk(P[i])]
    return PO.delete_chars(pg, lines, P, lid, drop) if drop else pg
# ---------- M6 金额／日期／编号格式校验 ----------
DIG_FIX = {'O': '0', 'o': '0', 'D': '0', 'l': '1', 'I': '1', '|': '1', 'Z': '2', 'S': '5', 'B': '8'}
def m6_format(s, p, pg, u, prm):
    def f(l):
        l2 = re.sub(r'(?<=\d)[OoDlI|ZSB](?=\d)', lambda m: DIG_FIX[m.group(0)], l)   # 两侧都是数字的字母
        if prm.get('日期'): l2 = re.sub(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', r'\1年\2月\3日', l2)
        if prm.get('金额千分位'): l2 = re.sub(r'(?<=\d)[，,．](?=\d{3}(\D|$))', ',', l2)
        return l2
    return map_lines(pg, f)
# ---------- M7 重复行去重 ----------
def m7_dedup(s, p, pg, u, prm):
    seen = set(); prev = None; secs = []
    for sct in pg.get('sections') or []:
        keep = []
        for l in (sct.get('text') or '').split('\n'):
            n = PO.norm(l)
            if len(n) >= prm.get('最短', 4) and ((prm['范围'] == '相邻' and n == prev) or (prm['范围'] == '页内' and n in seen)): continue
            keep.append(l)
            if n: prev = n; seen.add(n)
        secs.append({**sct, 'text': '\n'.join(keep)})
    return {**pg, 'sections': secs}
# ---------- M8 印章区域过滤变体 ----------
_SB = {}
def seal_boxes(s, p):
    d, q = src(s, int(p)); k = (str(d), q)
    if k not in _SB:
        cs = Path(d) / 'call_summary.json'; bx = []
        if cs.exists():
            for pgx in json.load(open(cs)).get('pages', []):
                if pgx.get('page') == q: bx = (pgx.get('route') or {}).get('seal_boxes') or []
        _SB[k] = bx
    return _SB[k]
def m8_seal(s, p, pg, u, prm):
    if u is None: return pg
    bx = seal_boxes(s, p)
    if not bx: return pg
    lines, P, lid, own = PO.locate(pg, u); seal = PO.norm(pg.get('seal') or '')
    inside = set()
    for i, w in enumerate(u):
        cx, cy = w['x'] + w['w'] / 2, w['y'] + w['h'] / 2
        if any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in bx):
            if prm['变体'] == '框内低置信度' and (w['prob'] or 1) < prm.get('阈值', 0.95): inside.add(i)
            if prm['变体'] == '框内且为印章串子串' and seal and len(w['t']) >= 2 and w['t'] in seal: inside.add(i)
    drop = [i for i in range(len(P)) if own[i] in inside]
    return PO.delete_chars(pg, lines, P, lid, drop) if drop else pg
# ---------- M9 页边竖排窄条低置信度（C 的变体：不限英文） ----------
def m9_edge(s, p, pg, u, prm):
    if u is None: return pg
    W = max(w['x'] + w['w'] for w in u); lines, P, lid, own = PO.locate(pg, u)
    flag = {i for i, w in enumerate(u) if w['vert'] and (w['prob'] or 1) < prm.get('阈值', 0.5) and (w['x'] < 0.06 * W or w['x'] + w['w'] > 0.94 * W)}
    drop = [i for i in range(len(P)) if own[i] in flag]
    return PO.delete_chars(pg, lines, P, lid, drop) if drop else pg
# ---------- M10 孤立单字行（低置信度） ----------
def m10_single(s, p, pg, u, prm):
    if u is None: return pg
    lines, P, lid, own = PO.locate(pg, u); drop = []
    for li in set(lid):
        idx = [i for i in range(len(P)) if lid[i] == li]
        if len(idx) == 1 and not P[idx[0]].isdigit() and own[idx[0]] >= 0 and (u[own[idx[0]]]['prob'] or 1) < prm.get('阈值', 0.8): drop += idx
    return PO.delete_chars(pg, lines, P, lid, drop) if drop else pg
METHODS = {'M1': m1_dict, 'M2': m2_pairs, 'M3': m3_order, 'M4': m4_punct, 'M5': m5_prob, 'M6': m6_format, 'M7': m7_dedup, 'M8': m8_seal, 'M9': m9_edge, 'M10': m10_single}
# ---------------- 第 2 轮新增 ----------------
import math
_ANG = {}
def angles(s, p):
    """本页每个文字块的倾斜角（度），按 (文字, left, top) 对应；从百度原始结果的 vertexes_location 算，只读缓存。"""
    d, q = src(s, int(p)); k = (str(d), q)
    if k not in _ANG:
        raw = Path(d) / 'raw'; fs = sorted(raw.glob(f'p{q:02d}_main_*.json')); m = {}
        ws = list(json.load(open(fs[0])).get('words_result') or []) if fs else []
        rm = raw / f'p{q:02d}_retry_mapped.json'
        if rm.exists():
            x = json.load(open(rm)); ws += x if isinstance(x, list) else []
        for w in ws:
            v = w.get('vertexes_location') or []; L = w.get('location')
            if len(v) >= 2 and L: m[(PO.norm(w.get('words')), L['left'], L['top'])] = abs(math.degrees(math.atan2(v[1]['y'] - v[0]['y'], v[1]['x'] - v[0]['x'])))
        _ANG[k] = m
    return _ANG[k]
def m8b_seal(s, p, pg, u, prm):
    """印章框内、文字是本页印章串的子串，并且（倾斜 ≥ 角度阈值 或 长度 ≤ 短片段阈值）——弯曲印章文字被拆成的倾斜碎片。"""
    if u is None: return pg
    bx = seal_boxes(s, p); seal = PO.norm(pg.get('seal') or '')
    if not bx or not seal: return pg
    A = angles(s, p); lines, P, lid, own = PO.locate(pg, u); flag = set()
    for i, w in enumerate(u):
        cx, cy = w['x'] + w['w'] / 2, w['y'] + w['h'] / 2
        if not any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in bx) or w['t'] not in seal: continue
        ang = A.get((w['t'], w['x'], w['y']), 0); ang = min(ang, 180 - ang)
        if ang >= prm.get('角度', 8) or len(w['t']) <= prm.get('短片段', 0): flag.add(i)
    drop = [i for i in range(len(P)) if own[i] in flag]
    return PO.delete_chars(pg, lines, P, lid, drop) if drop else pg
def m6b_format(s, p, pg, u, prm):
    """只在「纯数字串（含小数点、千分位、短横线）」内部修 O/o→0、l/I/|→1；串里有其他字母就不动（避免改坏字母数字混排的编号）。"""
    def fix(m):
        t = m.group(0)
        if re.search(r'[A-HJ-KM-NP-Za-hj-km-np-z]', t): return t
        return t.translate(str.maketrans({'O': '0', 'o': '0', 'l': '1', 'I': '1', '|': '1'}))
    return map_lines(pg, lambda l: re.sub(r'(?<![A-Za-z0-9])[0-9OolI|][0-9OolI|.,\-]*[0-9OolI|](?![A-Za-z0-9])', fix, l))
def m12_punct_lines(s, p, pg, u, prm):
    """整行只有符号（如「●」「|」「—」「·」）的行删除。"""
    def keep(l): n = PO.norm(l); return not n or any(c.isalnum() or cjk(c) for c in n)
    secs = [{**x, 'text': '\n'.join(l for l in (x.get('text') or '').split('\n') if keep(l))} for x in pg.get('sections') or []]
    return {**pg, 'sections': secs}
def m15_repeat(s, p, pg, u, prm):
    """行内紧挨着重复的片段（ABCDABCD，片段≥最短字数）只保留一份。"""
    n = prm.get('最短', 4)
    return map_lines(pg, lambda l: re.sub(r'(.{%d,}?)\1+' % n, r'\1', l))
def m16_lm(s, p, pg, u, prm):
    """字二元语言模型（由 jieba 通用词频表推出的字二元频次）＋形近字：替换后左右二元频次之和 ≥ 倍数×原来，且形近度 ≥ 阈值。"""
    global _BG
    try: _BG
    except NameError:
        _BG = {}
        for w, f in wdict().items():
            for a, b in zip(w, w[1:]):
                if cjk(a) and cjk(b): _BG[a + b] = _BG.get(a + b, 0) + f
    sim_t, ratio = prm.get('形近阈值', 0.9), prm.get('倍数', 100)
    B2 = by_pair(); lines = PO.page_lines(pg); P = [c for l in lines for c in PO.norm(l)]; rep = {}
    for i, c in enumerate(P):
        if not cjk(c): continue
        L = P[i - 1] if i > 0 and cjk(P[i - 1]) else ''; R = P[i + 1] if i + 1 < len(P) and cjk(P[i + 1]) else ''
        if not L or not R: continue
        sc = lambda x: _BG.get(L + x, 0) + _BG.get(x + R, 0)
        cur = sc(c); cands = {c2 for c2, _ in B2.get((0, L), [])} | {c2 for c2, _ in B2.get((1, R), [])}
        best = max(((sc(c2), c2) for c2 in cands if c2 != c and gsim(c, c2) >= sim_t), default=None)
        if best and best[0] >= ratio * max(cur, 1) and _BG.get(L + best[1], 0) > 0 and _BG.get(best[1] + R, 0) > 0: rep[i] = best[1]
    return replace_chars(pg, lines, P, rep) if rep else pg
METHODS.update({'M8b': m8b_seal, 'M6b': m6b_format, 'M12': m12_punct_lines, 'M15': m15_repeat, 'M16': m16_lm})
def m15b_repeat(s, p, pg, u, prm):
    """M15 改进：紧邻重复的片段须含汉字且至少 3 种不同字符（避免把「********」「0000」之类压掉）。"""
    n = prm.get('最短', 4)
    def f(l):
        def r(m):
            t = m.group(1)
            return t if (any(cjk(c) for c in t) and len(set(t)) >= 3) else m.group(0)
        return re.sub(r'(.{%d,}?)\1+' % n, r, l)
    return map_lines(pg, f)
METHODS['M15b'] = m15b_repeat
def m8c_seal(s, p, pg, u, prm):
    """M8 改进：印章框内、是印章串子串的文字块，只有当它所在的输出行完全由这类文字块组成（独立的印章碎片行）时才删；
    与「甲方：」「（盖章）」等正文同行的不删（避免删掉印章压着的落款单位名）。"""
    if u is None: return pg
    bx = seal_boxes(s, p); seal = PO.norm(pg.get('seal') or '')
    if not bx or not seal: return pg
    lines, P, lid, own = PO.locate(pg, u); flag = set()
    for i, w in enumerate(u):
        cx, cy = w['x'] + w['w'] / 2, w['y'] + w['h'] / 2
        if any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in bx) and len(w['t']) >= prm.get('最短', 2) and w['t'] in seal: flag.add(i)
    drop = []
    for li in set(lid):
        idx = [i for i in range(len(P)) if lid[i] == li]
        if idx and all(own[i] in flag for i in idx): drop += idx
    return PO.delete_chars(pg, lines, P, lid, drop) if drop else pg
METHODS['M8c'] = m8c_seal
