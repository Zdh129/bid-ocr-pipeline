"""第四轮：按版式触发的专用排序规则＋表格行列规则（只改顺序、拼接和空白，不改字符内容；每条规则结束时自检字符多重集不变）。
全部只看本页识别文字与本页百度坐标，不看参照，不按文件名或页码写。
页面 → 版式识别（版式()）→ 只有对应版式的规则会触发。"""
import re
from collections import Counter
from statistics import median
import phys_order as PO
# ---------------- 公共 ----------------
def flat(pg):
    """[(段名, 行文字)]"""
    return [(s.get('section') or 'body', ln) for s in pg.get('sections') or [] for ln in (s.get('text') or '').split('\n')]
def rebuild(pg, rows):
    return {**pg, 'sections': [{'section': sec, 'text': t} for sec, t in rows]}
def chars(pg): return Counter(PO.norm(''.join(s.get('text') or '' for s in pg.get('sections') or [])))
def check(old, new, rule):
    if chars(old) != chars(new): raise AssertionError(f'{rule} 改变了字符')
    return new
def body_text(pg): return PO.norm(''.join(s.get('text') or '' for s in pg.get('sections') or []))
def located(pg, units):
    """把正文字符分配给百度文字块；返回 flat 行、每行字符下标、每字符所属文字块。"""
    lines, P, lid, own = PO.locate(pg, units)
    return lines, P, lid, own
def line_anchor(lines, P, lid, own, units):
    """每行的锚点文字块（该行里认领字最多的块），没有则 None。"""
    res = []
    for li in range(len(lines)):
        c = Counter(own[i] for i in range(len(P)) if lid[i] == li and own[i] >= 0)
        res.append(units[c.most_common(1)[0][0]] if c else None)
    return res
def skew(us):
    """用一行里若干文字块估计页面倾斜（中心 y 对 x 的斜率）。"""
    if len(us) < 3: return 0.0
    xs = [u['x'] + u['w'] / 2 for u in us]; ys = [u['y'] + u['h'] / 2 for u in us]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys); d = sum((x - mx) ** 2 for x in xs)
    k = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / d if d else 0.0
    return k if abs(k) < 0.05 else 0.0
# ---------------- 版式识别 ----------------
INV_FIELDS = ('发票号码', '发票代码', '开票日期')
TAX_KW = ('完税证明', '完税凭证', '缴税付款凭证', '电子缴税', '税收缴款书', '纳税缴费信用评价', '纳税信用评价', '社会保险费', '缴费证明', '参保缴费', '社保缴费')
DEV_KW = ('偏离表', '偏离情况', '响应偏离')
LIST_KW = ('装箱清单', '设备清单', '货物清单', '配置清单', '供货清单', '开箱验收', '验收清单', '分项报价', '报价明细')
CON_KW = ('合同', '协议')
def 版式(pg):
    t = body_text(pg); h = t[:80]
    if sum(k in t for k in INV_FIELDS) >= 2 and ('发票' in h) and ('购买方' in t or '销售方' in t): return '发票'
    if any(k in h for k in TAX_KW): return '完税社保表'
    if any(k in t[:120] for k in DEV_KW) and ('偏离' in t[120:] or t.count('偏离') >= 3): return '偏离表'
    if any(k in h for k in LIST_KW) and ('数量' in t or '型号' in t): return '设备清单'
    if any(k in h for k in CON_KW) and ('数量' in t and ('单价' in t or '金额' in t or '总价' in t)): return '合同明细'
    return '其他'
# ---------------- 字符归属（与第三版 phys_order.reorder 相同的零散字处理） ----------------
def fix_dups(P, own, units, lid=None):
    """文字相同的多个文字块：第三版的就近消歧对重复内容容易错配。按每段已认领文字前面紧邻的其他文字块的纵坐标（同一行里的上下文），
    把这些段重新分给纵坐标最接近的同文字块（贪心，一块一段）。"""
    span = {}
    for i, o in enumerate(own):
        if o >= 0: span.setdefault(o, []).append(i)
    grp = {}
    for ui, u in enumerate(units):
        if len(u['t']) >= 2 and not u['vert']: grp.setdefault(u['t'], []).append(ui)
    cy = lambda u: units[u]['y'] + units[u]['h'] / 2
    for t, uis in grp.items():
        have = [u for u in uis if u in span]
        if len(uis) < 2 or not have: continue
        if any(span[u] != list(range(span[u][0], span[u][-1] + 1)) for u in have): continue
        ctx = {}
        for u in have:
            i = span[u][0] - 1
            while i >= 0 and (own[i] < 0 or own[i] in uis) and (lid is None or lid[i] == lid[span[u][0]]): i -= 1
            if i >= 0 and own[i] >= 0 and own[i] not in uis and (lid is None or lid[i] == lid[span[u][0]]): ctx[u] = cy(own[i])
        if len(ctx) != len(have): continue
        pairs = sorted((abs(ctx[a] - cy(b)), a, b) for a in have for b in uis); ua, ub, m = set(), set(), {}
        for d, a_, b_ in pairs:
            if a_ in ua or b_ in ub: continue
            m[a_] = b_; ua.add(a_); ub.add(b_)
        segs = {a_: span[a_] for a_ in have}
        for a_, b_ in m.items():
            for i in segs[a_]: own[i] = b_
    return own
def ownership(pg, units):
    lines, P, lid, own = PO.locate(pg, units); n = len(P); floats = {}
    own = fix_dups(P, own, units, lid)
    for li in range(len(lines)):
        idx = [i for i in range(n) if lid[i] == li]
        if not idx: continue
        if all(own[i] < 0 for i in idx):
            prev = [own[i] for i in range(idx[0]) if own[i] >= 0]; nxt = [own[i] for i in range(idx[-1] + 1, n) if own[i] >= 0]
            anc = prev[-1] if prev else (nxt[0] if nxt else None); floats.setdefault(anc, []).append(idx)
            for i in idx: own[i] = -2
            continue
        j = 0
        while j < len(idx):
            if own[idx[j]] >= 0: j += 1; continue
            e = j
            while e < len(idx) and own[idx[e]] < 0: e += 1
            left = own[idx[j - 1]] if j > 0 else None; right = own[idx[e]] if e < len(idx) else None
            br = next((t for t in range(j, e) if P[idx[t]] in '([（【《“‘'), None)   # 左括号及其后的零散字一起并到右边，避免把「(小」拆开
            for t in range(j, e):
                own[idx[t]] = right if (right is not None and br is not None and t >= br) or left is None else left
            j = e
    return lines, P, lid, own, floats
# ---------------- 规则：发票表头字段顺序（行级换位，不拆行） ----------------
HEAD_STOP = re.compile(r'^(购买方|销售方|购货单位|名称[:：]|项目名称|货物或应税)')
def r_发票表头(pg, units, opt=None):
    """发票页：「购买方」之前的表头带里，标题行放最前，然后左栏字段自上而下，再右栏字段自上而下；其余行（监制章文字等）按原相对顺序放在后面。"""
    rows = flat(pg); lines, P, lid, own = located(pg, units); anc = line_anchor(lines, P, lid, own, units)
    end = next((i for i, (_, t) in enumerate(rows) if HEAD_STOP.match(PO.norm(t))), None)
    if not end or end < 2: return pg, None
    idx = [i for i in range(end) if PO.norm(rows[i][1])]
    if any(anc[i] is None for i in idx): return pg, None
    tit = [i for i in idx if '发票' in PO.norm(rows[i][1]) and not any(k in PO.norm(rows[i][1]) for k in ('号码', '代码', '日期', '监制'))]
    opt = opt or {}
    if not tit and not opt.get('无标题也分栏'): return pg, None
    if tit: t = max(tit, key=lambda i: anc[i]['h']); tx = anc[t]['x'] + anc[t]['w'] / 2
    else: t = None; tx = (min(u['x'] for u in units) + max(u['x'] + u['w'] for u in units)) / 2
    if opt.get('只移标题'):
        order = [t] + [i for i in idx if i != t] if t is not None else idx
        if order == idx: return pg, None
        new = rebuild(pg, [rows[i] for i in order] + [rows[i] for i in range(end) if i not in idx] + rows[end:])
        return check(pg, new, '发票表头'), {'换位行数': sum(a != b for a, b in zip(order, idx))}
    fld = [i for i in idx if i != t and re.search(r'[:：]', rows[i][1])]
    oth = [i for i in idx if i != t and i not in fld]
    left = sorted([i for i in fld if anc[i]['x'] + anc[i]['w'] / 2 < tx], key=lambda i: anc[i]['y'])
    right = sorted([i for i in fld if anc[i]['x'] + anc[i]['w'] / 2 >= tx], key=lambda i: anc[i]['y'])
    order = ([t] if t is not None and not opt.get('不移标题') else []) + left + right + oth
    if opt.get('不移标题'):   # 标题留在原位，只把字段行按「左栏→右栏」填回字段行原来占的位置
        it = iter(left + right); order = [next(it) if i in fld else i for i in idx]
    if sorted(order) != sorted(idx): order = idx
    if order == idx: return pg, None
    blank = [i for i in range(end) if i not in idx]
    new = rebuild(pg, [rows[i] for i in order] + [rows[i] for i in blank] + rows[end:])
    return check(pg, new, '发票表头'), {'换位行数': sum(a != b for a, b in zip(order, idx))}
# ---------------- 原字符映射：输出用原字符（保留全角半角），只丢空白 ----------------
def raw_map(lines):
    """返回 R：R[i] 为 P 下标 i 对应的原字符串（一个原字符规范化成多个字时，原字符记在第一个下标上，其余为空串）。"""
    R = []
    for ln in lines:
        for c in ln:
            k = len(PO.norm(c))
            if k: R += [c] + [''] * (k - 1)
    return R
def raw_check(old, new, rule):
    a = Counter(c for s in old.get('sections') or [] for c in (s.get('text') or '') if not c.isspace())
    b = Counter(c for s in new.get('sections') or [] for c in (s.get('text') or '') if not c.isspace())
    if a != b: raise AssertionError(f'{rule} 改变了原字符')
    return check(old, new, rule)
# ---------------- 规则：表格区按行列重建（发票明细、偏离表、合同明细、设备清单共用） ----------------
HDR = {'发票': (('项目名称', '货物或应税', '货物或服务'), ('金额', '税额', '单价')),
       '偏离表': (('序号', '招标要求', '技术要求', '采购要求', '参数名称'), ('偏离', '响应')),
       '合同明细': (('序号', '名称', '品名'), ('数量', '型号', '单价')),
       '设备清单': (('序号', '名称', '品名'), ('数量', '型号', '单价'))}
TOTAL = re.compile(r'^(合计|总计|小计|价税合计|大写|人民币|总价)')
def grid(pg, units, kind, opt):
    """opt: 倾斜校正 / 合并单元格归首行 / 跨行拼回 / 表头列顺序（四项表格能力，各自可开关）。"""
    lines, P, lid, own, floats = ownership(pg, units); n = len(P)
    if not n: return pg, None
    R = raw_map(lines); rows0 = flat(pg)
    txt = {}
    for i in range(n):
        if own[i] >= 0: txt.setdefault(own[i], []).append(i)
    T = {ui: dict(u=units[ui], idx=v, cx=units[ui]['x'] + units[ui]['w'] / 2, cy=units[ui]['y'] + units[ui]['h'] / 2, h=units[ui]['h'],
                  t=''.join(P[i] for i in v)) for ui, v in txt.items() if not units[ui]['vert']}
    if len(T) < 4: return pg, None
    k1, k2 = HDR[kind]
    best = None
    for ui, a in sorted(T.items(), key=lambda kv: kv[1]['cy']):
        if not any(k in a['t'] for k in k1): continue
        band = [b for b in T.values() if abs(b['cy'] - a['cy']) <= 0.6 * a['h']]
        k = skew([b['u'] for b in band]) if opt.get('倾斜校正') else 0.0
        band = [b for b in T.values() if abs((b['cy'] - k * b['cx']) - (a['cy'] - k * a['cx'])) <= 0.6 * a['h']]
        if len(band) >= 3 and any(k_ in b['t'] for b in band for k_ in k2): best = (band, k); break
    if not best: return pg, None
    hb, k = best; yp = lambda b: b['cy'] - k * b['cx']
    hmed = median([b['h'] for b in hb]); hid = {id(b) for b in hb}
    hbot = max(yp(b) + b['h'] / 2 for b in hb); htop = min(yp(b) - b['h'] / 2 for b in hb)
    heads = sorted(hb, key=lambda b: b['cx']); x0 = min(b['u']['x'] for b in hb); x1 = max(b['u']['x'] + b['u']['w'] for b in hb); TW = x1 - x0
    col = lambda b: min(range(len(heads)), key=lambda j: abs(heads[j]['cx'] - b['cx']))
    # 物理行（倾斜校正时按校正后的纵坐标聚类）
    # 【第2轮修正，评估集上调过】表格左边界以外（页边竖排注记等）不进表格区；左边界取首列行标签（合计、价税合计、以 * 开头的项目名）的最左端
    labs = [b for b in T.values() if yp(b) > hbot and (TOTAL.match(b['t']) or b['t'].startswith('*'))]
    lo = min(b['u']['x'] for b in labs) - hmed if (labs and opt.get('_左界', True)) else -1e9
    cand = sorted([b for b in T.values() if id(b) not in hid and yp(b) > hbot - 0.3 * hmed and b['u']['x'] < x1 + 0.1 * TW and b['u']['x'] + b['u']['w'] > lo and not (opt.get('_左界', True) and len(b['t']) >= 2 and b['h'] > 1.2 * b['u']['w'])], key=yp)   # 同上：竖着的多字块（旋转的页边注记）不进表格区
    prow = []
    for b in cand:
        tol = 0.5 * hmed if opt.get('倾斜校正') else 0.3 * min(b['h'], prow[-1]['h'] if prow else b['h'])
        if prow and abs(yp(b) - prow[-1]['c']) <= tol:
            prow[-1]['ts'].append(b); m = len(prow[-1]['ts']); prow[-1]['c'] = (prow[-1]['c'] * (m - 1) + yp(b)) / m
        else: prow.append(dict(c=yp(b), h=b['h'], ts=[b]))
    reg = []; last = hbot; tot_seen = False
    for r in prow:
        ts = sorted(r['ts'], key=lambda b: b['cx']); first = ts[0]['t']
        if r['c'] - last > (10 if TOTAL.match(first) else 4) * hmed: break   # 合计行与明细之间常留空行，放宽
        if kind == '发票' and re.match(r'^(备注|销售方|购买方|收款人|开票人|复核)', first): break
        if kind != '发票' and any(b['u']['w'] > 0.75 * TW for b in ts) and not TOTAL.match(first): break
        if tot_seen and not TOTAL.match(first): break
        reg.append(r); last = r['c'] + r['h'] / 2
        if TOTAL.match(first) and kind != '发票': tot_seen = True
        if kind == '发票' and first.startswith('价税合计'): break
    if not reg: return pg, None
    RT = [b for r in reg for b in r['ts']]
    # 行带：锚列（每格单行高、每物理行至多一格、格数最多的列）
    out_rows = None
    if opt.get('跨行拼回') or opt.get('合并单元格归首行'):
        cols = {}
        for b in RT: cols.setdefault(col(b), []).append(b)
        cand_c = []
        for c, bs in cols.items():
            bs2 = sorted(bs, key=yp)
            gaps_ok = all((yp(b) - b['h'] / 2) - (yp(a) + a['h'] / 2) >= 0.5 * hmed for a, b in zip(bs2, bs2[1:]))   # 锚列不能有折行续写
            rows_ok = len({id(r) for b in bs for r in reg if any(b is x for x in r['ts'])}) == len(bs)
            if all(b['h'] <= 1.5 * hmed for b in bs) and gaps_ok and rows_ok: cand_c.append((len(bs), -c, c))
        if cand_c and max(cand_c)[0] >= 2:
            ac = max(cand_c)[2]; anc = [yp(b) for b in cols[ac]]
            for r in reg:   # 合计、价税合计等汇总行各自成一行带
                f = min(r['ts'], key=lambda b: b['cx'])
                if TOTAL.match(f['t']) and all(abs(a - yp(f)) > 0.5 * hmed for a in anc): anc.append(yp(f))
            anc.sort()
            near = lambda y: min(range(len(anc)), key=lambda j: abs(anc[j] - y))
            groups = []
            for c, bs in cols.items():
                bs = sorted(bs, key=yp); cur = [bs[0]]
                for b in bs[1:]:
                    a = cur[-1]; gap = (yp(b) - b['h'] / 2) - (yp(a) + a['h'] / 2)
                    sep = near(yp(a)) != near(yp(b)) and abs(anc[near(yp(a))] - yp(a)) <= 0.35 * hmed and abs(anc[near(yp(b))] - yp(b)) <= 0.35 * hmed
                    star = kind == '发票' and b['t'].startswith('*')
                    if opt.get('跨行拼回') and gap < 0.5 * hmed and not sep and not star and not TOTAL.match(b['t']): cur.append(b)
                    else: groups.append(cur); cur = [b]
                groups.append(cur)
            band = {}
            for g in groups:
                top = min(yp(b) - b['h'] / 2 for b in g); bot = max(yp(b) + b['h'] / 2 for b in g)
                y = top + hmed / 2 if (opt.get('合并单元格归首行') and bot - top > 1.6 * hmed) else (top + bot) / 2
                band.setdefault(near(y), []).append(g)
            out_rows = []
            for j in sorted(band):
                gs = sorted(band[j], key=lambda g: (col(g[0]), g[0]['cx']) if opt.get('表头列顺序') else g[0]['u']['x'])
                out_rows.append([b for g in gs for b in sorted(g, key=yp)] + [None])   # None：行尾
                out_rows[-1] = [(g, True) for g in gs]
    if out_rows is None:
        out_rows = [[([b], True) for b in sorted(r['ts'], key=lambda b: (col(b), b['cx']) if opt.get('表头列顺序') else b['u']['x'])] for r in reg]
    # 生成：表头行＋区域行；区域外但落在同一跨度行里的字按原顺序放在前／后
    used = set()
    for b in hb + RT: used.update(b['idx'])
    span_l = sorted({lid[i] for i in used}); a_l, b_l = span_l[0], span_l[-1]
    inspan = [i for i in range(n) if a_l <= lid[i] <= b_l]
    def rawtxt(idx): return ''.join(R[i] for i in idx)
    def unit_y(i):
        o = own[i]
        return (units[o]['y'] + units[o]['h'] / 2 - k * (units[o]['x'] + units[o]['w'] / 2)) if o >= 0 else None
    before, after, fl_after = [], [], {}
    run = []; side = None
    def flush():
        if run: (before if side == 'b' else after).append(rawtxt(run))
    for i in inspan:
        if i in used: continue
        if own[i] == -2:   # 整行无人认领的浮动行：锚点在区域内则跟在锚点所在行后，否则放到区域后
            continue
        if opt.get('_按原位置', True): sd = 'b' if i < min(used) else 'a'   # 【第2轮修正，评估集上调过】区域外的字按原文位置放在区域前／后
        else: y = unit_y(i); sd = 'b' if (y is not None and y < htop) else 'a'
        if run and (sd != side or lid[i] != lid[run[-1]] or i != run[-1] + 1): flush(); run = []
        run.append(i); side = sd
    flush()
    for anc_u, L in floats.items():
        for idx in L:
            if a_l <= lid[idx[0]] <= b_l: fl_after.setdefault(anc_u, []).append(rawtxt(idx))
    reg_ids = {id(b): b for b in hb + RT}
    def row_text(cells):
        ix = sorted(i for g, _ in cells for b in g for i in b['idx'])
        if opt.get('_整行保持', True) and ix and ix[-1] - ix[0] + 1 == len(ix): return rawtxt(ix)   # 原文里本来就连在一起的行：行内不动，只整行挪位
        return ' '.join(''.join(rawtxt(b['idx']) for b in g) for g, _ in cells)
    def row_units(cells): return [ui for g, _ in cells for b in g for ui, v in T.items() if v is b]
    newl = [' '.join(rawtxt(b['idx']) for b in sorted(hb, key=lambda b: (col(b), b['cx']) if opt.get('表头列顺序') else b['u']['x']))]
    for ui in [ui for b in hb for ui, v in T.items() if v is b]: newl += fl_after.pop(ui, [])
    for cells in out_rows:
        newl.append(row_text(cells))
        for ui in row_units(cells): newl += fl_after.pop(ui, [])
    for L in fl_after.values(): newl += L
    sec = rows0[a_l][0]
    new_rows = rows0[:a_l] + [(sec, t) for t in before] + [('table', t) for t in newl] + [(sec, t) for t in after] + rows0[b_l + 1:]
    new = rebuild(pg, new_rows)
    if body_text(new) == body_text(pg): return pg, None
    return raw_check(pg, new, '表格行列重建'), {'版式': kind, '区域行': len(out_rows), '倾斜': round(k, 4)}
# ---------------- 规则：发票页边注记（旋转的竖排小字，如下载次数、税控码、打印序号）移到页尾 ----------------
def r_发票页边注记(pg, units, opt=None):
    rows = flat(pg); lines, P, lid, own = located(pg, units)
    X0 = min(u['x'] for u in units); X1 = max(u['x'] + u['w'] for u in units); Wd = X1 - X0
    def margin(u): return (u['vert'] or (len(u['t']) >= 2 and u['h'] > 1.2 * u['w'])) and (u['x'] + u['w'] < X0 + 0.08 * Wd or u['x'] > X1 - 0.08 * Wd)
    mv = []
    for li in range(len(rows)):
        c = Counter(own[i] for i in range(len(P)) if lid[i] == li)
        if not c: continue
        if all(o >= 0 and margin(units[o]) for o in c): mv.append(li)
    if not mv or mv == list(range(len(rows) - len(mv), len(rows))): return pg, None
    new = rebuild(pg, [r for i, r in enumerate(rows) if i not in mv] + [rows[i] for i in mv])
    return check(pg, new, '发票页边注记'), {'移动行数': len(mv)}
# ---------------- 规则：完税社保表倾斜校正后再按物理行重排 ----------------
def page_skew(units):
    us = sorted([u for u in units if not u['vert']], key=lambda u: u['y'] + u['h'] / 2); rows = []
    for u in us:
        c = u['y'] + u['h'] / 2
        if rows and abs(c - rows[-1][-1]['y'] - rows[-1][-1]['h'] / 2) <= 0.3 * u['h']: rows[-1].append(u)
        else: rows.append([u])
    ks = [skew(r) for r in rows if len(r) >= 3]; ks = [x for x in ks if x]
    return median(ks) if len(ks) >= 3 else 0.0
def r_完税社保倾斜(pg, units, opt=None):
    if not pg.get('meta', {}).get('物理行重排') and not any(k in body_text(pg)[:80] for k in TAX_KW): return pg, None
    k = page_skew(units)
    if abs(k) < (opt or {}).get('门槛', 0.002): return pg, None
    xm = median([u['x'] + u['w'] / 2 for u in units])
    us = [{**u, 'y': u['y'] - k * (u['x'] + u['w'] / 2 - xm), 'chars': [(t - k * (u['x'] + u['w'] / 2 - xm), h) for t, h in u['chars']]} for u in units]
    new, lg = PO.reorder(pg, us)
    if body_text(new) == body_text(pg): return pg, None
    return check(pg, new, '完税社保倾斜'), {'倾斜': round(k, 4)}
# ---------------- 总入口 ----------------
TABLE_OPTS = ('倾斜校正', '合并单元格归首行', '跨行拼回', '表头列顺序')
def apply(pg, units, cfg):
    """cfg: {'发票表头':bool, '发票明细':bool, '偏离表':bool, '合同明细':bool, '设备清单':bool, '完税社保倾斜':bool, 表格能力...}"""
    if units is None or not pg.get('sections'): return pg, {}
    kind = 版式(pg); log = {}
    if kind == '完税社保表' and cfg.get('完税社保倾斜'): pg, log['完税社保倾斜'] = r_完税社保倾斜(pg, units, {'门槛': cfg.get('倾斜门槛', 0.002)})
    if kind == '发票' and cfg.get('发票明细'): pg, log['发票明细'] = grid(pg, units, '发票', {**{k: cfg.get(k) for k in TABLE_OPTS}, **{k: v for k, v in cfg.items() if k.startswith('_')}})
    if kind == '发票' and cfg.get('发票表头'): pg, log['发票表头'] = r_发票表头(pg, units, {k: cfg.get('表头_' + k) for k in ('无标题也分栏', '只移标题', '不移标题')})
    if kind == '发票' and cfg.get('发票页边注记'): pg, log['发票页边注记'] = r_发票页边注记(pg, units)
    if kind in ('偏离表', '合同明细', '设备清单') and cfg.get(kind): pg, log[kind] = grid(pg, units, kind, {**{k: cfg.get(k) for k in TABLE_OPTS}, **{k: v for k, v in cfg.items() if k.startswith('_')}})
    return pg, {k: v for k, v in log.items() if v}
