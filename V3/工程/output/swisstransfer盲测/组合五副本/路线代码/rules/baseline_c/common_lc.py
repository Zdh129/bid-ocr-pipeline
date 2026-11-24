"""版面约束：公共部分（只读已有输出，不调用任何外部服务）。工作目录 /workspace/pdf-test/baidu。"""
import json, sys
from pathlib import Path
ROOT = Path('/workspace/pdf-test/baidu')
sys.path.insert(0, str(ROOT / 'output/二次高精度OCR')); sys.path.insert(0, str(ROOT / 'src')); sys.path.insert(0, str(ROOT / 'scripts'))
from common import SETS, r1, classify
import synth18b_score as SB, tolerant_score as T
D = ROOT / 'output/版面约束'
K = lambda o: {k: o[k] for k in ('ref_chars', 'matches', 'deletions', 'substitutions', 'insertions')}
E = lambda o: o['deletions'] + o['substitutions'] + o['insertions']
def nz(pr): return {p: {**v, 'sections': [{**x, 'text': x['text'] or ''} for x in v['sections']]} for p, v in pr.items()}
def base_C(pf, ver):
    A = nz(json.load(open(pf))[ver])
    cls = {p: classify(''.join(x['text'] for x in v['sections'])) for p, v in A.items()}
    return {p: {**v, 'sections': [{**x, 'text': r1(x['text'], cls[p])[0]} for x in v['sections']]} for p, v in A.items()}, cls
def blind_scorer(refdir):
    import bestconfig_run as bc
    bs = bc.load_mod("blind20_score", ROOT / "scripts" / "blind20_score.py")
    bs.REF, bs.MANIFEST = refdir / "blind-ref.txt", refdir / "manifest.json"; bs.MANUAL.clear()
    full = bs.REF.read_text(encoding="utf-8"); rj = json.load(open(refdir / "blind-ref.json"))
    refs = {"full": bs.parse_ref(full)}; mp = {int(p["output_page"]): p for p in json.load(open(refdir / "manifest.json"))["pages"]}
    return bs, refs, rj, mp
def scorers(s):
    if s == 'blind20':
        return [('blind20_旧参照', *blind_scorer(ROOT / 'output/blind20_frozen/计分参照')),
                ('blind20_校准参照', *blind_scorer(ROOT / 'output/格式规则实装/参照_v2'))]
    bc, bs, hs, refs, rj, mp, rng = SB.load_bs(Path('/workspace/pdf-test') / s)
    return [(s, bs, refs, rj, mp)]
def score(bs, refs, rj, mp, pr, inv):
    tol = T.score_tolerant(bs, pr, rj, mp, 'in'); st = bs.score_system(pr, refs['full'], mp, None)
    return {'主口径_全套': K(tol['total']['overall']), '严格_全套': K(st['total']['overall']),
            '主口径_发票页': {k: sum(tol['per_page'][p]['overall'][k] for p in inv) for k in K(tol['total']['overall'])},
            '严格_发票页': {k: sum(st['per_page'][p]['overall'][k] for p in inv) for k in K(st['total']['overall'])},
            '主口径_逐页': {p: E(tol['per_page'][p]['overall']) for p in tol['per_page']},
            '严格_逐页': {p: E(st['per_page'][p]['overall']) for p in st['per_page']}, '_tol': tol, '_st': st}
