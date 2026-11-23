"""T-23 新扫描 9 页：v4 冻结参数与候选路线_v2（＋旋转规则）出预测、导出参照底稿与看图图片、计分。0 次调用，必须用 离线跑.sh 运行。
用法：新扫描9页.py 预测 | 新扫描9页.py 计分"""
import sys, json, hashlib
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))
import 公共v3 as V
NEW = V.V / '新扫描9页_运行隔离'; RUN = NEW / '运行'; REF = NEW / '参照草稿'
P4 = V.V / 'v4冻结/v4参数.json'
assert hashlib.sha256(P4.read_bytes()).hexdigest() == '52729ec5745fa20808d73c97b3a7e8cf63895c2e5ccb792c0cfe0b663f097284'
for f, h in json.load(open(P4))['代码sha256'].items():
    assert hashlib.sha256((V.H / f).resolve().read_bytes()).hexdigest() == h, f
PAR = json.load(open(P4))['参数']
单 = {p['新页号']: p for p in json.load(open(NEW / '清单.json'))['页']}
C9 = V._mod('C9', V.H / '工具/公共25.py')
C9.REF_DIR = REF; C9.页表 = {q: {'版式': 单[q]['版式_旧表']} for q in 单}; C9.SCORED = sorted(单)
sa_o, sa_p2 = RUN / 'orig/work/output/scheme_a', RUN / 'P2/work/output/scheme_a'
DOC = json.load(open(RUN / 'orig/work/output/final/scheme_b_standard/pred_pages.json'))
six = json.load(open(NEW / '识别结果/第六轮新版.json')); integ = json.load(open(NEW / '识别结果/整合版.json'))
V.SETS['新9'] = [dict(k=str(p), q=p, integ=integ[str(p)], six=six[str(p)], sa_o=sa_o, sa_p2=sa_p2, doc=DOC.get(str(p))) for p in 单]
_orig_score = V.score
def txt(pg): return '\n'.join(s['text'] for s in pg['sections'])

def predict():
    pm4, rt4 = V.run('新9', lambda d: V.v3(d, **PAR)); pmc, _ = V.run('新9', V.cand_v2)
    (V.V / '预测').mkdir(exist_ok=True)
    json.dump(pm4, open(V.V / '预测/新扫描9_整合版_v4冻结.json', 'w'), ensure_ascii=False, indent=1)
    json.dump(pmc, open(V.V / '预测/新扫描9_候选路线_v2.json', 'w'), ensure_ascii=False, indent=1)
    json.dump({k: {'路线': rt4[k], '并排行比例': V.sr(d)} for k, d in zip(rt4, V.SETS['新9'])}, open(V.V / '预测/新扫描9_v4路线.json', 'w'), ensure_ascii=False, indent=1)
    (REF / '中间').mkdir(parents=True, exist_ok=True)
    for k in pm4:
        (REF / '中间' / f'第{k}页_v4.txt').write_text(txt(pm4[k]), encoding='utf-8')
        (REF / '中间' / f'第{k}页_候选v2.txt').write_text(txt(pmc[k]), encoding='utf-8')
    print({k: (v['路线'], v['并排行比例']) for k, v in json.load(open(V.V / '预测/新扫描9_v4路线.json')).items()})

def score_all():
    pm4 = json.load(open(V.V / '预测/新扫描9_整合版_v4冻结.json')); pmc = json.load(open(V.V / '预测/新扫描9_候选路线_v2.json'))
    rt = json.load(open(V.V / '预测/新扫描9_v4路线.json'))
    # 逐页：用 C9 计分（借用 V.batch 的“新集8”分支，换成 C9）
    V.C8 = C9; V.SETS['新集8'] = V.SETS['新9']
    s4, sc = V.batch('新集8', [pm4, pmc])
    pp = {k: V._PP[('新集8', k, V._h(pm4[k]))] for k in pm4}; pc = {k: V._PP[('新集8', k, V._h(pmc[k]))] for k in pmc}
    def agg(recs, keys):
        n = sum(recs[k]['n'] for k in keys); r = {'参照字数': n}
        for c in ('主', '严'):
            m = sum(recs[k][c + 'm'] for k in keys); e = sum(recs[k][c + 'e'] for k in keys)
            r[c] = {'错字': e, '读出率': round(100 * m / n, 2), '错字率': round(100 * e / n, 2)}
        u = sum(recs[k]['序'] for k in keys); r['不计顺序'] = {'错字': u, '错字率': round(100 * u / n, 2)}
        return r
    K9 = list(pm4); K7 = [k for k in K9 if not 单[int(k)]['博凯（独立性较弱）']]
    out = {'说明': '参照草稿，未经人工复核（模型看图）。v4 参数是在评估集（盲测集_v1 25 页）上调的。', '9页': {}, '7页（去掉博凯2页）': {}, '逐页': {}}
    for nm, rec in (('整合版_v4（冻结）', pp), ('候选路线_v2（＋旋转）', pc)):
        out['9页'][nm] = agg(rec, K9); out['7页（去掉博凯2页）'][nm] = agg(rec, K7)
    for k in K9:
        out['逐页'][k] = {'候选编号': 单[int(k)]['候选编号'], '层': 单[int(k)]['层'], '博凯': 单[int(k)]['博凯（独立性较弱）'], 'v4路线': rt[k]['路线'], '并排行比例': rt[k]['并排行比例'],
                        '参照字数': pp[k]['n'], 'v4': {x: pp[k][x] for x in ('主e', '严e', '序', '主m')}, '候选v2': {x: pc[k][x] for x in ('主e', '严e', '序', '主m')}}
    json.dump(out, open(V.V / '成绩/新扫描9页.json', 'w'), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))

{'预测': predict, '计分': score_all}[sys.argv[1]]()
