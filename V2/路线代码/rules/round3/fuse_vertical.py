#!/usr/bin/env python3
"""页边竖排融合·本地原型（事后/试算：规则在已看过的开发集 synth_blind_g 上写成，不冻结，不当盲测结论）。
只读已有缓存，不调用任何外部服务，不改主路径代码、冻结规则、synth_blind_g、参照和已有计分/归因文件。
输出只写本目录。运行：cd /workspace/pdf-test/baidu && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python <本文件>

规则（计分前写定）：
 R1 竖排条带：用冻结副本 vertical_margin.detect（原图 200 DPI 渲染缓存 + 文档解析块框，本地 OpenCV）找页边竖排条带。
 R2 竖排文字：只取缓存的二次识别（高精度）结果 运行/reread/A_P2/results.json 里的单元：
     中心落在条带内（纵向外扩 10px，横向外扩 max(30px, 条宽)）、框高 ≥ 2.5×框宽（竖条）、二次识别置信度 ≥ 0.80。
     条带上没有缓存文字的 → 不动（正式流程这里要调高精度，原型不调）。
 R3 删正文误检：文档解析里落在条带内的窄块（宽 < 40px，非印章/表格，同 vertical_margin.apply 的位置规则），
     若与竖排文字近似重复（窄块文字在竖排文字里的最佳局部相似度 ≥ 0.4），删掉预测里与该窄块文字相同（去标点后）的正文段。
 R4 插入：竖排文字作为一个 body 段放到该页正文末尾（与正式流程 vertical_margin 放页末一致）；
     若该页已有与竖排文字局部相似度 ≥ 0.95 的段，则不重复插入。
"""
import json, sys, re, copy, importlib.util, hashlib
from difflib import SequenceMatcher
from pathlib import Path
ROOT = Path('/workspace/pdf-test/baidu')
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
B = ROOT / 'output/盲测_版面约束'; RUN = B / '运行'; SET = B / 'synth_blind_g'
OUT = B / '版面分析优化计划/试算/并行_竖排融合'
spec = importlib.util.spec_from_file_location('vm_frozen', B / '冻结规则/基线C_方案E/src/baidu_ocr/postprocess/vertical_margin.py')
VM = importlib.util.module_from_spec(spec); spec.loader.exec_module(VM)
PX = 200 / 72.0
def jl(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def wj(p, o): Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding='utf-8')
def nz(s): return re.sub(r'[\s:：,，;；.。()（）\-－_]', '', s or '')
def psim(a, b):
    """a 在 b 里的最佳局部相似度（按 a 的长度滑窗）。"""
    a, b = nz(a), nz(b)
    if not a or not b: return 0.0
    if len(a) > len(b): a, b = b, a
    return max(SequenceMatcher(None, a, b[i:i + len(a)]).ratio() for i in range(len(b) - len(a) + 1))

def build():
    doc = jl(RUN / 'orig/work/output/final/scheme_b_standard/blind20-scan.json')
    strips = VM.detect(doc, RUN / 'orig/work/output/scheme_a/renders')
    units = jl(RUN / 'reread/A_P2/results.json')['units']
    plan = []
    for s in strips:
        p = s['page']; X0, Y0, X1, Y1 = s['box_px']; pad = 10; hpad = max(30, X1 - X0)
        def inside(cx, cy): return X0 - hpad <= cx <= X1 + hpad and Y0 - pad <= cy <= Y1 + pad
        cand = []
        for u in units:
            if u['page'] != p: continue
            x0, y0, x1, y1 = u['box_px']
            if inside((x0 + x1) / 2, (y0 + y1) / 2) and (y1 - y0) >= 2.5 * (x1 - x0) and u['pr'] >= 0.80 and u['r_text'].strip():
                cand.append(u)
        frags = [b for b in doc['blocks'] if b['page'] == p and b['type'] not in ('seal', 'table') and b.get('bbox')
                 and (b['bbox'][2] - b['bbox'][0]) * PX < 40
                 and inside((b['bbox'][0] + b['bbox'][2]) / 2 * PX, (b['bbox'][1] + b['bbox'][3]) / 2 * PX)]
        best = max(cand, key=lambda u: u['pr']) if cand else None
        plan.append({'页': p, '条带_px': s['box_px'], '边': s['side'], '缓存竖排文字': best['r_text'] if best else None,
                     '缓存来源': best['id'] if best else None, '置信度': best['pr'] if best else None,
                     '条带内正文窄块': [{'id': b['id'], 'text': b.get('text'), '与竖排相似度': round(psim(b.get('text'), best['r_text']), 3) if best else None} for b in frags],
                     '处理': '融合' if best else '无缓存文字，不动（正式流程需调用高精度）'})
    return plan

def fuse(pred, plan):
    pred = copy.deepcopy(pred); log = []
    for it in plan:
        v = it['缓存竖排文字']
        if not v: continue
        secs = pred[str(it['页'])]['sections']; removed = []
        for f in it['条带内正文窄块']:
            if f['与竖排相似度'] is not None and f['与竖排相似度'] >= 0.4:
                for i, s in enumerate(secs):
                    if s['section'] == 'body' and nz(s['text']) == nz(f['text']):
                        removed.append(secs.pop(i)['text']); break
        dup = any(psim(v, s['text']) >= 0.95 and len(nz(s['text'])) <= 2 * len(nz(v)) for s in secs)
        if not dup: secs.append({'section': 'body', 'text': v, 'layout_role': '页边竖排', 'source': '缓存二次识别·试算'})
        log.append({'页': it['页'], '删去正文段': removed, '插入': None if dup else v})
    return pred, log

def score(preds):
    import synth18b_score as SB, tolerant_score as T
    bc, bs, hs, refs, rj, mp, rng = SB.load_bs(SET)
    inv = sorted(p for p in mp if mp[p]['doc_type'] == '发票')
    K = ('ref_chars', 'matches', 'deletions', 'substitutions', 'insertions'); E = lambda o: o['deletions'] + o['substitutions'] + o['insertions']
    res = {}
    for n, pr in preds.items():
        tol = T.score_tolerant(bs, pr, rj, mp, 'in'); st = bs.score_system(pr, refs['full'], mp, None)
        pages = list(tol['per_page']); non = [p for p in pages if p not in inv]
        agg = lambda sc, ps: {k: sum(sc['per_page'][p]['overall'][k] for p in ps) for k in K}
        r = {'主_发票': agg(tol, inv), '主_全套': agg(tol, pages), '主_非发票': agg(tol, non),
             '严_发票': agg(st, inv), '严_全套': agg(st, list(st['per_page'])), '严_非发票': agg(st, [p for p in st['per_page'] if p not in inv]),
             '主_逐页': {p: E(tol['per_page'][p]['overall']) for p in pages}, '严_逐页': {p: E(st['per_page'][p]['overall']) for p in st['per_page']}}
        for k in list(r):
            if not k.endswith('逐页'):
                a = r[k]; r[k] = {**a, '错字': E(a), '读出率%': round(100 * a['matches'] / a['ref_chars'], 2), '错字率%': round(100 * E(a) / a['ref_chars'], 2)}
        res[n] = r
    return res, inv

if __name__ == '__main__':
    V = jl(RUN / '计分_无竖排/pred_三行.json')
    plan = build(); wj(OUT / '条带与缓存匹配.json', plan)
    preds = {'C': V['C'], '乙′（无门控）': V['乙′']}
    fused = {}
    for n in list(preds):
        f, lg = fuse(preds[n], plan); fused[n + '＋竖排融合'] = f; wj(OUT / f'融合日志_{"C" if n == "C" else "乙撇"}.json', lg)
    wj(OUT / 'pred_C＋竖排融合.json', fused['C＋竖排融合']); wj(OUT / 'pred_乙撇＋竖排融合.json', fused['乙′（无门控）＋竖排融合'])
    res, inv = score({**preds, **fused})
    wj(OUT / 'scores.json', {'说明': '事后/试算；开发集已看过；只用缓存；未调用外部服务', '发票页': inv, '结果': res,
                             '输入sha256': {str(p.relative_to(B)): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                           [RUN / '计分_无竖排/pred_三行.json', RUN / 'reread/A_P2/results.json', RUN / 'orig/work/output/final/scheme_b_standard/blind20-scan.json']}})
    for n, r in res.items():
        print(n, {k: (v['错字'], v['读出率%'], v['错字率%']) for k, v in r.items() if not k.endswith('逐页')})
    for it in plan: print(it)
