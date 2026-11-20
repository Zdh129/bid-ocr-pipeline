"""第5轮整合路线（开发版）。步骤：
  S1 去章（方法1 的 0 调用版 1a）：第3轮冻结路线 frozen_stack3.py 的 sa_dir 换成「颜色去章图高精度」结果（流程里本来就有，不新增调用）
  S2 第3轮冻结路线（frozen_stack3.py，不改）
  S3 方法4 版式先验 layout_prior.apply_pred（字框：原图高精度缓存）
  S4 方法2 一致性校验 consistency_check.check_pred（另一路：去章图高精度物理行 → 基线C → 原图高精度物理行）
用法：route5.py C.json sa_orig sa_p2 pdf setname lines_dir doc renders reread out.json [combo]
combo：逗号分隔的步骤开关，如 1,4,2（默认 1,4,2）。不读参照、不读 manifest，不调用百度。"""
import json, os, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = Path('/workspace/pdf-test/baidu')
M = Path(os.environ.get('R5_MODS', str(HERE)))   # 冻结后指向冻结目录
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(M), str(ROOT / 'output/盲测_版面约束/冻结规则')]
PY = str(ROOT / '.venv/bin/python')
STACK3 = os.environ.get('R5_STACK3', str(ROOT / 'output/盲测_版面约束/版面分析优化计划/试算/第3轮/冻结/frozen_stack3.py'))
def main(C, sa_o, sa_p2, pdf, name, ld, doc, rd, rr, out, combo='1,4,2'):
    st = set(combo.split(','))
    sa = sa_p2 if '1' in st else sa_o
    tmp = Path(out).with_suffix('.r3.json')
    Path(ld, 'renders300').mkdir(parents=True, exist_ok=True); Path(ld, 'lines_cache').mkdir(parents=True, exist_ok=True)
    r = subprocess.run([PY, STACK3, C, sa, pdf, name, ld, doc, rd, rr, str(tmp)], capture_output=True, text=True, cwd=ROOT,
                       env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert r.returncode == 0, r.stderr[-2000:]
    pred = json.load(open(tmp))['pred']; logs = {}
    from baidu_ocr.hybrid.inputs import a_chars_for_page
    def summ(d): return {pg['page']: pg for pg in json.load(open(Path(d) / 'call_summary.json'))['pages']}
    so, sp = summ(sa_o), summ(sa_p2)
    if '4' in st:
        import layout_prior as LP
        ach = {int(p): a_chars_for_page(Path(sa_o), so[int(p)]) for p in pred if int(p) in so}
        pred, logs['方法4'] = LP.apply_pred(pred, achars_by_page=ach)
    if '2' in st:
        import consistency_check as CC
        from layout import phys_lines
        Cj = json.load(open(C))
        def at(d, s, p):
            if int(p) not in s: return ''
            return '\n'.join(''.join(c['ch'] for c in l) for l in phys_lines(a_chars_for_page(Path(d), s[int(p)])))
        alts = lambda p: [at(sa_p2, sp, p), '\n'.join(x['text'] for x in Cj[p]['sections']) if p in Cj else '', at(sa_o, so, p)]
        pred, logs['方法2'] = CC.check_pred(pred, alts)
    json.dump({'pred': pred, 'combo': combo, 'log': logs}, open(out, 'w'), ensure_ascii=False, indent=1, default=str)
if __name__ == '__main__':
    main(*sys.argv[1:])
