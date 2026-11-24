"""事后修订版 乙′ 的预测（只有规则一，纯本地）。结果写 variants_posthoc.json。"""
import json, re, sys, copy
from pathlib import Path
sys.path.insert(0, 'output/版面约束')
from common_lc import *
import layout_posthoc as LP, lines as LN
from apply import is_inv, region_span
from baidu_ocr.hybrid.inputs import a_chars_for_page
VA = json.load(open(D / 'variants.json'))
OUT = {}
for s, pf, ver, sa, pdf in SETS:
    C = VA[s]['C']; cls = {p: classify(''.join(x['text'] for x in v['sections'])) for p, v in C.items()}
    asum = {pg['page']: pg for pg in json.load(open(Path(sa) / 'call_summary.json'))['pages']}
    V = copy.deepcopy(C); LOG = []
    for p in sorted(C, key=int):
        secs = C[p]['sections']; pt = ''.join(x['text'] for x in secs)
        if not is_inv(pt, cls[p]): continue
        for i, x in enumerate(secs):
            if x['section'] != 'table' or not region_span(x['text']) or int(p) not in asum: continue
            ach = a_chars_for_page(Path(sa), asum[int(p)]); ln = LN.detect(pdf, s, int(p))
            lg = []; new = LP.invoice_region(x['text'], ach, ln, '乙′', lg)
            LOG.append({'页': int(p), '段': i, '规则': '规则一', '变体': '乙′', **(lg[-1] if lg else {})})
            if new is not None: V[p]['sections'][i]['text'] = new
    OUT[s] = {'V': V, 'log': LOG}
    for x in LOG:
        print('=====', s, x['页'], x.get('结果') or x.get('跳过'), {k: x[k] for k in x if k not in ('原', '新', '页', '段', '规则', '变体', '结果')})
        if x.get('结果') == '改写': print(x['新'])
json.dump(OUT, open(D / 'variants_posthoc.json', 'w'), ensure_ascii=False, indent=1)
