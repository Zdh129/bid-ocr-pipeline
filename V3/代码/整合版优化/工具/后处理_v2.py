"""整合版_v2 后处理：与候选路线_v2 相同的 改动4 → 改动3 → 改动2，改动2 加整页误删保护（用户拍板 t494s2188）：
一次要删掉本页超过 30% 的字（按去空白后的字数）就不删，原样保留。通用规则，不按文件名或页码。不改 候选路线_v2/工具/后处理.py 原文件。"""
from copy import deepcopy
def _n(pg, norm): return len(norm(''.join(s.get('text') or '' for s in pg.get('sections') or [])))
def footer_guarded(A, pg, ratio=0.30):
    out, lg = A.footer(pg)
    if not lg: return out, None, None
    n0, n1 = _n(pg, A.norm), _n(out, A.norm)
    if n0 and (n0 - n1) / n0 > ratio:
        return pg, None, {'拦下': True, '删前': n0, '删后': n1}
    return out, lg, {'拦下': False, '删前': n0, '删后': n1}
def post(A, pg, units, logs=None, pid=None):
    out = deepcopy(pg)
    out, lg = A.watermark(out)
    if lg and logs is not None: logs.append({'页': pid, '改动': 4})
    out, lg = A.invoice_cells(out, units)
    if lg and logs is not None: logs.append({'页': pid, '改动': 3})
    out, lg, g = footer_guarded(A, out)
    if g and logs is not None: logs.append({'页': pid, '改动': 2, **g})
    return out
