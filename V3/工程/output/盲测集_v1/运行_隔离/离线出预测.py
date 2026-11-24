"""由本任务识别缓存离线组装第六轮新版、整合版预测。必须用 离线跑.sh 运行（无网络、清掉百度环境变量）。
不改生产配置、冻结目录、主路径、参照。只写 运行_隔离/ 与 盲测集_v1/识别结果/。"""
import copy, json, sys
from pathlib import Path

sys.dont_write_bytecode = True
# 【仓库副本：只改路径与加载】数据目录：OCR_RUN_ISO（本份材料的运行隔离目录）；输出目录：OCR_RESULT_DIR（默认 运行隔离目录的上一级/识别结果）。
# 代码目录：仓库 V3/工程/output/…（原试验机 baidu/output/… 的逐字节副本）与仓库 scripts/、src/。
for _k in range(1, 9):
    _s = Path(__file__).resolve().parents[_k] / 'scripts'
    if (_s / 'ocr_lab_paths.py').exists():
        sys.path.insert(0, str(_s)); break
import ocr_lab_paths as LP
try:
    ISO = LP.run_iso(required=True)
except LP.MissingInputError as _e:
    print(_e, file=sys.stderr); sys.exit(1)
ROOT = LP.repo_root()
ENG = LP.v3_root() / '工程'
OUTR = LP.result_dir_path()
W = ENG / 'output' / 'swisstransfer盲测'
H5 = W / '组合五副本'
src = H5 / '复跑_第五轮.py'
NS = {'__file__': str(src), '__name__': 'combo_header'}
exec(compile(src.read_text(encoding='utf-8').split('# ---------- 主流程 ----------')[0], str(src), 'exec'), NS)
# 底座子进程的工作目录：原为试验机 baidu 根（其中没有 pipeline 包）。仓库根有 pipeline 包，会让 tablerow 改走仓库分支、
# 与原机加载的模块不同；所以设为本份材料的运行隔离目录（只有数据、不含代码），与原机一样走 tablerow 的试验机分支。
NS['ROOT'] = ISO
CFG = copy.deepcopy(NS['CFG'])
layers = NS['layers']

sys.path.insert(0, str(ENG / 'output' / 'synth18e专项优化' / '工具'))
from 公共 import F as ZF
import 规则_专项 as Z
sys.path.insert(0, str(ENG / 'output' / '第六轮_组合五乙1优化' / '工具'))
import 第六轮规则 as R6

RUN = ISO / '运行'
WO, WP = RUN / 'orig' / 'work', RUN / 'P2' / 'work'
C = RUN / '预测' / 'blind30_C.json'
pdf = ISO / '输入' / 'blind30-scan.pdf'
sg = dict(
    tag='blind30', C=C,
    sa_o=WO / 'output' / 'scheme_a',
    sa_p2=WP / 'output' / 'scheme_a',
    pdf=pdf, name='blind30',
    lines=RUN / '线缓存',
    doc=WO / 'output' / 'final' / 'scheme_b_standard' / 'blind20-scan.json',
    rd=WO / 'output' / 'scheme_a' / 'renders',
    rr=RUN / 'reread' / 'A_P2' / 'results.json',
    vert=RUN / 'blind30' / 'vertical' / 'results.json',
    off=0,
)
# 缺识别缓存或上游预测时：中文报错、退出码 1，不写识别结果
try:
    for _k2 in ('C', 'pdf', 'doc', 'rr', 'vert'):
        LP.require_file(sg[_k2], f'上游输入 {_k2}（{Path(sg[_k2]).name}）', LP.ENV_RUN_ISO)
    for _k2 in ('sa_o', 'sa_p2'):
        LP.require_file(Path(sg[_k2]) / 'call_summary.json', f'高精度缓存 {_k2}/call_summary.json', LP.ENV_RUN_ISO)
    LP.require_dir(sg['rd'], '原图高精度渲染图 renders/', LP.ENV_RUN_ISO)
except LP.MissingInputError as _e:
    print(_e, file=sys.stderr); sys.exit(1)

def boxes(d, q):
    pages = json.load(open(Path(d) / 'call_summary.json'))['pages']
    return {pg['page']: pg['route'].get('seal_boxes') or [] for pg in pages}.get(q, [])

def yi1(pg, sa, q):
    u = ZF.PO.load_units(sa, q)
    if u is not None and pg.get('sections'):
        new, lg = Z.r_印章(pg, u, boxes(sa, q), {'乙1': True})
        return new
    return pg

def strip(pg):
    return {'sections': pg.get('sections') or [], 'seal': pg.get('seal') or ''}

def dump(path, pred):
    out = {str(int(p)): strip(v) for p, v in pred.items()}
    out = {k: out[k] for k in sorted(out, key=int)}
    path.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(path, 'w'), ensure_ascii=False, indent=1)
    print(path, '页数', len(out), flush=True)
    return out

# ---- 第六轮新版：组合五底座 + 规则甲乙 + 乙1（印章框用原图高精度）----
mid = RUN / '预测' / '组合五_中间'
mid.mkdir(parents=True, exist_ok=True)
base = NS['base'](sg, mid)
page_cfg = R6.make(CFG, ['甲', '乙'])
# 全部 30 页都是扫描件，甲乙关闭第三版B/D
fe = {q: {'扫描件': True} for q in base}
groups = {}
for q in base:
    c = page_cfg('盲测集_v1', int(q), fe[q])
    k = json.dumps(c, sort_keys=True)
    groups.setdefault(k, (c, []))[1].append(q)
out6 = {}
logs6 = {}
for k, (c, qs) in groups.items():
    sub, lg = layers({q: copy.deepcopy(base[q]) for q in qs}, sg, c)
    logs6[k] = lg
    for q in qs:
        out6[q] = yi1({kk: vv for kk, vv in sub[q].items() if kk != 'meta'}, Path(sg['sa_o']), int(q))
sixth = dump(OUTR / '第六轮新版.json', out6)
json.dump({'规则甲乙': True, '乙1坐标': '原图高精度'}, open(RUN / '预测' / '第六轮新版_说明.json', 'w'), ensure_ascii=False, indent=1)

# ---- 整合版：去章高精度底座 + 全层去甲2 + 规则甲乙 + 乙1（印章框用去章高精度）----
sys.path.insert(0, str(ROOT / 'scripts'))
import blind20_build as BB
hp_dir = RUN / '重建_去章高精度'
BB.build_scheme_a(sg['sa_p2'], hp_dir, 'routed')
hp2 = {str(k): {'sections': v['sections'], 'seal': v.get('seal') or ''} for k, v in json.load(open(hp_dir / 'pred_pages.json')).items()}
NO2 = copy.deepcopy(CFG)
NO2['第四轮甲2_发票明细行列重建'] = False

def r6(_s, _q, cfg):
    return {**cfg, '第三版B_认可标志页脚': False, '第三版D_超大字低置信度': False}

sg_p2 = {**sg, 'sa_o': sg['sa_p2']}
start = {str(int(q)): copy.deepcopy(v) for q, v in hp2.items()}
sub, lg = layers({q: copy.deepcopy(start[q]) for q in start}, sg_p2, r6(sg_p2, '1', NO2))
out_i = {q: yi1(strip(v), Path(sg['sa_p2']), int(q)) for q, v in sub.items()}
integ = dump(OUTR / '整合版.json', out_i)
print('done', '第六轮', len(sixth), '整合版', len(integ), flush=True)
