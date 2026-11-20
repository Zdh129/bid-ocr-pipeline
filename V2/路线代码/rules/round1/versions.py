"""第 2 项：版2～版7 逐版叠加（事后/试算，纯本地，只用已缓存的 C、乙′、高精度字框、本地表格线、二次识别缓存）。
所有规则都是看过 synth_blind_g（开发集）之后写的，没有冻结，只作试算。
  版1 = 乙′（不加门控，已有结果）
  版2 = 版1 + 合计行切分（¥ 金额按 A 字框 y 离「合计」更近就移入合计行；「合计」被粘到价税合计行首时拆回）
  版3 = 版2 + 表头定位（紧挨明细区前/区内只由表头标签组成的行并回「项目名称」行）＋ 行/格边界检测（一行含两个 *类别* 且 y 分属两行 → 拆行；其余只记录）
  版4 = 版3 + 安全闸代替门控：每个被改写的段要满足 覆盖率≥85%、字符多重集合不变、行 y 跨度（超出 1.5 字高的部分之和）不变大，否则退回 C
  版5 = 版4 + 页边竖排融合原型（只用缓存的二次识别：竖条框 h≥3w、靠页边 8% 以内；页里还没有近似文字才追加；正文里与之近似的误识片段删掉）
  版6 = 版5 + 非发票有线表格行带校验（冻结的 layout.ruled_table，过同样的安全闸）
  版4′～版7′（事后修正链）：版4′ 安全闸只查覆盖率与字符多重集合（去掉 y 跨度项，因为它在开发集上误拦了两处正确改写）；
          版5′ 只删文档解析版面框中心落在竖条框（外扩 15pt）里的正文片段（原版按相似度删，误删了 p11 密码区片段）；版6′、版7′ 依次叠加。
  版7 = 版6 + 识读类字段约束（纳税人识别号 18 位里 O→0、I→1；发票明细区 ☆★ 与 * 连写时 ☆★→*）"""
import copy, json, re, sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import core as K
L, LP, G = K.L, K.LP, K.G
AMT = re.compile(r'[¥￥]\s*-?[\d,]+\.\d{2}')
HEJI_START = re.compile(r'\s*合\s*计(?!\s*[（(]?\s*大写)')
LABELS = ['项目名称', '规格型号', '税率/征收率', '征收率', '税率', '单位', '数量', '单价', '金额', '税额']
LAB_ONLY = re.compile(r'^(?:' + '|'.join(re.escape(x) for x in LABELS) + r')+$')
CAT = re.compile(r'\*[\u4e00-\u9fff]{1,12}\*')
nsp = lambda s: re.sub(r'\s', '', s)
ms = lambda s: Counter(ch for ch in s if not ch.isspace())

def sorted_a(ach): return [c for l in L.phys_lines(ach) for c in l]
def a_find(srt, s, start=0):
    """在 A（按物理行排好的字）里找字符串 s（去空白），返回字框列表。"""
    astr = ''.join(L.nch(c['ch']) for c in srt); s = ''.join(L.nch(ch) for ch in nsp(s)); i = astr.find(s, start)
    return srt[i:i + len(s)] if i >= 0 and s else None

# ---------------- 版2 合计行切分
def heji_split(t, ach, log):
    r = G.region_span(t)
    if not r: return None
    hs, ve = r; reg = t[hs:ve].rstrip('\n'); post = t[ve:]; rows = reg.split('\n')
    hj = [i for i, l in enumerate(rows) if i > 0 and HEJI_START.match(l)]
    ph = re.match(r'\s*(合\s*计)(?=\s*价\s*税\s*合\s*计)', post)
    if not hj and not ph: return None
    srt = sorted_a(ach); h = L.ch_h(ach) if ach else 10
    astr = ''.join(L.nch(c['ch']) for c in srt)
    heji_y = [L.cy(srt[m.start()]) for m in re.finditer('合计', astr) if not astr[max(0, m.start() - 2):m.start()].endswith('税')]
    moves = []
    for i, l in enumerate(rows):
        if i == 0 or i in hj: continue
        for m in AMT.finditer(l):
            box = a_find(srt, m.group())
            row_box = a_find(srt, nsp(l)[:4])
            if box and row_box and heji_y:
                y, yr = L.cy(box[0]), L.cy(row_box[0]); yh = min(heji_y, key=lambda v: abs(v - y))
                ok = abs(y - yh) < abs(y - yr); why = f'y={y:.1f} 合计y={yh:.1f} 行首y={yr:.1f}'
            else: ok, why = False, 'A 里定位不到，不动'
            if ok: moves.append((i, m, box[0]['bbox'][0]))
            log.append({'金额': m.group(), '移入合计行': ok, '依据': why})
    if not moves: return None
    rows2 = list(rows); toks = []
    for i in sorted({i for i, _, _ in moves}, reverse=True):
        l = rows2[i]
        for _, m, x in sorted([mv for mv in moves if mv[0] == i], key=lambda mv: -mv[1].start()):
            l = l[:m.start()] + ' ' + l[m.end():]; toks.append((x, m.group()))
        rows2[i] = re.sub(r' {2,}', ' ', l).strip()
    if hj:
        k = hj[-1]; ex = [(a_find(srt, m.group())[0]['bbox'][0] if a_find(srt, m.group()) else 1e9, m.group()) for m in AMT.finditer(rows2[k])]
        label = AMT.sub('', rows2[k]).strip()
        rows2[k] = ' '.join([label] + [s for _, s in sorted(ex + toks)])
    else:
        rows2.append(' '.join(['合计'] + [s for _, s in sorted(toks)])); post = post[:ph.start(1)] + post[ph.end(1):]
    new = t[:hs] + '\n'.join(x for x in rows2 if x.strip()) + '\n' + post
    L.check_moves(t, new)
    return new if nsp(new) != nsp(t) else None

# ---------------- 版3 表头定位 + 行/格边界检测
def header_fix(t, ach, log):
    r = G.region_span(t)
    if not r: return None
    hs, ve = r; reg = t[hs:ve].rstrip('\n'); rows = reg.split('\n'); pre = t[:hs]
    head = rows[0]; moved = []
    pl_start = pre.rfind('\n', 0, len(pre.rstrip('\n'))) + 1; pre_line = pre[pl_start:].strip('\n')
    if nsp(head) == '项目名称' and pre_line.strip() and LAB_ONLY.match(nsp(pre_line)):
        moved.append(pre_line.strip()); pre = pre[:pl_start]
    keep = [head]
    for l in rows[1:]:
        if l.strip() and LAB_ONLY.match(nsp(l)) and not re.search(r'\d', l): moved.append(l.strip())
        else: keep.append(l)
    if not moved: return None
    srt = sorted_a(ach)
    parts = [head.strip()] + moved
    xs = [(a_find(srt, p_)[0]['bbox'][0] if a_find(srt, p_) else 1e9 + i) for i, p_ in enumerate(parts)]
    keep[0] = ' '.join(p_ for _, p_ in sorted(zip(xs, parts)))
    log.append({'表头并回': moved})
    new = pre + '\n'.join(keep) + '\n' + t[ve:]
    L.check_moves(t, new)
    return new if nsp(new) != nsp(t) else None
def row_split(t, ach, log):
    r = G.region_span(t)
    if not r: return None
    hs, ve = r; rows = t[hs:ve].rstrip('\n').split('\n'); srt = sorted_a(ach); h = L.ch_h(ach) if ach else 10
    out, n_split = [], 0
    for l in rows:
        ms_ = [m for m in CAT.finditer(l)]
        starts = [m for m in ms_ if m.start() == 0 or l[m.start() - 1] == ' ' or not l[:m.start()].strip()]
        if len(ms_) >= 2:
            a1, a2 = a_find(srt, ms_[0].group()), a_find(srt, ms_[1].group())
            if a1 and a2 and abs(L.cy(a1[0]) - L.cy(a2[0])) > 0.5 * h and ms_[1].start() > 0:
                k = ms_[1].start(); out += [l[:k].rstrip(), l[k:].lstrip()]; n_split += 1
                log.append({'拆行': l[:40]}); continue
        rates = re.findall(r'(?<![\d.])\d{1,2}%|免税|不征税', l)
        if len(rates) >= 2: log.append({'检测到一行两个税率（只记录）': l[:40]})
        out.append(l)
    if not n_split: return None
    new = t[:hs] + '\n'.join(out) + '\n' + t[ve:]
    L.check_moves(t, new); return new

# ---------------- 版4 安全闸
def yspan_excess(text, ach):
    if not ach: return 0.0, 0.0
    srt = sorted_a(ach); h = L.ch_h(ach); loc = LP.locate2(text, srt)
    cov = sum(a is not None for _, _, a in loc) / max(1, len(loc))
    starts = [0] + [m.end() for m in re.finditer('\n', text)]
    byl = {}
    for i, ch, a in loc:
        if a is None: continue
        ln = sum(i >= s for s in starts) - 1; byl.setdefault(ln, []).append(L.cy(a))
    ex = sum(max(0.0, (max(v) - min(v)) - 1.5 * h) for v in byl.values())
    return ex, cov
def safety(old, new, ach, alt=False):
    if ms(old) != ms(new): return False, '字符多重集合变了'
    if alt:
        _, cov = yspan_excess(new, ach)
        return (cov >= L.COV_MIN), f'事后版只查覆盖率 {cov:.3f} 与字符多重集合'
    ex0, _ = yspan_excess(old, ach); ex1, cov = yspan_excess(new, ach)
    if cov < L.COV_MIN: return False, f'覆盖率 {cov:.3f} < 0.85'
    if ex1 > ex0 + 1e-6: return False, f'行 y 跨度超出变大 {ex0:.1f}→{ex1:.1f}'
    return True, f'通过（覆盖率 {cov:.3f}，y 超出 {ex0:.1f}→{ex1:.1f}）'

# ---------------- 版5 竖排融合
def reread_units(S):
    O = K.ROOT / 'output'
    f = {'synth_blind_g': K.RUN / 'reread/A_P2/results.json', 'blind20': O / 'blind20_frozen/reread/A_P2/results.json'}.get(S.name, O / S.name / 'reread/A_P2/results.json')
    return json.load(open(f))['units'] if Path(f).exists() else []
def best_ratio(s, text):
    if not s or not text: return 0.0
    n = len(s); best = 0.0
    for i in range(0, max(1, len(text) - n + 1)):
        r = SequenceMatcher(None, s, text[i:i + n], autojunk=False).ratio()
        if r > best: best = r
    return best
def dp_layouts(S, p):
    import attr2
    fs = attr2.pool_files(S)['文档解析']
    out = []
    for f in fs:
        for pg in json.load(open(f))['pages']:
            if int(pg['page_num']) + 1 == int(p):
                W = pg['meta']['page_width']
                out += [(l['text'], [v for v in l['position']], W) for l in pg['layouts'] if l.get('text')]
    return out
def vertical_fuse(S, pred, log, alt=False):
    N = S.scorers[0][1].norm; out = copy.deepcopy(pred)
    for u in reread_units(S):
        b = u['box_px']; w, hh = b[2] - b[0], b[3] - b[1]
        if hh < 3 * w: continue
        p = str(u['page'])
        if p not in out: continue
        W = S.lines(p)['W_pt'] if S.achars(p) else None
        cx = u['center_pt'][0]
        if not W or not (cx < 0.08 * W or cx > 0.92 * W): log.append({'页': int(p), '跳过': '不靠页边'}); continue
        r = u['r_text']; body = ''.join(N(x['text']) for x in out[p]['sections'])
        if best_ratio(N(r), body) >= 0.8: log.append({'页': int(p), '跳过': '页里已有近似文字', '竖排': r}); continue
        # 删正文里近似的误识片段：只看 ≥4 字的字母数字串（含 - :），与竖排字母数字部分相似度 ≥0.6
        alnum = lambda s: re.sub(r'[^0-9A-Za-z]', '', s)
        removed = None
        if alt:
            k = u['center_pt'][0] / ((b[0] + b[2]) / 2); box = [b[0] * k - 15, b[1] * k - 15, b[2] * k + 15, b[3] * k + 15]
            for txt, (x0, y0, ww, hh2), Wd in dp_layouts(S, p):
                s_ = W / Wd; cxl, cyl = (x0 + ww / 2) * s_, (y0 + hh2 / 2) * s_
                if box[0] <= cxl <= box[2] and box[1] <= cyl <= box[3]:
                    for x in out[p]['sections']:
                        i = x['text'].rfind(txt)
                        if i >= 0: x['text'] = x['text'][:i] + x['text'][i + len(txt):]; removed = txt; break
                if removed: break
        for x in ([] if alt else out[p]['sections']):
            for m in re.finditer(r'[0-9A-Za-z][0-9A-Za-z\-:：]{3,}', x['text']):
                if SequenceMatcher(None, alnum(m.group()), alnum(r)).ratio() >= 0.6 and m.group() not in N(r):
                    x['text'] = x['text'][:m.start()] + x['text'][m.end():]; removed = m.group(); break
            if removed: break
        out[p]['sections'].append({'section': 'body', 'text': r})
        log.append({'页': int(p), '追加竖排': r, '删除正文近似片段': removed})
    return out

# ---------------- 版7 识读字段约束
TAXID = re.compile(r'(纳税人识别号\s*[:：]?\s*)([0-9A-Z]{18})')
def field_fix(t, inv_page, log):
    def rep(m):
        s = m.group(2); s2 = s.replace('O', '0').replace('I', '1')
        if s2 != s: log.append({'税号': s, '改为': s2})
        return m.group(1) + s2
    t2 = TAXID.sub(rep, t)
    r = G.region_span(t2)
    if r:
        hs, ve = r; reg = t2[hs:ve]
        def rs(m):
            s = m.group()
            if '*' in s and re.search('[☆★]', s): log.append({'星号': s}); return re.sub('[☆★]', '*', s)
            return s
        reg2 = re.sub(r'[☆★*]{2,}', rs, reg); t2 = t2[:hs] + reg2 + t2[ve:]
    return t2

# ---------------- 叠加
def build(S, alt=False):
    V, LOG = {}, {}
    C = S.C
    V['C'] = C; V['版1'] = S.Yp
    def per_section(prev, fn, name, only_inv=True, tables=True):
        cur = copy.deepcopy(prev); lg = []
        for p in sorted(cur, key=int):
            if only_inv and not S.is_inv_page(p): continue
            for i, x in enumerate(cur[p]['sections']):
                if tables and x['section'] != 'table': continue
                l1 = []; new = fn(x['text'], S.achars(p), l1)
                if l1 or new is not None: lg.append({'页': int(p), '段': i, '改写': new is not None, '记录': l1})
                if new is not None: x['text'] = new
        LOG[name] = lg; return cur
    V['版2'] = per_section(V['版1'], heji_split, '版2')
    v3 = per_section(V['版2'], header_fix, '版3_表头'); lg_h = LOG['版3_表头']
    V['版3'] = per_section(v3, row_split, '版3'); LOG['版3'] = lg_h + LOG['版3']
    # 版4：安全闸——所有与 C 不同的段逐段检查，不过就退回 C
    v4 = copy.deepcopy(V['版3']); lg = []
    for p in v4:
        for i, x in enumerate(v4[p]['sections']):
            old = C[p]['sections'][i]['text']
            if x['text'] == old: continue
            ok, why = safety(old, x['text'], S.achars(p), alt)
            lg.append({'页': int(p), '段': i, '通过': ok, '原因': why})
            if not ok: x['text'] = old
    LOG['版4'] = lg; V['版4'] = v4
    lg = []; V['版5'] = vertical_fuse(S, V['版4'], lg, alt); LOG['版5'] = lg
    v6 = copy.deepcopy(V['版5']); lg = []
    for p in sorted(v6, key=int):
        if S.is_inv_page(p): continue
        for i, x in enumerate(v6[p]['sections']):
            if x['section'] != 'table' or not S.achars(p): continue
            l1 = []; new = L.ruled_table(x['text'], S.achars(p), S.lines(p), l1)
            if new is None: lg.append({'页': int(p), '段': i, '结果': (l1[-1] if l1 else {})}); continue
            ok, why = safety(x['text'], new, S.achars(p), alt)
            lg.append({'页': int(p), '段': i, '改写': ok, '安全闸': why})
            if ok: x['text'] = new
    LOG['版6'] = lg; V['版6'] = v6
    v7 = copy.deepcopy(V['版6']); lg = []
    for p in v7:
        if not S.is_inv_page(p): continue
        for x in v7[p]['sections']:
            l1 = []; x['text'] = field_fix(x['text'], True, l1)
            if l1: lg.append({'页': int(p), '记录': l1})
    LOG['版7'] = lg; V['版7'] = v7
    return V, LOG

if __name__ == '__main__':
    sets = sys.argv[1:] or ['synth_blind_g'] + K.SIX
    OUT = K.TS / '版本输出'; OUT.mkdir(exist_ok=True)
    for s in sets:
        S = K.Set(s); V, LOG = build(S); Va, LOGa = build(S, alt=True)
        for n in ('版4', '版5', '版6', '版7'): V[n + '′'] = Va[n]; LOG[n + '′'] = LOGa[n]
        res = {n: K.metrics(S, pr) for n, pr in V.items()}
        json.dump({'pred': V, 'log': LOG}, open(OUT / f'{s}_pred.json', 'w'), ensure_ascii=False, indent=1)
        json.dump(res, open(OUT / f'{s}_scores.json', 'w'), ensure_ascii=False, indent=1)
        for tag in res['C']:
            print(tag, ' | '.join(f"{n}:{K.E(res[n][tag]['主_发票'])}/{K.E(res[n][tag]['主_全套'])}/严{K.E(res[n][tag]['严_发票'])}" for n in res), flush=True)
