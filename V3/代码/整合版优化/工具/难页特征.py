"""方向2：难页特征（只用整合版自身已付费数据 + 本地渲染，不看参照、不看第六轮输出、不按文件名/页码）。
  并排行比例：去章高精度行框中，与另一行在纵向重叠≥50%且横向不相交的行所占比例（双栏签字区、表单、发票）。
  低置信字比例：置信度<0.90 的行的字数占比。
  红章像素：本地渲染图的红色像素数（路线分类阶段本地算出，scheme_a call_summary 的 seal_red_px，不调接口）。
  发票：与第四轮 版式() 同一判定。"""
import json
from pathlib import Path
def _ov(a, b):
    lo, hi = max(a['y'], b['y']), min(a['y'] + a['h'], b['y'] + b['h'])
    return max(0, hi - lo) / max(1, min(a['h'], b['h']))
def side_ratio(u):
    u = [x for x in (u or []) if not x['vert'] and len(x['t']) >= 2]
    if not u: return 0.0
    n = 0
    for i, a in enumerate(u):
        for j, b in enumerate(u):
            if i != j and _ov(a, b) >= 0.5 and (a['x'] + a['w'] < b['x'] or b['x'] + b['w'] < a['x']):
                n += 1; break
    return round(n / len(u), 3)
def lowconf(u):
    u = u or []; tot = sum(len(x['t']) for x in u)
    return round(sum(len(x['t']) for x in u if (x['prob'] or 1) < 0.90) / max(1, tot), 3)
_RP = {}
def red_px(sa_o, q):
    k = str(sa_o)
    if k not in _RP:
        _RP[k] = {pg['page']: pg['route'].get('seal_red_px') or 0 for pg in json.load(open(Path(sa_o) / 'call_summary.json'))['pages']}
    return _RP[k].get(q, 0)
def feats(pg, u, sa_o, q, L4):
    return {'并排行比例': side_ratio(u), '低置信字比例': lowconf(u), '红章像素': red_px(sa_o, q), '发票': L4.版式(pg) == '发票'}
