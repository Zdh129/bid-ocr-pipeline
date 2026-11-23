"""T-23 v3 优化：三套集（盲测 25 页、新集 8 页、65 页的扫描件页）统一载入、参数化 v3、计分。
只读上游识别结果、参照与冻结规则文件；只写 v3优化/。0 次调用，必须用 离线跑.sh 运行。"""
import json, sys, hashlib, importlib.util
from copy import deepcopy
from pathlib import Path
sys.dont_write_bytecode = True
V = Path(__file__).resolve().parents[1]; H = V.parent
sys.path[:0] = [str(Path(__file__).parent), str(H / '工具'), str(H / '旋转页规则/工具')]
def _mod(name, path):
    s = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
C25 = _mod('C25', H / '工具/公共25.py')
C8 = _mod('C8', H / '工具/公共25.py')
import 难页特征 as F, 后处理_v2 as PP, 版面规则 as R, 旋转规则 as RR
sys.path.insert(0, '/workspace/pdf-test/baidu/output/整合版_裸高精度底座/工具')
import 整合框架 as G
import 公共65 as C65
A, PO = C25.A, C25.PO
# 冻结核对（组合.py、版面规则.py、难页特征.py、后处理_v2.py、旋转规则.py 一字未改）
for ln in (H / '新盲测集/冻结_规则sha256.txt').read_text(encoding='utf-8').splitlines():
    p = ln.split()
    if len(p) >= 2 and len(p[0]) == 64: assert hashlib.sha256((H / p[-1]).resolve().read_bytes()).hexdigest() == p[0]
assert hashlib.sha256((H / '旋转页规则/工具/旋转规则.py').read_bytes()).hexdigest() == (H / '旋转页规则/规则sha256.txt').read_text().split()[0]
EXTRA = 87.67   # 交第六轮每页多出的调用成本（¥/页，按 30 页实跑记录）
# ---------- 盲测 25 页 ----------
def _set25():
    six, integ = C25.load('第六轮新版'), C25.load('整合版')
    DOC = json.load(open(C25.ISO / '运行/orig/work/output/final/scheme_b_standard/pred_pages.json'))
    P = []
    for p in range(1, 31):
        P.append(dict(k=str(p), q=p, integ=integ[str(p)], six=six[str(p)], sa_o=C25.SA_O, sa_p2=C25.SA_P2, doc=DOC.get(str(p))))
    return P
# ---------- 新集 8 页 ----------
N = H / '新盲测集'; RUN = N / '运行_隔离副本' / '运行'
def _set8():
    单 = {p['新集页号']: p for p in json.load(open(N / '清单.json'))['页']}
    C8.REF_DIR = N / '参照草稿'; C8.页表 = {q: {'版式': 单[q]['版式_旧表']} for q in range(1, 9)}; C8.SCORED = list(range(1, 9))
    sa_o, sa_p2 = RUN / 'orig/work/output/scheme_a', RUN / 'P2/work/output/scheme_a'
    DOC = json.load(open(RUN / 'orig/work/output/final/scheme_b_standard/pred_pages.json'))
    six = json.load(open(N / '识别结果/第六轮新版.json')); integ = json.load(open(N / '识别结果/整合版.json'))
    return [dict(k=str(p), q=p, integ=integ[str(p)], six=six[str(p)], sa_o=sa_o, sa_p2=sa_p2, doc=DOC.get(str(p))) for p in range(1, 9)]
# ---------- 65 页（只换扫描件页，文字层页照 整合版_v2（分流口径） 的输出不动） ----------
ROUTE65 = json.load(open(C65.O / '分流整合版/候选路线_v2/成绩/候选路线_路线.json'))['盲测']
SCAN65 = [p for p in ROUTE65 if ROUTE65[p] != '文字层']
BASE65 = json.load(open(H / '预测/65页_整合版_v2（分流口径）.json'))
def _set65():
    integ, six = C65.load(C65.P_INTEG), C65.load(C65.P_SIX); P = []; docs = {}
    for p in SCAN65:
        sg, q = C65.seginfo(p)
        f = sg['doc'].parent / 'pred_pages.json'
        if str(f) not in docs: docs[str(f)] = json.load(open(f))
        P.append(dict(k=p, q=q, integ=integ[p], six=six[p], sa_o=sg['sa_o'], sa_p2=sg['sa_p2'], doc=docs[str(f)].get(str(q))))
    return P
SETS = {'盲测25': _set25(), '新集8': _set8(), '65扫描': _set65()}
_U = {}
def units(sa, q):
    k = (str(sa), q)
    if k not in _U: _U[k] = PO.load_units(sa, q)
    return _U[k]
_B = {}
def boxes(sa, q):
    k = (str(sa), q)
    if k not in _B: _B[k] = G.boxes(sa, q)
    return _B[k]
_ROT = {}
def rot(d, which):
    """旋转页规则（冻结版）：整合版侧只重排（去章图行框），第六轮侧 auto（原图行框，必要时重建）。"""
    k = (str(d['sa_o']), d['q'], which)
    if k not in _ROT:
        if which == 'integ': _ROT[k] = RR.apply(d['integ'], d['sa_p2'], d['q'], 'reorder')
        else: _ROT[k] = RR.apply(d['six'], d['sa_o'], d['q'], 'auto')
    return _ROT[k]
def side_ratio(u, ov=0.5, minlen=2):
    u = [x for x in (u or []) if not x['vert'] and len(x['t']) >= minlen]
    if not u: return 0.0
    n = 0
    for i, a in enumerate(u):
        for j, b in enumerate(u):
            if i != j and F._ov(a, b) >= ov and (a['x'] + a['w'] < b['x'] or b['x'] + b['w'] < a['x']):
                n += 1; break
    return round(n / len(u), 3)
_SR = {}
def sr(d, ov=0.5, minlen=2):
    k = (str(d['sa_p2']), d['q'], ov, minlen)
    if k not in _SR: _SR[k] = side_ratio(units(d['sa_p2'], d['q']), ov, minlen)
    return _SR[k]
DEF = dict(handover=0.85, reord=0.5, ov=0.5, minlen=2, conf=0.90, docthr=0.5, guard=0.30, rot=True, seal=True)
import 新规则 as NR
DEF.update(r3=False, r4=False, r5=False, inv=False, L=150, cover=0.7)
def _new(pg, d, sa, P, LOG):
    u = units(sa, d['q'])
    if P['r3']:
        pg, lg = NR.split_long_cell(pg, u, boxes(d['sa_o'], d['q']), PO.norm, L=P['L'], cover=P['cover'])
        if lg and LOG is not None: LOG.append({'页': d['k'], '规则': 3, '明细': lg})
    if P['r4']:
        pg, lg = NR.dedup_overlap(pg, u, PO.norm)
        if lg and LOG is not None: LOG.append({'页': d['k'], '规则': 4, '删': len(lg)})
    if P['r5']:
        pg, lg = NR.drop_stray(pg, u, PO.norm)
        if lg and LOG is not None: LOG.append({'页': d['k'], '规则': 5, '删': len(lg)})
    return pg
_INV = {}
def is_inv(d):
    k = (str(d['sa_o']), d['q'])
    if k not in _INV: _INV[k] = A.L4.版式(d['integ']) == '发票'
    return _INV[k]
def v3(d, LOG=None, **kw):
    """参数化整合版_v3（默认参数＝冻结的 组合.v3 ＋ 旋转页规则）。返回（页, 路线）。
    新增：r3/r4/r5＝规则3/4/5；inv＝True 时交接条件改为「本页版式判为发票，或并排行比例≥handover」。"""
    P = {**DEF, **kw}
    ig = rot(d, 'integ')[0] if P['rot'] else d['integ']
    sx = rot(d, 'six')[0] if P['rot'] else d['six']
    s = sr(d, P['ov'], P['minlen'])
    if (P['handover'] is not None and s >= P['handover']) or (P['inv'] and is_inv(d)):
        out = _new(deepcopy(sx), d, d['sa_o'], P, LOG); out, _ = A.watermark(out); out, _ = A.invoice_cells(out, units(d['sa_o'], d['q'])); out, _, _ = PP.footer_guarded(A, out, P['guard'])
        return out, '第六轮'
    pg = ig
    if P['seal']: pg, _ = R.seal_filter(pg, units(d['sa_p2'], d['q']), boxes(d['sa_o'], d['q']), PO.norm, conf=P['conf'])
    if P['reord'] is not None and s >= P['reord']: pg, _ = R.reorder_by_doc(pg, d['doc'], PO.norm, thr=P['docthr'])
    out = _new(deepcopy(pg), d, d['sa_p2'], P, LOG); out, _ = A.watermark(out); out, _ = A.invoice_cells(out, units(d['sa_p2'], d['q'])); out, _, _ = PP.footer_guarded(A, out, P['guard'])
    return out, '整合版'
def cand_v2(d, rotate=True):
    """候选路线_v2（＋旋转规则）：第六轮输出＋改动4→3→2（25/8 页用原后处理，与以往成绩口径一致）。"""
    sx = rot(d, 'six')[0] if rotate else d['six']
    return C25.post(sx, d['sa_o'], None, d['q'])
# ---------- 计分（逐页缓存） ----------
_SC = {}
def _h(pg): return hashlib.md5(json.dumps(pg, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
def score(set_name, pm):
    """pm：{页键: 页}。返回三口径汇总 {'主':{错字,读出率,错字率},'严':…,'不计顺序':…,'参照字数'} 与逐页主错字。"""
    key = (set_name, tuple(sorted((k, _h(v)) for k, v in pm.items())))
    if key in _SC: return _SC[key]
    if set_name in ('盲测25', '新集8'):
        C = C25 if set_name == '盲测25' else C8
        sc = C.score(pm)
        r = {c: {kk: sc[c][kk] for kk in ('错字', '读出率', '错字率') if kk in sc[c]} for c in ('主', '严', '不计顺序')}
        r['参照字数'] = sc['主'].get('参照字数'); r['逐页主'] = {str(q): v['错字'] for q, v in sc['主']['逐页'].items()}
        r['逐页严'] = {str(q): v['错字'] for q, v in sc['严']['逐页'].items()} if '逐页' in sc['严'] else {}
        r['逐页序'] = {str(q): v['错字'] for q, v in sc['不计顺序']['逐页'].items()} if '逐页' in sc['不计顺序'] else {}
    else:
        full = dict(BASE65); full.update(pm)
        det = G.detail('盲测', full); allp = {**det['众创隐藏层'], **det['65页可见层合计']}
        a = G.agg(allp, pages=SCAN65)
        r = {c: a[c] for c in ('主', '严', '不计顺序')}; r['参照字数'] = a['参照字数']
        r['逐页主'] = {p: G.E(allp[p]['主']) for p in SCAN65}; r['逐页序'] = {p: allp[p]['序'] for p in SCAN65}
        r['逐页严'] = {p: G.E(allp[p]['严']) for p in SCAN65}
    _SC[key] = r; return r
def run(set_name, fn):
    pm, route = {}, {}
    for d in SETS[set_name]:
        out = fn(d)
        if isinstance(out, tuple): pm[d['k']], route[d['k']] = out
        else: pm[d['k']], route[d['k']] = out, '第六轮'
    return pm, route
def cost(route, base=104.0):
    n6 = sum(v == '第六轮' for v in route.values()); return round(base + n6 / max(1, len(route)) * EXTRA, 1), n6

# ---------- 批量计分：逐页缓存（每页按输出内容去重，按“轮”拼整页图一起计分，再逐页汇总） ----------
_PP = {}   # (集, 页键, 哈希) -> {'n','主m','主e','严m','严e','序'}
def _rounds(set_name, pms):
    keys = [d['k'] for d in SETS[set_name]]
    todo = {k: [] for k in keys}
    for pm in pms:
        for k in keys:
            h = _h(pm[k])
            if (set_name, k, h) not in _PP and h not in [x[0] for x in todo[k]]: todo[k].append((h, pm[k]))
    nr = max([len(v) for v in todo.values()] + [0])
    for i in range(nr):
        pm = {k: (todo[k][i][1] if i < len(todo[k]) else pms[0][k]) for k in keys}
        if set_name in ('盲测25', '新集8'):
            C = C25 if set_name == '盲测25' else C8
            sc = C.score(pm)
            for k in keys:
                if i >= len(todo[k]): continue
                q = int(k)
                if q not in C.SCORED: _PP[(set_name, k, todo[k][i][0])] = None; continue
                a, b, u = sc['主']['逐页'][q], sc['严']['逐页'][q], sc['不计顺序']['逐页'][q]
                n = a['参照字数']
                _PP[(set_name, k, todo[k][i][0])] = {'n': n, '主m': round((a['读出率'] or 0) * n), '主e': a['错字'], '严m': round((b['读出率'] or 0) * n), '严e': b['错字'], '序': u['错字']}
        else:
            full = dict(BASE65); full.update(pm)
            det = G.detail('盲测', full); allp = {**det['众创隐藏层'], **det['65页可见层合计']}
            for k in keys:
                if i >= len(todo[k]): continue
                x = allp[k]
                _PP[(set_name, k, todo[k][i][0])] = {'n': x['n'], '主m': x['主']['matches'], '主e': G.E(x['主']), '严m': x['严']['matches'], '严e': G.E(x['严']), '序': x['序']}
def batch(set_name, pms):
    """pms：多个 {页键: 页}。返回与 pms 同序的三口径汇总（含逐页主错字）。"""
    _rounds(set_name, pms); out = []
    for pm in pms:
        recs = {k: _PP[(set_name, k, _h(v))] for k, v in pm.items()}
        recs = {k: v for k, v in recs.items() if v}
        n = sum(v['n'] for v in recs.values()); r = {'参照字数': n}
        for c in ('主', '严'):
            m = sum(v[c + 'm'] for v in recs.values()); e = sum(v[c + 'e'] for v in recs.values())
            r[c] = {'错字': e, '读出率': round(100 * m / max(1, n), 2), '错字率': round(100 * e / max(1, n), 2)}
        u = sum(v['序'] for v in recs.values()); r['不计顺序'] = {'错字': u, '错字率': round(100 * u / max(1, n), 2)}
        r['逐页主'] = {k: v['主e'] for k, v in recs.items()}
        out.append(r)
    return out
