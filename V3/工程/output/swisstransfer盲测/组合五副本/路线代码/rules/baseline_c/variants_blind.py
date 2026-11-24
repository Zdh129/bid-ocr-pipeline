"""盲测三行的生成（冻结，2026-11-19 生成 synth_blind_g 之前写定）。纯本地，不调用外部服务。
  C          = 方案E＋改进⊗（common_lc.base_C 的同一做法：方案E 预测 → r1 改进⊗补字）
  乙′        = C ＋ layout_posthoc.invoice_region(mode='乙′')，逐段做法与 output/版面约束/apply_posthoc.py 逐行相同
  乙′＋门控  = 只在 gate.gate(C 段文字) 触发时才用乙′的改写，否则保留 C
本文件只依赖本目录里冻结的副本（layout_posthoc.py、layout.py、lines.py、apply.py、common_lc.py、gate.py、二次高精度OCR_common.py）。
"""
import copy, json, sys
from pathlib import Path
FZ = Path(__file__).resolve().parent
ROOT = Path('/workspace/pdf-test/baidu')
for p in (ROOT / 'output/版面约束/deps', ROOT / 'src', ROOT / 'scripts', FZ):
    if str(p) not in sys.path: sys.path.insert(0, str(p))
import importlib.util
_s = importlib.util.spec_from_file_location('common', FZ / '二次高精度OCR_common.py'); _c = importlib.util.module_from_spec(_s); _s.loader.exec_module(_c)
sys.modules['common'] = _c                     # 让 common_lc / apply 导入到冻结副本
classify, r1 = _c.classify, _c.r1
import layout_posthoc as LP, lines as LN, gate as G
from baidu_ocr.hybrid.inputs import a_chars_for_page


def is_inv(pagetext, cls): return cls == '增值税发票' or '发票号码' in pagetext   # 与 apply.is_inv 相同


def base_C(E_pred):
    """E_pred：方案E 的 pred_pages（{页: {'sections': [...], 'seal': ...}}）。与 common_lc.nz + base_C 相同。"""
    A = {p: {**v, 'sections': [{**x, 'text': x['text'] or ''} for x in v['sections']]} for p, v in E_pred.items()}
    cls = {p: classify(''.join(x['text'] for x in v['sections'])) for p, v in A.items()}
    return {p: {**v, 'sections': [{**x, 'text': r1(x['text'], cls[p])[0]} for x in v['sections']]} for p, v in A.items()}


def make_variants(C, sa_dir, pdf, setname, cache_dir):
    """sa_dir：原图高精度识别（原图原走法 scheme_a，含 call_summary.json 与 raw）；pdf：扫描件；cache_dir：表格线缓存目录。"""
    LN.D = Path(cache_dir)                        # 缓存不写进冻结目录
    cls = {p: classify(''.join(x['text'] for x in v['sections'])) for p, v in C.items()}
    asum = {pg['page']: pg for pg in json.load(open(Path(sa_dir) / 'call_summary.json'))['pages']}
    V, VG, LOG = copy.deepcopy(C), copy.deepcopy(C), []
    for p in sorted(C, key=int):
        secs = C[p]['sections']; pt = ''.join(x['text'] for x in secs)
        if not is_inv(pt, cls[p]): continue
        for i, x in enumerate(secs):
            if x['section'] != 'table' or not G.region_span(x['text']) or int(p) not in asum: continue
            ach = a_chars_for_page(Path(sa_dir), asum[int(p)]); ln = LN.detect(pdf, setname, int(p))
            lg = []; new = LP.invoice_region(x['text'], ach, ln, '乙′', lg)
            g, why = G.gate(x['text'])
            LOG.append({'页': int(p), '段': i, '门控': g, '门控原因': why, **(lg[-1] if lg else {})})
            if new is not None:
                V[p]['sections'][i]['text'] = new
                if g: VG[p]['sections'][i]['text'] = new
    return {'C': C, '乙′': V, '乙′＋门控': VG}, LOG
