"""整合版优化（T-21）：盲测集 25 页计分公共函数（复制自 候选路线_v2/工具/盲测25页计分.py，只读参照与识别结果）。
必须用 离线跑.sh 运行，0 次付费调用。"""
from __future__ import annotations
import json, sys
from copy import deepcopy
from pathlib import Path
sys.dont_write_bytecode = True
H = Path(__file__).resolve().parents[1]
for _k in range(1, 9):
    _s = Path(__file__).resolve().parents[_k] / 'scripts'
    if (_s / 'ocr_lab_paths.py').exists():
        sys.path.insert(0, str(_s)); break
import ocr_lab_paths as LP
# 数据根与清单改为用到时再加载；代码依赖走仓库内路径。
UNC = '【?】'
SCORED = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 16, 18, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]
_CMP_DIR = None
def _v3():
    raw = __import__('os').environ.get('V3_ROOT', '').strip()
    return Path(raw) if raw else Path(__file__).resolve().parents[3]
def _cmp_dir():
    return _v3() / '工程/output/盲测集_v1/给ChatGPT'
sys.path[:0] = [str(H.parent / '候选路线_v2' / '工具'), str(_cmp_dir()), str(LP.repo_root() / 'src'), str(LP.repo_root() / 'scripts')]
import 后处理 as A
import phys_order as PO
import 比对 as CMP
import blind20_score as bs
import tolerant_score as T

def _data_root():
    r = LP.baidu_root(required=True) / 'output' / '盲测集_v1'
    return LP.require_dir(r, '盲测集_v1 数据目录', LP.ENV_BAIDU)

_清单 = None
_页表 = None

def __getattr__(name):
    global _清单, _页表
    if name == 'ROOT':
        return _data_root()
    if name == 'BAIDU':
        return LP.baidu_root(required=True)
    if name == 'ISO':
        return _data_root() / '运行_隔离'
    if name == 'SA_O':
        return _data_root() / '运行_隔离/运行/orig/work/output/scheme_a'
    if name == 'SA_P2':
        return _data_root() / '运行_隔离/运行/P2/work/output/scheme_a'
    if name == 'REF_DIR':
        return _data_root() / '三方对照参照/参照草稿'
    if name == 'PRED_DIR':
        return _data_root() / '识别结果'
    if name == 'IMG_DIR':
        return _cmp_dir()
    if name == '清单':
        if _清单 is None:
            _清单 = json.loads(LP.require_file(_data_root() / '清单.json', '盲测集清单 清单.json', LP.ENV_BAIDU).read_text(encoding='utf-8'))
        return _清单
    if name == '页表':
        if _页表 is None:
            _页表 = {p['盲测页号']: p for p in __getattr__('清单')['页']}
        return _页表
    raise AttributeError(name)

def _G(name):
    # 调用方（如 公共v3._set8、新扫描9页）会直接给本模块设置 REF_DIR、页表 等属性；已设置时用设置值（与原脚本相同），未设置时才按默认数据根延迟取值。
    g = globals()
    return g[name] if name in g else __getattr__(name)

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
    text = LP.require_file(_G('REF_DIR') / f'第{pid}页.txt', f'参照草稿 第{pid}页.txt', LP.ENV_BAIDU).read_text(encoding='utf-8')
    lines, ref_lines = [], []
    for i, ln in enumerate(text.splitlines(), 1):
        if not ln.strip() and ln != UNC:
            continue
        kind = 'table' if '\t' in ln else 'body'
        lines.append((kind, ln)); ref_lines.append({'n': i, 'kind': kind, 'text': ln})
    rec = _G('页表')[pid]
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
    f = LP.require_file(_data_root() / '识别结果' / f'{name}.json', f'识别结果 {name}.json', LP.ENV_BAIDU)
    return json.loads(f.read_text(encoding='utf-8'))

def brief(sc):
    return {k: {kk: sc[k][kk] for kk in ('读出率', '错字率', '错字', '参照字数') if kk in sc[k]} for k in ('主', '严', '不计顺序')}
