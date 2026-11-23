"""整合版优化（T-21）：65 页旧参照计分公共函数（借用 整合版_裸高精度底座/工具/整合框架.py 的计分，只读）。必须用 离线跑.sh。"""
import json, sys
from copy import deepcopy
from pathlib import Path
sys.dont_write_bytecode = True
H = Path(__file__).resolve().parents[1]
O = Path('/workspace/pdf-test/baidu/output')
I = O / '整合版_裸高精度底座'
sys.path.insert(0, str(O / '分流整合版/候选路线_v2/工具'))
import 后处理 as A
sys.path.insert(0, str(I / '工具'))
import 整合框架 as G

def load(p):
    d = json.load(open(p))
    if isinstance(d, dict) and 'pred' in d and isinstance(d['pred'], dict): d = d['pred']
    return {str(k): {'sections': v.get('sections') or [], 'seal': v.get('seal') or ''} for k, v in d.items() if isinstance(v, dict)}

def seginfo(p):
    s = '盲测'
    return (G.SEGS[s][1], int(G.REP[p])) if p in G.REP else (G.SEGS[s][0], int(p))

def units(p, which):
    sg, q = seginfo(p)
    return A.PO.load_units(sg[which], q), sg, q

def post(pg, p, which, logs=None):
    out = deepcopy(pg)
    out, lg = A.watermark(out)
    if lg and logs is not None: logs.append({'页': p, '改动': 4})
    u, _, _ = units(p, which)
    out, lg = A.invoice_cells(out, u)
    if lg and logs is not None: logs.append({'页': p, '改动': 3})
    out, lg = A.footer(out)
    if lg and logs is not None: logs.append({'页': p, '改动': 2})
    return out

def score(pred):
    det = G.detail('盲测', pred)
    allp = {**det['众创隐藏层'], **det['65页可见层合计']}
    assert len(allp) == 65, len(allp)
    return {'65页合计': G.agg(allp), '众创隐藏层': G.agg(det['众创隐藏层']), '可见层55页': G.agg(det['65页可见层合计'])}, allp

P_INTEG = I / '预测/盲测_去章高精度＋全层去甲2＋甲乙.json'
P_SIX = I / '预测/盲测_第六轮新版_复算.json'
P_V2 = O / '分流整合版/候选路线_v2/预测/盲测_候选路线_v2.json'
P_OPT = O / '分流整合版/最优整合路线/预测/盲测_最优整合路线.json'
