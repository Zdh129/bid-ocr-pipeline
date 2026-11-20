"""试算公共部分（事后/试算，纯本地，只读已有缓存；不调用任何外部服务）。工作目录 /workspace/pdf-test/baidu。
只 import 冻结规则/ 里的副本，不改它们；表格线缓存放在 试算/表格线缓存/（从已有缓存复制，缺页时本地 pdftoppm+OpenCV 生成）。"""
import json, sys, copy
from pathlib import Path
ROOT = Path('/workspace/pdf-test/baidu')
B = ROOT / 'output/盲测_版面约束'; FZ = B / '冻结规则'; RUN = B / '运行'; TS = B / '版面分析优化计划/试算'
sys.path.insert(0, str(FZ))
import variants_blind as VB                     # 设置好 sys.path 与 common（冻结副本）
import layout_posthoc as LP, layout as L, lines as LN, gate as G
from baidu_ocr.hybrid.inputs import a_chars_for_page
import synth18b_score as SB, tolerant_score as T
import common_lc as CL                           # 冻结副本：scorers / score
LN.D = TS / '表格线缓存'   # 新集由 frozen_stack.NewSet 改成各自的缓存目录
K5 = ('ref_chars', 'matches', 'deletions', 'substitutions', 'insertions')
E = lambda o: o['deletions'] + o['substitutions'] + o['insertions']

class Set:
    """一套数据：C、乙′、A 字框、表格线、计分器。"""
    def __init__(self, name):
        self.name = name
        if name == 'synth_blind_g':
            V = json.load(open(RUN / '计分_无竖排/pred_三行.json'))
            self.C, self.Yp = V['C'], V['乙′']
            self.sa = RUN / 'orig/work/output/scheme_a'; self.pdf = str(B / 'synth_blind_g/synth_blind_g-scan.pdf')
            bc, bs, hs, refs, rj, mp, rng = SB.load_bs(B / 'synth_blind_g')
            self.scorers = [(name, bs, refs, rj, mp)]
        else:
            VA = json.load(open(ROOT / 'output/版面约束/variants.json')); VP = json.load(open(ROOT / 'output/版面约束/variants_posthoc.json'))
            self.C, self.Yp = VA[name]['C'], VP[name]['V']
            ent = next(x for x in CL.SETS if x[0] == name)
            self.sa = ROOT / ent[3]; self.pdf = ent[4]
            self.scorers = CL.scorers(name)
        self.asum = {pg['page']: pg for pg in json.load(open(self.sa / 'call_summary.json'))['pages']}
        _, bs, refs, rj, mp = self.scorers[0]
        self.inv = sorted(int(p) for p in self.C if mp[int(p)]['doc_type'] == '发票')
        self.cls = {p: VB.classify(''.join(x['text'] for x in v['sections'])) for p, v in self.C.items()}
        self._a = {}
    def achars(self, p):
        p = int(p)
        if p not in self._a: self._a[p] = a_chars_for_page(self.sa, self.asum[p]) if p in self.asum else []
        return self._a[p]
    def lines(self, p): return LN.detect(self.pdf, self.name, int(p))
    def is_inv_page(self, p): return VB.is_inv(''.join(x['text'] for x in self.C[p]['sections']), self.cls[p])

def metrics(S, pred):
    """返回每个计分器：发票页/全套/非发票页 主口径与严格口径，以及逐页错字。"""
    out = {}
    for tag, bs, refs, rj, mp in S.scorers:
        tol = T.score_tolerant(bs, pred, rj, mp, 'in'); st = bs.score_system(pred, refs['full'], mp, None)
        pages = list(tol['per_page']); non = [p for p in pages if p not in S.inv]
        agg = lambda sc, ps: {k: sum(sc['per_page'][p]['overall'][k] for p in ps) for k in K5}
        out[tag] = {'主_发票': agg(tol, S.inv), '主_全套': agg(tol, pages), '主_非发票': agg(tol, non),
                    '严_发票': agg(st, S.inv), '严_全套': agg(st, list(st['per_page'])),
                    '主_逐页': {p: E(tol['per_page'][p]['overall']) for p in pages}, '严_逐页': {p: E(st['per_page'][p]['overall']) for p in st['per_page']}}
    return out
def rate(a):
    n = a['ref_chars']; return {'错字': E(a), '读出率': round(100 * a['matches'] / n, 2), '错字率': round(100 * E(a) / n, 2)}
SIX = ['synth18', 'synth18b', 'synth18c', 'synth18d', 'synth18e', 'blind20']
