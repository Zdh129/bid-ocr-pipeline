"""整合版_v5 候选（用户 09:04 拍板）：在 v4 冻结参数基础上只改一处——整合版侧旋转规则从「只重排」改为「对不上就重建」（与候选路线_v2 的 auto 一致）。
- 判定：本页在去章图高精度行框上被检测为旋转页（与 v4 相同的检测与行框），且整合版输出行与这些行框对上的比例 < 0.5。
- 重建：只用已付费的**原图高精度**结果（原图行框，按旋转规则的摆正与阅读顺序重建；章框内的字不进正文），与候选路线_v2 相同。
- 对得上（≥0.5）：与 v4 完全相同，按去章图行框只重排。
- 若需要重建但原图高精度缓存缺失：抛出 缺缓存 异常（不调用、不补跑）。
- 原图行框未检测为旋转页（与去章图判定不一致）：退回 v4 的只重排，并记日志。
其余（交接阈值、规则1′、后处理）与 v4 一字不差。不看文件名／页码／参照。0 次调用。"""
import sys
from copy import deepcopy
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))
import 公共v3 as V
RR, A, PP, R, PO = V.RR, V.A, V.PP, V.R, V.PO
class 缺缓存(Exception): pass
MIN_MATCH = 0.5
_ROT5 = {}; 日志 = []
def rot_integ(d):
    k = (str(d['sa_o']), str(d['sa_p2']), d['q'])
    if k in _ROT5: return _ROT5[k]
    q, pg = d['q'], d['integ']
    inf_p2 = RR.info(d['sa_p2'], q)
    if inf_p2 is None: res = (pg, None)
    elif RR.match_rate(pg, inf_p2) >= MIN_MATCH:
        out, lg = RR.reorder_lines(pg, inf_p2); res = (out, {'方式': '重排', **lg} if lg else None)
    else:
        if RR.load(d['sa_o'], q) is None: raise 缺缓存(f"{d['k']}：需要重建但缺原图高精度缓存（{d['sa_o']}）")
        inf_o = RR.info(d['sa_o'], q)
        if inf_o is None:
            out, lg = RR.reorder_lines(pg, inf_p2); res = (out, {'方式': '原图未判为旋转，退回只重排'})
        else:
            out, lg = RR.rebuild(pg, inf_o); res = (out, {'方式': '重建（原图高精度行框）', '方向': inf_o['方向'], **lg})
    if res[1]: 日志.append({'页': d['k'], **res[1]})
    _ROT5[k] = res; return res
def v5(d, **kw):
    """与 公共v3.v3 相同的流程，只把整合版侧的旋转处理换成 rot_integ。"""
    P = {**V.DEF, **kw}
    assert P['rot'] and not (P['r3'] or P['r4'] or P['r5'] or P['inv'])
    ig = rot_integ(d)[0]
    sx = V.rot(d, 'six')[0]
    s = V.sr(d, P['ov'], P['minlen'])
    if P['handover'] is not None and s >= P['handover']:
        out = deepcopy(sx); out, _ = A.watermark(out); out, _ = A.invoice_cells(out, V.units(d['sa_o'], d['q'])); out, _, _ = PP.footer_guarded(A, out, P['guard'])
        return out, '第六轮'
    pg = ig
    if P['seal']: pg, _ = R.seal_filter(pg, V.units(d['sa_p2'], d['q']), V.boxes(d['sa_o'], d['q']), PO.norm, conf=P['conf'])
    if P['reord'] is not None and s >= P['reord']: pg, _ = R.reorder_by_doc(pg, d['doc'], PO.norm, thr=P['docthr'])
    out = deepcopy(pg); out, _ = A.watermark(out); out, _ = A.invoice_cells(out, V.units(d['sa_p2'], d['q'])); out, _, _ = PP.footer_guarded(A, out, P['guard'])
    return out, '整合版'
