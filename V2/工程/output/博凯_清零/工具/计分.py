"""与 候选路线_第二版/复跑_第二版.py 第 5 节完全相同的计分（主口径 tolerant_score、严格口径 score_system），另加「不计顺序」口径。"""
import copy, json, sys
from collections import Counter
from pathlib import Path
ROOT = Path('/workspace/pdf-test/baidu'); TS = ROOT / 'output/盲测_版面约束/版面分析优化计划/试算'
sys.path[:0] = [str(TS / '第5轮/综合测试_2776d4b'), str(ROOT / 'src'), str(ROOT / 'scripts')]
import sets_all as SA, synth18b_score as SB, tolerant_score as T
BK = ROOT / 'output/博凯文件_最优路线测试'; JS = BK / '优化_第4轮/江苏留出'
REFS = {'博凯': BK / '博凯包/参照', '江苏': BK / '优化_第3轮/留出验证/江苏凭证/参照', **{s: Path(c['ref']) for s, c in SA.SETS.items()}}
SETS8 = list(SA.SETS)
K = ('ref_chars', 'matches', 'deletions', 'substitutions', 'insertions'); E = lambda o: o['deletions'] + o['substitutions'] + o['insertions']
rate = lambda a: {'错字': E(a), '参照字数': a['ref_chars'], '读出率': round(100 * a['matches'] / a['ref_chars'], 3), '错字率': round(100 * E(a) / a['ref_chars'], 3)}
_RC = {}
def load(rd):
    if str(rd) not in _RC:
        bc, bs, hs, refs, rj, mp, rng = SB.load_bs(rd); f = Path(rd) / '图片区不计分行.json'
        img = {int(k): v for k, v in json.load(open(f)).items()} if f.exists() else {}
        rj2 = copy.deepcopy(rj)
        for pg in rj2['pages']: pg['ref_lines'] = [l for l in pg['ref_lines'] if l.get('kind') != 'image']
        _RC[str(rd)] = (bs, refs, rj2, mp, img)
    return _RC[str(rd)]
def unordered(bs, refs, pr, pages):
    """不计顺序：逐页按字符多重集比较，错字＝max(多出, 缺少)。"""
    tot = 0; nref = 0; per = {}
    for p in pages:
        rs, _ = bs.ref_chars(refs['full'][p]); pp = pr.get(str(p)) or {'sections': []}
        ps = bs.norm(''.join(s['text'] for s in pp['sections'])); a, b = Counter(rs), Counter(ps)
        e = max(sum((a - b).values()), sum((b - a).values())); per[str(p)] = e; tot += e; nref += len(rs)
    return {'错字': tot, '参照字数': nref, '错字率': round(100 * tot / nref, 3), '读出率_上限': round(100 * (nref - sum(max(0, 0) for _ in [0]) - 0) / nref, 3)}, per
def score(s, pr):
    rd = REFS[s]; bs, refs, rj, mp, img = load(rd); pr = copy.deepcopy(pr)
    tol = T.score_tolerant(bs, pr, rj, mp, 'in'); st = bs.score_system(pr, refs['full'], mp, None); pages = sorted(tol['per_page'])
    agg = lambda x, ps: {k: sum(x['per_page'][p]['overall'][k] for p in ps) for k in K}
    uo, uop = unordered(bs, refs, pr, pages)
    return {'主': rate(agg(tol, pages)), '严': rate(agg(st, pages)), '不计顺序': {'错字': uo['错字'], '错字率': uo['错字率']},
            '主_逐页': {str(p): E(tol['per_page'][p]['overall']) for p in pages}, '严_逐页': {str(p): E(st['per_page'][p]['overall']) for p in pages}, '不计顺序_逐页': uop}
