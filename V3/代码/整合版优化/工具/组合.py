"""各组合的逐页构建（盲测集 25 页与新盲测集共用；冻结后不再改）。
整合版_v2：并排行比例≥0.5 → 整页第六轮；否则整合版；带保护后处理。
整合版_v3：整合版 ＋ 规则二（章影过滤）＋ 并排行比例≥0.5 的页做规则一′（按文档解析顺序重排）＋ 带保护后处理；
           只有并排行比例≥0.85 的页（整页几乎都是左右并排，如发票截图、表单）才整页交给第六轮。
           交接阈值 0.85、规则一′只用于并排页，都是在盲测集_v1 上看过后定的：评估集上调过，结果偏乐观。
整合版_v3（不交接）：同上但不交给第六轮。"""
import 难页特征 as F, 后处理_v2 as PP, 版面规则 as R
def v2(A, PO, integ_pg, six_pg, u_p2, u_o, red_sa, q):
    f = F.feats(integ_pg, u_p2, red_sa, q, A.L4)
    if f['并排行比例'] >= 0.5: return PP.post(A, six_pg, u_o), '第六轮', f
    return PP.post(A, integ_pg, u_p2), '整合版', f
def v3(A, PO, integ_pg, six_pg, u_p2, u_o, red_sa, q, boxes, doc_pg, handover=0.85):
    f = F.feats(integ_pg, u_p2, red_sa, q, A.L4)
    if handover is not None and f['并排行比例'] >= handover: return PP.post(A, six_pg, u_o), '第六轮', f
    pg, _ = R.seal_filter(integ_pg, u_p2, boxes, PO.norm)
    if f['并排行比例'] >= 0.5: pg, _ = R.reorder_by_doc(pg, doc_pg, PO.norm)
    return PP.post(A, pg, u_p2), '整合版', f
