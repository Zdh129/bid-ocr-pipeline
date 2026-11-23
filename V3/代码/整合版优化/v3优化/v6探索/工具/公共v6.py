"""v6 探索：在冻结的 v5（sha 4b53d049…4122）流程上插入方向1/3/5 规则。0 次调用。
载入四套集（盲测25、新集8、65扫描、新9）。新9 与新集8 计分共用 公共25 的 C8 槽，所以评分按集分进程跑。"""
import sys, json, hashlib
from copy import deepcopy
from pathlib import Path
sys.dont_write_bytecode = True
X = Path(__file__).resolve().parents[1]; T = X.parent / '工具'
sys.path[:0] = [str(Path(__file__).parent), str(T)]
F5 = X.parent / '整合版_v5冻结/v5参数.json'
assert hashlib.sha256(F5.read_bytes()).hexdigest() == '4b53d049c9b6a678fb46400faf6f40862d5b3d6a082e0749c705b0c8942a4122'
C5 = json.load(open(F5))
for f, h in C5['代码sha256'].items(): assert hashlib.sha256((X.parents[1] / f).resolve().read_bytes()).hexdigest() == h, f
src = (T / '新扫描9页.py').read_text(encoding='utf-8').replace("{'预测': predict, '计分': score_all}[sys.argv[1]]()", '')
N9 = type(sys)('N9'); N9.__file__ = str(T / '新扫描9页.py'); exec(compile(src, N9.__file__, 'exec'), N9.__dict__)
V = N9.V
import v5规则 as F
assert F.V is V
import 规则v6 as G
P5 = C5['参数']
A, PP, R, PO = V.A, V.PP, V.R, V.PO
日志 = []
def v6(d, rules=(), **kw):
    """rules：'1'、'3a'/'3b'、'5a'/'5b' 的组合。其余与 v5 一字不差。"""
    P = {**V.DEF, **P5, **kw}
    ig = F.rot_integ(d)[0]; sx = V.rot(d, 'six')[0]
    s = V.sr(d, P['ov'], P['minlen'])
    six_route = P['handover'] is not None and s >= P['handover']
    sa = d['sa_o'] if six_route else d['sa_p2']
    pg = deepcopy(sx) if six_route else ig
    if '1' in rules:
        pg2, lg = G.mixed_rebuild(pg, d['sa_o'], d['q'])
        if lg: 日志.append({'页': d['k'], **lg}); pg = pg2
    if not six_route:
        if P['seal']: pg, _ = R.seal_filter(pg, V.units(d['sa_p2'], d['q']), V.boxes(d['sa_o'], d['q']), PO.norm, conf=P['conf'])
        if P['reord'] is not None and s >= P['reord'] and not (('1' in rules) and 日志 and 日志[-1].get('页') == d['k'] and 日志[-1].get('规则') == 1):
            pg, _ = R.reorder_by_doc(pg, d['doc'], PO.norm, thr=P['docthr'])
    for r in rules:
        if r in ('3a', '3b', '3c', '3d', '3e'):
            pg2, lg = G.giant_cell(pg, sa, d['q'], mode=r[1])
            if lg: 日志.append({'页': d['k'], **lg}); pg = pg2
        if r in ('5a', '5b'):
            pg2, lg = G.split_cols(pg, sa, d['q'], mode=r[1])
            if lg: 日志.append({'页': d['k'], **lg}); pg = pg2
    out = deepcopy(pg); out, _ = A.watermark(out); out, _ = A.invoice_cells(out, V.units(sa, d['q'])); out, _, _ = PP.footer_guarded(A, out, P['guard'])
    return out, ('第六轮' if six_route else '整合版')
