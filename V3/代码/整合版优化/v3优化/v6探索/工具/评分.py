"""用法：评分.py 集名 变体1,变体2,...（变体用 + 连接规则，如 1+3a+5a；'v5' 为基线）。0 次调用。"""
import sys, json
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))

def main(argv=None):
    argv = list(argv or sys.argv)
    if len(argv) < 3:
        raise SystemExit("用法：评分.py 集名 变体1,变体2,... [tag]")
    ST, VARS = argv[1], argv[2].split(',')
    import 公共v6 as C
    V = C.V
    if ST == '新扫描9':
        N9 = C._n9(); V.C8 = N9.C9; V.SETS['新集8'] = V.SETS['新9']; KEY = '新集8'
    else:
        KEY = ST
        if KEY not in V.SETS:
            raise C.LP.MissingInputError(
                f"未知集名：{ST}。可用：盲测25、新集8、65扫描、新扫描9。正式运行需要本地识别缓存与参照，请设置环境变量 OCR_BAIDU_ROOT。"
            )
        # 先加载本集；缺缓存或清单时在此处明确报错，不写成绩。
        V.SETS[KEY]
    pms, rts = [], []
    pm5, rt5 = V.run(KEY, lambda d: C.F.v5(d, **C.P5))
    for v in VARS:
        rules = () if v == 'v5' else tuple(v.split('+'))
        pm, rt = V.run(KEY, lambda d: C.v6(d, rules)); pms.append(pm); rts.append(rt)
        if v == 'v5': assert all(V._h(pm[k]) == V._h(pm5[k]) for k in pm), 'v6 空规则 ≠ v5'
    pmc, rtc = V.run(KEY, V.cand_v2)
    sc = V.batch(KEY, [pm5] + pms + [pmc])
    def pp(pm): return {k: V._PP.get((KEY, k, V._h(pm[k]))) for k in pm}
    base = pp(pm5); out = {'集': ST, '变体': {}}
    for nm, pm, rt, r in zip(['v5'] + VARS + ['候选v2'], [pm5] + pms + [pmc], [rt5] + rts + [rtc], sc):
        p = pp(pm); co, n6 = V.cost(rt)
        good = {k: (base[k]['主e'], p[k]['主e']) for k in p if p[k] and p[k]['主e'] < base[k]['主e']}
        bad = {k: {'主': (base[k]['主e'], p[k]['主e']), '严': (base[k]['严e'], p[k]['严e']), '序': (base[k]['序'], p[k]['序'])} for k in p if p[k] and any(p[k][x] > base[k][x] for x in ('主e', '严e', '序'))}
        out['变体'][nm] = {'主': r['主'], '严': r['严'], '不计顺序': r['不计顺序'], '参照字数': r['参照字数'], '每千页成本_元': co, '变好页': good, '变差页（任一口径）': bad,
                          '逐页': {k: (p[k]['主e'], p[k]['严e'], p[k]['序']) for k in p if p[k]}}
    if ST == '新扫描9':
        K7 = [k for k in base if base[k] and not C._n9().单[int(k)]['博凯（独立性较弱）']]
        for nm, pm in zip(['v5'] + VARS + ['候选v2'], [pm5] + pms + [pmc]):
            p = pp(pm); n = sum(p[k]['n'] for k in K7); r = {'参照字数': n}
            for c in ('主', '严'):
                m = sum(p[k][c + 'm'] for k in K7); e = sum(p[k][c + 'e'] for k in K7); r[c] = {'错字': e, '读出率': round(100 * m / n, 2), '错字率': round(100 * e / n, 2)}
            u = sum(p[k]['序'] for k in K7); r['不计顺序'] = {'错字': u, '错字率': round(100 * u / n, 2)}
            out['变体'][nm]['7页（去博凯）'] = r
    out['规则日志'] = C.日志
    tag = argv[3] if len(argv) > 3 else 'run'
    (C.X / '成绩').mkdir(exist_ok=True)
    json.dump(out, open(C.X / f'成绩/{tag}_{ST}.json', 'w'), ensure_ascii=False, indent=1, default=str)
    for nm, x in out['变体'].items(): print(ST, nm, x['主'], x['严'], x['不计顺序'], x['每千页成本_元'], '好', x['变好页'], '差', x['变差页（任一口径）'])
    print('日志', [(x['页'], x['规则']) for x in C.日志])

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        if type(e).__name__ == 'MissingInputError':
            print(e, file=sys.stderr)
            sys.exit(1)
        raise
