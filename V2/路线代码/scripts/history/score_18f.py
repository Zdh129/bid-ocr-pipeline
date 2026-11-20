#!/usr/bin/env python3
"""synth18f 留出集一次性计分：C（方案E＋改进⊗，含真实页边竖排识别）vs 冻结路线（../冻结_新盲测前/frozen_stack.run_stack）。
外部调用只经 scripts/synth18f_run.py（及 本地补测_1119/synth18f_只重发文档解析.py）完成；本脚本不联网。
先生成全部预测并记 sha → 同时写 output/synth18f/SCORED 与 本目录 运行_18f/SCORED（独占）→ 才打开参照计分。只计一次。"""
import json, sys, hashlib, re
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import yaml
ROOT = Path('/workspace/pdf-test/baidu'); sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
NB = Path(__file__).resolve().parent; OUTL = NB / '运行_18f'; OUTL.mkdir(exist_ok=True)
FZ = ROOT / 'output/盲测_版面约束/冻结规则'; ZD = NB.parent / '冻结_新盲测前'
R18 = ROOT / 'output/synth18f'; WO, WP = R18 / 'orig/work', R18 / 'P2/work'; SET = Path('/workspace/pdf-test/synth18f')
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
now = lambda: datetime.now(ZoneInfo('Europe/London')).strftime('%Y-%m-%d %H:%M:%S')
def wj(p, o): Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
def verify():
    fz = json.loads((ROOT / 'output/synth18e_prep/frozen_方案E.json').read_text())
    assert not [c for c, s in fz['code_sha256'].items() if sha(ROOT / c) != s], '方案E 冻结代码被改'
    v = yaml.safe_load((ROOT / 'config_方案E.yaml').read_text(encoding='utf-8'))['方案E']['variant']
    assert v == fz['variant']
    zr = (ZD / '冻结记录.md').read_text(encoding='utf-8')
    for f, h in re.findall(r'\| `([^`]+)` \| `([0-9a-f]{64})` \|', zr):
        p = ZD / f if (ZD / f).exists() else ROOT / f
        assert sha(p) == h, f'冻结路线被改：{f}'
    return v
if __name__ == '__main__':
    variant = verify()
    if (R18 / 'SCORED').exists() or (OUTL / 'SCORED').exists(): sys.exit('synth18f 已计过分，拒绝再跑')
    import f1r2 as F
    sys.path[:0] = [str(FZ), str(ZD)]
    import variants_blind as VB, frozen_stack as Z
    name = 'synth18f'; bdoc = WO / 'output/final/scheme_b_standard/blind20-scan.json'
    entry = {'dir': SET, 'b': bdoc, 'root': WO, 'a': WP / 'output/scheme_a', 'u': WP / 'output/scheme_b_unlimited/raw/blind20-scan.parse_result.json',
             'reread': R18 / 'reread/A_P2/results.json', 'render': WO / 'output/scheme_a/renders', 'llm_caches': [], 'llm_cfg': None,
             'llm_spent_before': 0.0, 'blind': True, 'a_orig_summary': WO / 'output/scheme_a/call_summary.json'}
    assert (R18 / name / 'vertical/results.json').exists(), '缺页边竖排结果'
    F.OUT = R18; F.DS[name] = entry
    c = F.Ctx(name); E, cnt, lg = c.run(variant, llm=None)
    wj(OUTL / 'pred_方案E.json', {'方案E': E}); E = json.loads((OUTL / 'pred_方案E.json').read_text())['方案E']
    C = VB.base_C(E)
    st, LOG = Z.run_stack(C, WO / 'output/scheme_a', SET / 'synth18f-scan.pdf', name, OUTL / '表格线缓存', bdoc, WO / 'output/scheme_a/renders', R18 / 'reread/A_P2/results.json')
    V = {'C': C, '冻结路线': st['S6_竖排融合'], **{k: v for k, v in st.items() if k not in ('C', 'S6_竖排融合')}}
    wj(OUTL / 'pred_全部.json', V); wj(OUTL / '冻结路线日志.json', LOG); ps = sha(OUTL / 'pred_全部.json')
    note = f'{now()} 由 版面分析优化计划/试算/新盲测/score_18f.py 一次性计分（C vs 冻结路线，donghao 09:22 批准）pred_sha256={ps}'
    with open(R18 / 'SCORED', 'x') as f: f.write(note)
    with open(OUTL / 'SCORED', 'x') as f: f.write(note)
    import synth18b_score as SB, tolerant_score as T
    bc, bs, hs, refs, rj, mp, rng = SB.load_bs(SET)
    inv = sorted(p for p in mp if mp[p]['doc_type'] == '发票')
    K = ('ref_chars', 'matches', 'deletions', 'substitutions', 'insertions'); E_ = lambda o: o['deletions'] + o['substitutions'] + o['insertions']
    res = {}
    for n, pr in V.items():
        tol = T.score_tolerant(bs, pr, rj, mp, 'in'); s2 = bs.score_system(pr, refs['full'], mp, None)
        agg = lambda sc, pages: {k: sum(sc['per_page'][p]['overall'][k] for p in pages) for k in K}
        non = [p for p in tol['per_page'] if p not in inv]
        res[n] = {'主口径_全套': agg(tol, list(tol['per_page'])), '主口径_发票页': agg(tol, inv), '主口径_非发票页': agg(tol, non),
                  '严格_全套': agg(s2, list(s2['per_page'])), '严格_发票页': agg(s2, inv),
                  '主口径_逐页': {p: E_(tol['per_page'][p]['overall']) for p in tol['per_page']}, '严格_逐页': {p: E_(s2['per_page'][p]['overall']) for p in s2['per_page']}}
    wj(OUTL / 'scores.json', {'计分时间_伦敦': now(), 'pred_sha256': ps, '发票页': inv, '方案E步骤': cnt, '结果': res})
    print(json.dumps({n: {k: E_(v) for k, v in r.items() if not k.endswith('逐页')} for n, r in res.items()}, ensure_ascii=False))
