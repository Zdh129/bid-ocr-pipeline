"""整合版优化（T-21）：盲测集 25 页计分公共函数（复制自 候选路线_v2/工具/盲测25页计分.py，只读参照与识别结果）。
必须用 离线跑.sh 运行，0 次付费调用。"""
from __future__ import annotations
import json, sys
from copy import deepcopy
from pathlib import Path
sys.dont_write_bytecode = True
H = Path(__file__).resolve().parents[1]
ROOT = Path('/workspace/pdf-test/baidu/output/盲测集_v1')
BAIDU = Path('/workspace/pdf-test/baidu')
ISO = ROOT / '运行_隔离'
SA_O = ISO / '运行/orig/work/output/scheme_a'   # 原图高精度
SA_P2 = ISO / '运行/P2/work/output/scheme_a'    # 颜色去章图高精度
REF_DIR = ROOT / '三方对照参照/参照草稿'
PRED_DIR = ROOT / '识别结果'
IMG_DIR = ROOT / '给ChatGPT'
UNC = '【?】'
SCORED = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 16, 18, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]
sys.path[:0] = [str(H.parent / '候选路线_v2' / '工具'), str(IMG_DIR), str(BAIDU / 'src'), str(BAIDU / 'scripts')]
import 后处理 as A
import phys_order as PO
import 比对 as CMP
import blind20_score as bs
import tolerant_score as T
清单 = json.loads((ROOT / '清单.json').read_text(encoding='utf-8'))
页表 = {p['盲测页号']: p for p in 清单['页']}

def post(pg, sa, logs=None, pid=None):
    """末端后处理：改动4 → 改动3 → 改动2（与候选路线_v2 相同）。sa：该页输出所用的 scheme_a 坐标目录。"""
    out = deepcopy(pg)
    out, lg = A.watermark(out)
    if lg and logs is not None: logs.append({'页': pid, '改动': 4})
    out, lg = A.invoice_cells(out, PO.load_units(sa, pid))
    if lg and logs is not None: logs.append({'页': pid, '改动': 3})
    out, lg = A.footer(out)
    if lg and logs is not None: logs.append({'页': pid, '改动': 2})
    return out

def draft_to_ref(pid):
    text = (REF_DIR / f'第{pid}页.txt').read_text(encoding='utf-8')
    lines, ref_lines = [], []
    for i, ln in enumerate(text.splitlines(), 1):
        if not ln.strip() and ln != UNC:
            continue
        kind = 'table' if '\t' in ln else 'body'
        lines.append((kind, ln)); ref_lines.append({'n': i, 'kind': kind, 'text': ln})
    rec = 页表[pid]
    return ({'lines': lines, 'seals': []},
            {'page': pid, 'doc_type': rec['版式'], 'ref_lines': ref_lines, 'blocks': []},
            {'doc_type': rec['版式'], 'output_page': pid})

_R = None
def score(pmap):
    """pmap：{'页号字符串': 页}；返回三口径成绩（含逐页）。"""
    global _R
    if _R is None:
        refs, pages, mp = {}, [], {}
        for pid in SCORED:
            r, pg, m = draft_to_ref(pid); refs[pid] = r; pages.append(pg); mp[pid] = m
        _R = (refs, {'set': '盲测集_v1_模型看图核对版', 'pages': pages}, mp)
    refs, rj, mp = _R
    pred = {pid: CMP.as_pred(pmap[str(pid)]) for pid in SCORED}
    return CMP.score_route(bs, T, refs, rj, mp, pred, SCORED)

def load(name):
    return json.loads((PRED_DIR / f'{name}.json').read_text(encoding='utf-8'))

def brief(sc):
    return {k: {kk: sc[k][kk] for kk in ('读出率', '错字率', '错字', '参照字数') if kk in sc[k]} for k in ('主', '严', '不计顺序')}
