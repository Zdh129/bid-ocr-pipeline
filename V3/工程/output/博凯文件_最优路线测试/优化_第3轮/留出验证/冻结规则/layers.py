"""各层的统一入口：博凯（两段各自的缓存）与 synth（sets_all 配置）。"""
import json
from pathlib import Path
import layer1, layer3
BK = Path('/workspace/pdf-test/baidu/output/博凯文件_最优路线测试')
SEG = {'博凯包': BK / '博凯包/运行', '社保重渲': BK / '博凯包_社保重渲/运行'}
def segs(p): return ('社保重渲', int(p) - 7) if int(p) >= 8 else ('博凯包', int(p))
def doc(R): return R / 'orig/work/output/final/scheme_b_standard/blind20-scan.json'
def sa(R): return R / 'orig/work/output/scheme_a'
STEPS = {}
def L1(pred, d, s, **k): return layer1.apply(pred, d, s)
def L3(pred, d, s, **k):
    r = layer3.run(pred); return pred, [{'页': p, '第三层校验': v} for p, v in r.items()]
def L2(pred, d, s, **k): return pred, []   # 第二层：真认错只剩 9 个标点/符号，且多为参照文字层残留，没有可通用、不过拟合的改字规则 → 不改字
STEPS['第一层'] = [L1]; STEPS['第二层'] = [L2]; STEPS['第三层'] = [L3]; STEPS['整合'] = [L1, L2, L3]
def run_bk(name, pred):
    out, log = {}, []
    for tag, R in SEG.items():
        sub = {}
        for p, v in pred.items():
            t, q = segs(p)
            if t == tag: sub[str(q)] = v
        for f in STEPS[name]:
            sub, lg = f(sub, doc(R), sa(R)); log += [{**x, '段': tag} for x in lg]
        for q, v in sub.items(): out[str(int(q) + (7 if tag == '社保重渲' else 0))] = v
    return out, log
def run_synth(name, pred, c):
    log = []
    for f in STEPS[name]:
        pred, lg = f(pred, c['doc'], c['sa_o']); log += lg
    return pred, log
