"""规则逐条消融／多方法测试的公共框架（离线，只读第二版预测与百度缓存原始结果；0 次百度调用）。
apply_v3(s, pr, cfg)：在第二版预测上按 cfg 叠加第三版各小规则（顺序与第三版复跑一致：B/C/D/E 删字 → A 重排 → F）。
score_all(preds)：四套（江苏、八套、博凯）计分；按预测内容哈希缓存成绩，并行计算。"""
import hashlib, json, os, sys, copy
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
ROOT = Path('/workspace/pdf-test/baidu'); O = ROOT / 'output'; A = O / '第三版_规则逐条消融'
sys.path[:0] = [str(A / '规则代码'), str(O / '博凯_清零/工具'), str(ROOT / 'src'), str(ROOT / 'scripts')]
import phys_order as PO, v3_layers as V, 计分 as S
from baidu_ocr.score import levenshtein_alignment
BK = O / '博凯文件_最优路线测试'; JS = BK / '优化_第4轮/江苏留出'
SETS8 = S.SETS8; ALL = ['江苏'] + SETS8 + ['博凯']
FULL = {k: True for k in ('A_物理行重排', 'A_竖排单字归最近行', 'A_左括号并右', 'A_浮动行跟随锚点', 'B_认可标志页脚', 'B_签发人栏上延', 'B_整行补删',
                          'C_低置信度英文杂字', 'D_超大字低置信度', 'D_零散残字补删', 'E_页首英文徽标', 'F_表单盖章栏补括号')}
def src(s, p):
    if s == '博凯': return (BK / ('博凯包' if p <= 7 or p > 14 else '博凯包_社保重渲') / '运行/orig/work/output/scheme_a', p if p <= 7 or p > 14 else p - 7)
    if s == '江苏': return (JS / '运行/orig/work/output/scheme_a', p)
    return (Path(S.SA.SETS[s]['sa_o']), p)
_U = {}
def units(s, p):
    k = (s, p)
    if k not in _U: d, q = src(s, p); _U[k] = PO.load_units(d, q)
    return _U[k]
def v2(s):
    return {p: {k: v for k, v in pg.items() if k != 'meta'} for p, pg in json.load(open(O / '候选路线_第二版/预测' / f'{s}.json')).items()}
def apply_v3(s, pr, cfg):
    out = {}
    for p, pg in pr.items():
        u = units(s, int(p))
        if u is not None and pg.get('sections') and any(cfg.get(k) for k in ('B_认可标志页脚', 'C_低置信度英文杂字', 'D_超大字低置信度', 'E_页首英文徽标')):
            pg, _ = V.noise_page(pg, u, cfg)
        if V.is_tax_form(pg):
            if cfg.get('A_物理行重排') and u is not None:
                pg, _ = PO.reorder(pg, u, opt={k2: bool(cfg.get('A_' + k2)) for k2 in ('竖排单字归最近行', '左括号并右', '浮动行跟随锚点')})
            if cfg.get('F_表单盖章栏补括号'): pg, _ = V.fix_seal_label(pg)
        out[p] = pg
    return out
def text(pg): return PO.norm(''.join(x.get('text') or '' for x in (pg or {}).get('sections') or []))
def changes(s, a, b):
    """相对 a，b 在计分页上的触发页数与改动字数（逐页 Levenshtein 距离之和）。"""
    pages = S.load(S.REFS[s])[1]['full'].keys(); np = nc = 0
    for p in pages:
        x, y = text(a.get(str(p))), text(b.get(str(p)))
        if x != y:
            np += 1; al = levenshtein_alignment(y, x); nc += al['deletions'] + al['substitutions'] + al['insertions']
    return np, nc
CACHE = A / '工具/成绩缓存.json'
def _h(s, pr): return hashlib.sha256((s + json.dumps({p: text(v) for p, v in sorted(pr.items(), key=lambda t: int(t[0]))}, ensure_ascii=False)).encode()).hexdigest()
def _score_one(args):
    s, pr = args; r = S.score(s, pr)
    return {'主': r['主'], '严': r['严'], '不计顺序': r['不计顺序'], '主_逐页': r['主_逐页'], '严_逐页': r['严_逐页']}
def score_all(jobs):
    """jobs: [(set, pred)] → 成绩列表；内容相同的预测只算一次。注意：计分只看正文拼接后的规范化文本，因此按规范化文本哈希缓存是安全的。"""
    cache = json.load(open(CACHE)) if CACHE.exists() else {}
    keys = [_h(s, pr) for s, pr in jobs]; todo = {}
    for k, (s, pr) in zip(keys, jobs):
        if k not in cache and k not in todo: todo[k] = (s, pr)
    if todo:
        with ProcessPoolExecutor(8) as ex:
            for k, r in zip(todo, ex.map(_score_one, todo.values())): cache[k] = r
        tmp = CACHE.with_suffix('.tmp'); json.dump(cache, open(tmp, 'w'), ensure_ascii=False); os.replace(tmp, CACHE)
    return [cache[k] for k in keys]
def agg8(rs):
    """八套合计：rs 为 8 个集合的成绩。"""
    out = {}
    for m in ('主', '严'):
        e = sum(r[m]['错字'] for r in rs); n = sum(r[m]['参照字数'] for r in rs)
        mt = sum(round(r[m]['读出率'] * r[m]['参照字数'] / 100) for r in rs)
        out[m] = {'错字': e, '参照字数': n, '读出率': round(100 * mt / n, 3), '错字率': round(100 * e / n, 3)}
    return out
