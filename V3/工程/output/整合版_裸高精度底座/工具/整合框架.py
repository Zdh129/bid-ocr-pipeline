"""整合版（裸高精度底座）离线框架。0 次百度调用：必须用 ../../swisstransfer盲测/补跑/离线跑.sh（无网络命名空间、清掉百度环境变量）运行。
只读：组合五副本的各层函数与底座缓存、各集 scheme_a 原始返回、参照。只写本隔离目录。"""
import copy, json, sys
from collections import Counter
from pathlib import Path
sys.dont_write_bytecode = True
H = Path(__file__).resolve().parents[1]; O = H.parent; ROOT = O.parent
W = O / 'swisstransfer盲测'; H5 = W / '组合五副本'; src = H5 / '复跑_第五轮.py'
NS = {'__file__': str(src), '__name__': 'combo_header'}
exec(compile(src.read_text(encoding='utf-8').split('# ---------- 主流程 ----------')[0], str(src), 'exec'), NS)   # 含安全检查：有网络即退出
CFG5 = copy.deepcopy(NS['CFG']); layers = NS['layers']; score5 = NS['score']; REFS = dict(NS['REFS']); DATA = NS['DATA']
sys.path.insert(0, str(O / 'synth18e专项优化/工具'))
from 公共 import F as ZF
import 规则_专项 as Z
bs_norm = None
# ---------- 乙1 ----------
_BX = {}
def boxes(d, q):
    if str(d) not in _BX: _BX[str(d)] = {pg['page']: pg['route'].get('seal_boxes') or [] for pg in json.load(open(Path(d) / 'call_summary.json'))['pages']}
    return _BX[str(d)].get(q, [])
def yi1(pg, sa_o, q):
    u = ZF.PO.load_units(sa_o, q)
    if u is not None and pg.get('sections'):
        new, lg = Z.r_印章(pg, u, boxes(sa_o, q), {'乙1': True}); return new
    return pg
def units(sa_o, q): return ZF.PO.load_units(sa_o, int(q))
# ---------- 数据集与输入段 ----------
def _sg(tag, R, C, pdf, lines, vert, off=0, keep=None, name='bk', sa_o=None, sa_p2=None, doc=None, rd=None, rr=None):
    WO, WP = R / 'orig/work', R / 'P2/work'
    return dict(tag=tag, C=Path(C) if C else None, sa_o=Path(sa_o or WO / 'output/scheme_a'), sa_p2=Path(sa_p2 or WP / 'output/scheme_a'), pdf=Path(pdf), name=name,
                lines=Path(lines), doc=Path(doc or WO / 'output/final/scheme_b_standard/blind20-scan.json'), rd=Path(rd or WO / 'output/scheme_a/renders'),
                rr=Path(rr or R / 'reread/A_P2/results.json'), vert=Path(vert), off=off, keep=keep or (lambda p: True))
H6 = O / '第六轮_组合五乙1优化/输入快照'   # 只读
SEGS = {k: [dict(v) for v in vs] for k, vs in DATA.items()}
SEGS['synth18e'] = [_sg('synth18e', O / 'synth18e', H6 / 'synth18e_C.json', '/workspace/pdf-test/synth18e/synth18e-scan.pdf', H6 / 'lines_synth18e',
                        O / 'synth18e/synth18e/vertical/results.json', name='synth18e')]
SEGS['synth18b'] = [_sg('synth18b', O / 'synth18b', None, '/workspace/pdf-test/synth18b/synth18b-scan.pdf', H / '线缓存/synth18b', '/nonexistent', name='synth18b')]
for tag, R in (('盲测_补跑', W / '补跑/运行'), ('盲测_重跑', W / '补跑_字体重跑/运行')):
    s = json.load(open(R / '段.json'))[0]
    SEGS.setdefault('盲测', []).append(_sg(tag, R, s['C'], s['pdf'], s['lines'], R / 'bk/vertical/results.json'))
REFS['synth18e'] = Path('/workspace/pdf-test/synth18e'); REFS['synth18b'] = Path('/workspace/pdf-test/synth18b')
BASE5 = {'synth18e': H6 / 'synth18e_原路线_底座.json', '盲测_补跑': W / '补跑/运行/预测/组合五_中间/swiss_底座.json', '盲测_重跑': W / '补跑_字体重跑/运行/预测/组合五_中间/swiss_底座.json'}
def base5(sg):
    f = BASE5.get(sg['tag'], H5 / '中间' / f"{sg['tag']}_底座.json")
    return json.load(open(f))['pred'] if Path(f).exists() else None
def hp_file(sg, rebuild=False):
    f = sg['sa_o'].parent / 'final/scheme_a/pred_pages.json'
    if f.exists() and not rebuild: return f
    out = H / '重建_裸高精度' / sg['tag']; g = out / 'pred_pages.json'
    if not g.exists():   # 缓存里没有落盘的裸高精度预测：用原 build_scheme_a（routed）从已缓存的 scheme_a 原始返回离线重建，只写本目录
        sys.path.insert(0, str(ROOT / 'scripts')); import blind20_build as BB
        BB.build_scheme_a(sg['sa_o'], out, 'routed')
    return g
def hp(sg):
    """裸高精度：scheme_a 原图走法（主识别＋覆盖补识别，routed 构建）直出。"""
    return {str(k): {'sections': v['sections'], 'seal': v.get('seal') or ''} for k, v in json.load(open(hp_file(sg))).items()}
def Cpred(sg):
    if not sg['C'] or not Path(sg['C']).exists(): return None
    return json.load(open(sg['C']))
REP = {str(x['原补跑页']): str(x['output_page']) for x in json.load(open(W / '补跑_字体重跑/输入/页对照.json'))['pages']}
def assemble(s, fn):
    """fn(sg) -> {段内页: 页} 或 None；按 off/keep 拼成整集；盲测用字体重跑 9 页替换。"""
    if s == '盲测':
        a = fn(SEGS['盲测'][0]); b = fn(SEGS['盲测'][1])
        if a is None or b is None: return None
        P = {k: v for k, v in a.items()}
        for q, i in REP.items(): P[q] = b[i]
        return {p: P[p] for p in sorted(P, key=int)}
    pred = {}
    for sg in SEGS[s]:
        r = fn(sg)
        if r is None: return None
        for q, v in r.items():
            if sg['keep'](int(q)): pred[str(int(q) + sg['off'])] = v
    return {p: pred[p] for p in sorted(pred, key=int)}
def strip(pg): return {k: v for k, v in pg.items() if k in ('sections', 'seal')}
def run_layers(start, sg, cfg, y1=True, page_cfg=None):
    """在任意起点上跑组合五各层（cfg 为层开关），再叠乙1。page_cfg(sg, q, cfg) 可按页面特征改开关。"""
    groups = {}
    for q in start:
        c = page_cfg(sg, q, cfg) if page_cfg else cfg; k = json.dumps(c, sort_keys=True, ensure_ascii=False); groups.setdefault(k, (c, []))[1].append(q)
    out, logs = {}, {}
    for k, (c, qs) in groups.items():
        sub, lg = layers({q: copy.deepcopy(start[q]) for q in qs}, sg, c); logs[k] = lg
        for q in qs: out[q] = strip(sub[q])
    if y1: out = {q: yi1(v, sg['sa_o'], int(q)) for q, v in out.items()}
    return out, logs
# ---------- 计分 ----------
MZ = W / '补跑_字体重跑/合并65页/参照子集'
BLIND_GROUPS = {'众创隐藏层': '隐藏OCR层_4. 众创', '荣和可见层': '可见文字层_2. 荣和', '65页可见层合计': '可见文字层_合计'}
sys.path.insert(0, str(O / '博凯_清零/工具')); import 计分 as S3
T = NS['T']; KK = ('ref_chars', 'matches', 'deletions', 'substitutions', 'insertions'); E = lambda o: o['deletions'] + o['substitutions'] + o['insertions']
def _detail(rd, pr):
    bs, refs, rj, mp, img = S3.load(rd); pr = copy.deepcopy(pr)
    tol = T.score_tolerant(bs, pr, rj, mp, 'in'); st = bs.score_system(pr, refs['full'], mp, None); out = {}
    for p in sorted(tol['per_page']):
        rs, _ = bs.ref_chars(refs['full'][p]); pp = pr.get(str(p)) or {'sections': []}
        ps = bs.norm(''.join(s['text'] for s in pp['sections'])); a, b = Counter(rs), Counter(ps)
        out[str(p)] = {'n': len(rs), '主': {k: tol['per_page'][p]['overall'][k] for k in KK}, '严': {k: st['per_page'][p]['overall'][k] for k in KK},
                       '序': max(sum((a - b).values()), sum((b - a).values())), '类型': mp[p]['doc_type']}
    return out
def agg(det, pages=None):
    ps = [p for p in det if pages is None or p in pages]; n = sum(det[p]['n'] for p in ps); r = {'参照字数': n, '页数': len(ps)}
    for c in ('主', '严'):
        m = sum(det[p][c]['matches'] for p in ps); e = sum(E(det[p][c]) for p in ps)
        r[c] = {'错字': e, '读出率': round(100 * m / max(n, 1), 3), '错字率': round(100 * e / max(n, 1), 3)}
    u = sum(det[p]['序'] for p in ps); r['不计顺序'] = {'错字': u, '错字率': round(100 * u / max(n, 1), 3)}
    return r
def detail(s, pred):
    """返回 {组: 逐页明细}。"""
    if s == '盲测':
        return {g: _detail(MZ / d, pred) for g, d in BLIND_GROUPS.items()}
    return {s: _detail(REFS[s], pred)}
# ---------- 以裸高精度为输入跑冻结栈底座（route5 1,4,2 → tablerow），线缓存用本目录副本 ----------
LC = H / '线缓存'
def my_lines(sg):
    t = sg['tag']
    if t in ('博凯包', '社保重渲', '江苏', 'synth18e', '盲测_补跑', '盲测_重跑', 'synth18b'): return LC / t
    return LC / 'synth' / t
def base_from(sg, start, label):
    """start：段内逐页预测（作为 route5 的「C」输入）。返回冻结栈底座。结果缓存在 中间/。"""
    MID = H / '中间'; MID.mkdir(exist_ok=True); tag = f"{sg['tag']}__{label}"
    fin = MID / f'{tag}_底座.json'
    if fin.exists(): return json.load(open(fin))['pred']
    cf = MID / f'{tag}_输入.json'; json.dump(start, open(cf, 'w'), ensure_ascii=False, indent=1)
    sg2 = {**sg, 'tag': tag, 'C': cf, 'lines': my_lines(sg)}
    return NS['base'](sg2, MID)
# ---------- 去章高精度：颜色去章图（300DPI 去章后按原尺寸送识）上的强制高精度原始返回，用同一 build_scheme_a 离线构建 ----------
def hp2_file(sg):
    out = H / '重建_去章高精度' / sg['tag']; g = out / 'pred_pages.json'
    if not g.exists():
        sys.path.insert(0, str(ROOT / 'scripts')); import blind20_build as BB
        BB.build_scheme_a(sg['sa_p2'], out, 'routed')
    return g
def hp2(sg):
    return {str(k): {'sections': v['sections'], 'seal': v.get('seal') or ''} for k, v in json.load(open(hp2_file(sg))).items()}
def sg_p2(sg): return {**sg, 'sa_o': sg['sa_p2']}
