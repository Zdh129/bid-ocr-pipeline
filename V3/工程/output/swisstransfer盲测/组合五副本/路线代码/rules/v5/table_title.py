"""第五轮（路线代码版，与 工具/规则5.py 逻辑相同）候选规则：把组合一的纯结构思路推广到非发票表格（完税社保表、偏离表、合同明细、设备清单）。
只改顺序、拼接和空白，不改字符；每条规则结束时自检字符多重集（含原字符全角半角）不变。只看本页识别文字和本页百度坐标，不看参照，不按文件名或页码写。
在组合一的输出上叠加：apply(pg, units, cfg)。"""
import re, sys
from collections import Counter
from statistics import median
from pathlib import Path as _P
_R = _P(__file__).resolve().parent.parent
for _d in ('v4', 'v3'):
    if str(_R / _d) not in sys.path: sys.path.insert(0, str(_R / _d))
import layout_order as L4
PO = L4.PO
FOUR = ('完税社保表', '偏离表', '合同明细', '设备清单')
# ---------------- 丙0 版式识别扩充：标题里带「清单」且有数量／型号列的页记为设备清单（如「销售货物或者提供应税劳务清单」） ----------------
def 版式5(pg, cfg):
    k = L4.版式(pg)
    if k == '其他' and cfg.get('清单识别扩充'):
        t = L4.body_text(pg); h = t[:80]
        if '清单' in h and ('数量' in t or '型号' in t) and not (sum(x in t for x in L4.INV_FIELDS) >= 2 and '发票' in h and '开票日期' in t): return '设备清单'
    return k
# ---------------- 丙1 顶部大字标题置首 ----------------
def r_标题置首(pg, units, ratio=1.3):
    """页面最上方的一行若是明显的大字标题（字高 ≥ 本页文字块字高中位数的 1.3 倍），而输出里排在它前面的行在页面上都位于它下方，就把标题行移到最前。只整行移动。"""
    rows = L4.flat(pg); lines, P, lid, own = L4.located(pg, units); anc = L4.line_anchor(lines, P, lid, own, units)
    idx = [i for i, (_, t) in enumerate(rows) if PO.norm(t)]
    if len(idx) < 3: return pg, None
    hs = [u['h'] for u in units if not u['vert'] and len(u['t']) >= 2]
    if len(hs) < 5: return pg, None
    hm = median(hs)
    cand = [i for i in idx if anc[i] is not None and not anc[i]['vert']]
    if not cand: return pg, None
    t = min(cand, key=lambda i: anc[i]['y'])
    T = anc[t]; tt = PO.norm(rows[t][1])
    if t == idx[0] or T['h'] < ratio * hm or len(tt) < 4 or not re.search(r'[\u4e00-\u9fff]{4}', tt): return pg, None
    before = [i for i in idx if i < t]
    if any(anc[i] is None or anc[i]['y'] < T['y'] + 0.5 * T['h'] for i in before): return pg, None
    order = [t] + [i for i in range(len(rows)) if i != t]
    new = L4.rebuild(pg, [rows[i] for i in order])
    return L4.raw_check(pg, new, '标题置首'), {'标题行': t, '越过行数': len(before)}
# ---------------- 丙2 表格行列重建＋「已按物理行有序就不动」守卫 ----------------
def ordered_already(pg, units, kind):
    """表头行之后到页尾的各行，锚点纵坐标（按表头带倾斜校正）是否已经自上而下有序。"""
    rows = L4.flat(pg); lines, P, lid, own = L4.located(pg, units); anc = L4.line_anchor(lines, P, lid, own, units)
    k1, k2 = L4.HDR[kind]
    hi = next((i for i, (_, t) in enumerate(rows) if any(k in t for k in k1) and any(k in t for k in k2)), None)
    if hi is None: return False
    ys = [(anc[i]['y'] + anc[i]['h'] / 2, anc[i]['h']) for i in range(hi, len(rows)) if anc[i] is not None and not anc[i]['vert'] and PO.norm(rows[i][1])]
    return all(b[0] >= a[0] - 0.5 * min(a[1], b[1]) for a, b in zip(ys, ys[1:]))
def r_表格守卫(pg, units, kind, cfg):
    if ordered_already(pg, units, kind): return pg, {'守卫': '原文已按物理行有序，不动'}
    return L4.grid(pg, units, kind, {k: cfg.get(k, True) for k in L4.TABLE_OPTS})
# ---------------- 丙3 页边竖排注记移到页尾（甲7 推广到四类非发票表格） ----------------
def r_页边注记(pg, units):
    return L4.r_发票页边注记(pg, units)
# ---------------- 总入口 ----------------
def apply(pg, units, cfg):
    """cfg 开关：清单识别扩充、标题置首（四类）、标题置首_全部非发票、偏离表守卫重建、合同明细守卫重建、设备清单守卫重建、
    偏离表重建_无守卫、合同明细重建_无守卫、设备清单重建_无守卫、页边注记、完税社保倾斜"""
    if units is None or not pg.get('sections'): return pg, {}
    kind = 版式5(pg, cfg); log = {}
    if kind == '发票': return pg, {}
    if kind == '完税社保表' and cfg.get('完税社保倾斜'): pg, log['完税社保倾斜'] = L4.r_完税社保倾斜(pg, units, {'门槛': cfg.get('倾斜门槛', 0.002)})
    if kind in ('偏离表', '合同明细', '设备清单'):
        if cfg.get(kind + '守卫重建'): pg, log[kind + '守卫重建'] = r_表格守卫(pg, units, kind, cfg)
        elif cfg.get(kind + '重建_无守卫'): pg, log[kind + '重建'] = L4.grid(pg, units, kind, {k: True for k in L4.TABLE_OPTS})
    if kind in FOUR and cfg.get('页边注记'): pg, log['页边注记'] = r_页边注记(pg, units)
    if (kind in FOUR and cfg.get('标题置首')) or (kind not in FOUR and cfg.get('标题置首_全部非发票')): pg, log['标题置首'] = r_标题置首(pg, units, cfg.get('标题字高倍数', 1.3))
    return pg, {k: v for k, v in log.items() if v and not (isinstance(v, dict) and set(v) == {'守卫'})} | ({'守卫拦下': [k for k, v in log.items() if isinstance(v, dict) and set(v) == {'守卫'}]} if any(isinstance(v, dict) and set(v) == {'守卫'} for v in log.values()) else {})
