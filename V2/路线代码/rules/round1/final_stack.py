"""最终叠加（事后/试算，纯本地，不冻结）。顺序：
 S1 乙′（不加门控，已有结果）
 S2 合计行切分 → 表头定位 → 行/格检测（versions.py 的 heji_split / header_fix / row_split；只在发票页表格段）
 S3 安全闸事后版：发票页上与 C 不同的段只查 覆盖率≥85% 与 字符多重集合不变，不过就退回 C
 S4 非发票有线表格（冻结 layout.ruled_table），过完整安全闸（含 y 跨度项），不过就不改
 S5 栏位纠正（并行任务 field_correct.correct_pred，发票页用文本判断 is_inv，不用参照 manifest）
 S6 竖排融合（并行任务 fuse_vertical.fuse，最后一步；条带计划按各集自己的缓存现建，规则同 fuse_vertical.build；
     没有二次识别缓存的集（synth18、synth18b）不做）
发票页判定：计分时按 manifest（与历次报告一致）；改写时只用文本判断（is_inv_page）。"""
import copy, json, sys, os
from pathlib import Path
TS = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(Path(__file__).resolve().parent)]   # 冻结副本：只从本目录导入
import core as K, versions as VS
import field_correct as FC, fuse_vertical as FV
OUTD = TS / '最终叠加'   # 仅 __main__ 用
O = K.ROOT / 'output'
def paths(name):
    if name == 'synth_blind_g':
        R = K.RUN; return R / 'orig/work/output/final/scheme_b_standard/blind20-scan.json', R / 'orig/work/output/scheme_a/renders', R / 'reread/A_P2/results.json'
    if name == 'blind20': return O / 'final/scheme_b_standard/blind20-scan.json', O / 'scheme_a/renders', O / 'blind20_frozen/reread/A_P2/results.json'
    d = O / name; sub = 'work' if name == 'synth18' else 'orig/work'
    return d / sub / 'output/final/scheme_b_standard/blind20-scan.json', d / sub / 'output/scheme_a/renders', d / 'reread/A_P2/results.json'
def build_plan(name, fp=None):
    """与 fuse_vertical.build 相同的规则，只是路径换成各集自己的缓存（新集用 S.fusion_paths 传入）。"""
    docp, rend, rr = fp if fp else paths(name)
    if not rr.exists() or not docp.exists(): return None
    doc = FV.jl(docp); strips = FV.VM.detect(doc, rend); units = FV.jl(rr)['units']; plan = []
    for s in strips:
        p = s['page']; X0, Y0, X1, Y1 = s['box_px']; pad = 10; hpad = max(30, X1 - X0)
        inside = lambda cx, cy: X0 - hpad <= cx <= X1 + hpad and Y0 - pad <= cy <= Y1 + pad
        cand = [u for u in units if u['page'] == p and inside((u['box_px'][0] + u['box_px'][2]) / 2, (u['box_px'][1] + u['box_px'][3]) / 2)
                and (u['box_px'][3] - u['box_px'][1]) >= 2.5 * (u['box_px'][2] - u['box_px'][0]) and u['pr'] >= 0.80 and u['r_text'].strip()]
        frags = [b for b in doc['blocks'] if b['page'] == p and b['type'] not in ('seal', 'table') and b.get('bbox')
                 and (b['bbox'][2] - b['bbox'][0]) * FV.PX < 40 and inside((b['bbox'][0] + b['bbox'][2]) / 2 * FV.PX, (b['bbox'][1] + b['bbox'][3]) / 2 * FV.PX)]
        best = max(cand, key=lambda u: u['pr']) if cand else None
        plan.append({'页': p, '条带_px': s['box_px'], '缓存竖排文字': best['r_text'] if best else None,
                     '条带内正文窄块': [{'id': b['id'], 'text': b.get('text'), '与竖排相似度': round(FV.psim(b.get('text'), best['r_text']), 3) if best else None} for b in frags]})
    return plan
def stack(S):
    C = S.C; st = {'C': C, 'S1_乙′': S.Yp}; LOG = {}
    def per_section(prev, fn):
        cur = copy.deepcopy(prev); lg = []
        for p in sorted(cur, key=int):
            if not S.is_inv_page(p): continue
            for i, x in enumerate(cur[p]['sections']):
                if x['section'] != 'table': continue
                l1 = []; new = fn(x['text'], S.achars(p), l1)
                if new is not None: x['text'] = new; lg.append({'页': int(p), '段': i, '记录': l1})
        return cur, lg
    v, a = per_section(S.Yp, VS.heji_split); v, b = per_section(v, VS.header_fix); v, c = per_section(v, VS.row_split)
    st['S2_合计行表头'] = v; LOG['S2'] = a + b + c
    v = copy.deepcopy(v); lg = []
    for p in v:
        if not S.is_inv_page(p): continue
        for i, x in enumerate(v[p]['sections']):
            old = C[p]['sections'][i]['text']
            if x['text'] == old: continue
            ok, why = VS.safety(old, x['text'], S.achars(p), alt=True); lg.append({'页': int(p), '段': i, '通过': ok, '原因': why})
            if not ok: x['text'] = old
    st['S3_安全闸'] = v; LOG['S3'] = lg
    v = copy.deepcopy(v); lg = []
    for p in sorted(v, key=int):
        if S.is_inv_page(p): continue
        for i, x in enumerate(v[p]['sections']):
            if x['section'] != 'table' or not S.achars(p): continue
            l1 = []; new = K.L.ruled_table(x['text'], S.achars(p), S.lines(p), l1)
            if new is None: continue
            ok, why = VS.safety(x['text'], new, S.achars(p), alt=False); lg.append({'页': int(p), '段': i, '改写': ok, '安全闸': why})
            if ok: x['text'] = new
    st['S4_非发票有线表格'] = v; LOG['S4'] = lg
    inv_txt = [int(p) for p in v if S.is_inv_page(p)]
    v, lg = FC.correct_pred(v, invoice_pages=inv_txt); st['S5_栏位纠正'] = v; LOG['S5'] = lg
    plan = build_plan(S.name, getattr(S, 'fusion_paths', None))
    if plan: v, lg = FV.fuse(v, plan); LOG['S6'] = {'计划': plan, '日志': lg}
    else: LOG['S6'] = '无二次识别缓存，不做'
    st['S6_竖排融合'] = v
    return st, LOG
if __name__ == '__main__':
    for s in sys.argv[1:]:
        S = K.Set(s); st, LOG = stack(S)
        sc = {n: K.metrics(S, pr) for n, pr in st.items()}
        json.dump({'pred': st, 'log': LOG}, open(OUTD / f'{s}_pred.json', 'w'), ensure_ascii=False, indent=1, default=str)
        json.dump(sc, open(OUTD / f'{s}_scores.json', 'w'), ensure_ascii=False, indent=1)
        for t in sc['C']: print(t, ' | '.join(f"{n}:{K.E(sc[n][t]['主_发票'])}/{K.E(sc[n][t]['主_全套'])}/严{K.E(sc[n][t]['严_发票'])}" for n in sc), flush=True)
