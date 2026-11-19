"""计分：事后修订版 乙′（参考）与基线 C；逐区域判定。纯本地。"""
import json, sys, copy
sys.path.insert(0, 'output/版面约束')
from common_lc import *
VA = json.load(open(D / 'variants.json')); VP = json.load(open(D / 'variants_posthoc.json'))
res = {'sets': {}, '区域': []}
for s, pf, ver, *_ in SETS:
    C = VA[s]['C']; V = {'C': C, '乙′': VP[s]['V']}
    for tag, bs, refs, rj, mp in scorers(s):
        inv = sorted(int(p) for p in C if mp[int(p)]['doc_type'] == '发票')
        S = {'发票页': inv}
        for n, pr in V.items():
            r = score(bs, refs, rj, mp, pr, inv); S[n] = {k: v for k, v in r.items() if not k.startswith('_')}
        res['sets'][tag] = S
        print(tag, {n: (E(S[n]['主口径_发票页']), E(S[n]['主口径_全套']), E(S[n]['严格_全套'])) for n in V}, flush=True)
        # 逐区域：把某变体某页某段单独换到 C 上
        for vn in ('乙′',):
            for p in C:
                for i, (xc, xv) in enumerate(zip(C[p]['sections'], V[vn][p]['sections'])):
                    if xc['text'] == xv['text']: continue
                    one = copy.deepcopy(C); one[p]['sections'][i]['text'] = xv['text']
                    r = score(bs, refs, rj, mp, one, inv)
                    d0, d1 = S['C']['主口径_逐页'][int(p)], r['主口径_逐页'][int(p)]
                    s0, s1 = S['C']['严格_逐页'][int(p)], r['严格_逐页'][int(p)]
                    res['区域'].append({'集': tag, '变体': vn, '页': int(p), '段': i, '发票页': int(p) in inv, '主口径前': d0, '主口径后': d1,
                                      '严格前': s0, '严格后': s1, '判定': '修正' if d1 < d0 else ('改错' if d1 > d0 else '不影响'),
                                      '原': xc['text'], '新': xv['text']})
json.dump(res, open(D / 'results_posthoc.json', 'w'), ensure_ascii=False, indent=1)
for x in res['区域']: print(x['集'], x['变体'], x['页'], x['段'], x['判定'], x['主口径前'], '→', x['主口径后'], '严', x['严格前'], '→', x['严格后'])
