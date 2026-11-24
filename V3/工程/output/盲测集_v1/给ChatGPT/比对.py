#!/usr/bin/env python3
"""以 ChatGPT 转写为参照，离线计算第六轮新版、整合版、分流版的主／严／不计顺序成绩。

不改原件、冻结目录和既有参照；不调用百度或其他付费接口。
某条路线若还没有该页的识别结果，输出「需实跑」，不编数字。
分流版对 ①′ 页可当场从文字层取字（去掉看不见的字），这不算实跑识别。
分流版对其余类别：有现成的第六轮／整合版结果时按规则复用，否则标需实跑。
"""
from __future__ import annotations
import warnings
warnings.filterwarnings("ignore", message="The `fitz` API is deprecated")

import json, re, sys, unicodedata
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _k in range(1, 9):
    _s = Path(__file__).resolve().parents[_k] / 'scripts'
    if (_s / 'ocr_lab_paths.py').exists():
        sys.path.insert(0, str(_s)); break
import ocr_lab_paths as LP
sys.path[:0] = [str(LP.repo_root() / 'src'), str(LP.repo_root() / 'scripts')]
sys.dont_write_bytecode = True

_清单 = _图片清单 = _页表 = _图名 = _名到页 = None
_ROOT = None

def _data_root():
    global _ROOT
    if _ROOT is None:
        r = LP.baidu_root(required=True) / 'output' / '盲测集_v1'
        _ROOT = LP.require_dir(r, '盲测集_v1 数据目录', LP.ENV_BAIDU)
    return _ROOT

def _ensure_lists():
    global _清单, _图片清单, _页表, _图名, _名到页
    if _清单 is not None:
        return
    root = _data_root()
    _清单 = LP.load_json(root / '清单.json', '盲测集清单 清单.json', LP.ENV_BAIDU)
    imgf = HERE / '图片清单.json'
    if not imgf.is_file():
        imgf = root / '给ChatGPT' / '图片清单.json'
    _图片清单 = LP.load_json(imgf, '图片清单.json', LP.ENV_BAIDU)
    _页表 = {p['盲测页号']: p for p in _清单['页']}
    _图名 = {p['盲测页号']: p['图片文件名'] for p in _图片清单['页']}
    _名到页 = {p['图片文件名']: p['盲测页号'] for p in _图片清单['页']}
    _名到页.update({Path(n).stem: i for n, i in {p['图片文件名']: p['盲测页号'] for p in _图片清单['页']}.items()})

def __getattr__(name):
    if name == 'ROOT':
        return _data_root()
    if name == 'BAIDU':
        return LP.baidu_root(required=True)
    if name in ('清单', '图片清单', '页表', '图名', '名到页'):
        _ensure_lists()
        return {'清单': _清单, '图片清单': _图片清单, '页表': _页表, '图名': _图名, '名到页': _名到页}[name]
    raise AttributeError(name)

def _G(name):
    return __getattr__(name)

def _结果目录候选():
    return [HERE / 'ChatGPT结果', _data_root() / 'ChatGPT结果', Path.cwd() / 'ChatGPT结果']
def _识别目录候选():
    return [_data_root() / '识别结果', HERE / '识别结果']

SEAL_RE = re.compile(r'【印章[:：](.*?)】')
FENCE_RE = re.compile(r'^```(?:\w+)?\s*$')
HEAD_RE = re.compile(r'^(?:=====)?\s*([0-9]{2}_[^\s]+?\.(?:png|jpg|jpeg|webp|txt))?\s*(?:=====)?$', re.I)


def find_dir(cands):
    for d in cands:
        if d.is_dir():
            return d
    return cands[0]


def is_empty_ref(g: dict) -> bool:
    """只有 [?] / 【?】、或没有任何正文和印章，视为无效参照。"""
    seals = [s for s in (g.get('seals') or []) if str(s).strip()]
    lines = []
    for ln in (g.get('lines') or []):
        s = (ln or '').strip()
        s2 = s.replace('[?]', '').replace('[？]', '').replace('【?】', '').replace('【？】', '').strip()
        if s2:
            lines.append(s)
    return not lines and not seals


def strip_unc(s: str) -> str:
    """把 [?] 收成官方参照里的 【?】，计分时会被规范函数去掉。"""
    return (s or '').replace('[?]', '【?】').replace('[？]', '【?】')


def parse_page_text(raw: str, expect_name: str | None = None) -> dict:
    text = raw.encode('utf-8').decode('utf-8-sig') if isinstance(raw, str) else raw
    lines = text.splitlines()
    # drop wrapping fences
    if lines and FENCE_RE.match(lines[0].strip()):
        lines = lines[1:]
        if lines and FENCE_RE.match(lines[-1].strip()):
            lines = lines[:-1]
    # drop first-line filename
    if lines:
        first = lines[0].strip().strip('`').strip()
        first = first[5:-5] if first.startswith('=====') and first.endswith('=====') else first
        stems = set()
        if expect_name:
            stems.update({expect_name, Path(expect_name).stem, Path(expect_name).name})
        if first in stems or first in _G("名到页") or Path(first).stem in _G("名到页"):
            lines = lines[1:]
    body, seals = [], []
    for ln in lines:
        s = ln.replace('\u3000', ' ')
        found = SEAL_RE.findall(s)
        seals.extend(x.strip() for x in found if x.strip())
        rest = SEAL_RE.sub('', s)
        # 旧格式：【印章】内容
        m = re.match(r'^【印章】\s*(.*)$', rest.strip())
        if m:
            if m.group(1).strip():
                seals.append(m.group(1).strip())
            continue
        if rest.strip():
            body.append(rest.rstrip())
    return {'lines': body, 'seals': seals}


def split_combined(raw: str) -> dict[int, str]:
    """从一份多页 txt 里按代码块或 =====文件名===== 切开。"""
    text = raw.encode('utf-8').decode('utf-8-sig')
    out: dict[int, list[str]] = {}
    cur = None
    buf: list[str] = []

    def flush():
        nonlocal cur, buf
        if cur is not None:
            out[cur] = buf
        cur, buf = None, []

    for ln in text.splitlines():
        t = ln.strip().strip('`')
        if FENCE_RE.match(ln.strip()):
            # new fence: if we already have a page, stay; filename comes next
            continue
        name = None
        if t.startswith('=====') and t.endswith('====='):
            name = t.strip('=').strip()
        elif t in _G("名到页") or Path(t).stem in _G("名到页") or t in {_G("图名")[i] for i in _G("图名")}:
            name = t
        if name:
            key = name if name in _G("名到页") else (name if name in _G("图名").values() else Path(name).stem)
            pid = _G("名到页").get(key) or _G("名到页").get(Path(key).stem)
            if pid:
                flush()
                cur = pid
                continue
        if cur is not None:
            buf.append(ln)
    flush()
    return {k: '\n'.join(v) for k, v in out.items()}


def load_chatgpt():
    d = find_dir(_结果目录候选())
    got = {}
    sources = {}
    if not d.exists():
        return got, sources, d
    # first pass: combined files
    for f in sorted(d.glob('*.txt')) + sorted(d.glob('*.md')):
        if f.name.startswith('把转写结果') or f.name.startswith('.'):
            continue
        raw = f.read_text(encoding='utf-8', errors='replace')
        parts = split_combined(raw)
        if len(parts) >= 2 or (len(parts) == 1 and Path(f.stem) not in [Path(_G("图名")[i]).stem for i in parts]):
            for pid, body in parts.items():
                if pid not in got:
                    got[pid] = parse_page_text(body, _G("图名")[pid])
                    sources[pid] = str(f)
    # second pass: 同名.txt takes priority
    for rec in _G("图片清单")['页']:
        pid, fn = rec['盲测页号'], rec['图片文件名']
        stem = Path(fn).stem
        for cand in (d / f'{stem}.txt', d / f'{fn}.txt', d / f'{stem}.md'):
            if cand.exists():
                got[pid] = parse_page_text(cand.read_text(encoding='utf-8', errors='replace'), fn)
                sources[pid] = str(cand)
                break
    return got, sources, d


def load_json_preds(stem: str):
    names = [stem + '.json', stem.replace('新版', '') + '.json']
    for folder in _识别目录候选():
        if not folder.exists():
            continue
        for n in names:
            p = folder / n
            if p.exists():
                data = json.load(open(p))
                # allow {"页": {...}} or flat { "1": {...} } or list
                if isinstance(data, dict) and '页' in data and isinstance(data['页'], dict):
                    data = data['页']
                out = {}
                if isinstance(data, dict):
                    for k, v in data.items():
                        if str(k).isdigit():
                            out[int(k)] = v
                return out, str(p)
    return {}, None


def native_visible(pdf, q):
    """①′：去掉看不见的字后再按杨工逻辑取文字层。源文件不改、不保存。"""
    import pymupdf
    sys.path.insert(0, str(Path('/workspace/pdf-test/baidu/output/分流整合版/工具')))
    import 杨工脚本副本 as Y

    def invisible(sp):
        if sp.get('type') == 3:
            return True
        if (sp.get('opacity', 1) or 0) <= 0.01:
            return True
        c = sp.get('color') or (0, 0, 0)
        if all(x >= 0.97 for x in c[:3]):
            return True
        return (sp.get('size') or 10) < 3

    doc = pymupdf.open(pdf)
    pg = doc[q - 1]
    n = 0
    for sp in pg.get_texttrace():
        if invisible(sp):
            for ch in sp.get('chars', ()):
                r = pymupdf.Rect(ch[3])
                r = r & pg.rect
                if not r.is_empty:
                    cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
                    w, h = (r.x1 - r.x0) * 0.3, (r.y1 - r.y0) * 0.3
                    pg.add_redact_annot(pymupdf.Rect(cx - w, cy - h, cx + w, cy + h), fill=False)
                    n += 1
    if n:
        pg.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    t, use_ocr = Y._pdf_native_input(pg)
    doc.close()
    return (t if t and not use_ocr else ''), n


def as_pred(obj) -> dict:
    if not obj:
        return None
    if isinstance(obj, str):
        return {'sections': [{'section': 'body', 'text': obj}], 'seal': ''}
    if 'sections' in obj:
        return {'sections': obj['sections'], 'seal': obj.get('seal') or ''}
    if 'text' in obj:
        return {'sections': [{'section': 'body', 'text': obj['text']}], 'seal': obj.get('seal') or ''}
    return None


def collect_preds():
    sixth, sixth_src = load_json_preds('第六轮新版')
    if not sixth:
        sixth, sixth_src = load_json_preds('第六轮')
    integ, integ_src = load_json_preds('整合版')
    shunt, shunt_src = load_json_preds('分流版')
    out = {'第六轮新版': {}, '整合版': {}, '分流版': {}}
    src = {'第六轮新版': sixth_src, '整合版': integ_src, '分流版': shunt_src}
    note = {'第六轮新版': {}, '整合版': {}, '分流版': {}}
    for rec in _G("清单")['页']:
        pid = rec['盲测页号']
        cat = rec['分流类别']
        if as_pred(sixth.get(pid) or sixth.get(str(pid))):
            out['第六轮新版'][pid] = as_pred(sixth.get(pid) or sixth.get(str(pid)))
            note['第六轮新版'][pid] = '识别结果文件'
        if as_pred(integ.get(pid) or integ.get(str(pid))):
            out['整合版'][pid] = as_pred(integ.get(pid) or integ.get(str(pid)))
            note['整合版'][pid] = '识别结果文件'
        if as_pred(shunt.get(pid) or shunt.get(str(pid))):
            out['分流版'][pid] = as_pred(shunt.get(pid) or shunt.get(str(pid)))
            note['分流版'][pid] = '识别结果文件'
            continue
        # 按分流规则补：①′ 当场取文字层；④/③ 复用第六轮；④′/②′ 复用整合版
        if cat == '①′':
            pdf = Path(rec['原件绝对路径'])
            t, n = native_visible(pdf, rec['源页'])
            out['分流版'][pid] = {'sections': [{'section': 'body', 'text': t}], 'seal': ''}
            note['分流版'][pid] = f'①′文字层直取去不可见（去掉不可见字 {n} 处）'
        elif cat in ('④', '③') and pid in out['第六轮新版']:
            out['分流版'][pid] = out['第六轮新版'][pid]
            note['分流版'][pid] = f'按规则走第六轮新版（{cat}）'
        elif cat in ('④′', '②′') and pid in out['整合版']:
            out['分流版'][pid] = out['整合版'][pid]
            note['分流版'][pid] = f'按规则走整合版（{cat}）'
    return out, src, note


def to_official_ref(chatgpt, pids):
    import blind20_score as bs
    refs, rj_pages, mp = {}, [], {}
    for pid in pids:
        rec = _G("页表")[pid]
        g = chatgpt[pid]
        lines = []
        ref_lines = []
        for i, ln in enumerate(g['lines'], 1):
            t = strip_unc(ln)
            kind = 'table' if '\t' in ln else 'body'
            lines.append((kind, t))
            ref_lines.append({'n': i, 'kind': kind, 'text': t})
        seals = [strip_unc(s) for s in g['seals']]
        refs[pid] = {'lines': lines, 'seals': seals}
        rj_pages.append({'page': pid, 'doc_type': rec['版式'], 'ref_lines': ref_lines, 'blocks': []})
        mp[pid] = {'doc_type': rec['版式'], 'output_page': pid}
    # 空页也要有结构，否则 scorer 可能跳过
    return refs, {'set': '盲测集_v1_ChatGPT', 'pages': rj_pages}, mp


def unordered(bs, refs, pred, pids):
    tot = nref = 0
    per = {}
    for p in pids:
        rs, _ = bs.ref_chars(refs[p])
        pp = pred.get(p) or pred.get(str(p)) or {'sections': []}
        ps = bs.norm(''.join(s['text'] for s in pp['sections']))
        a, b = Counter(rs), Counter(ps)
        e = max(sum((a - b).values()), sum((b - a).values()))
        per[p] = {'错字': e, '参照字数': len(rs)}
        tot += e
        nref += len(rs)
    return {'错字': tot, '参照字数': nref, '错字率': round(100 * tot / nref, 2) if nref else None}, per


def fmt(n, cer, err, refn):
    if refn is None:
        return '需实跑'
    return f'{n:.2f}%／{cer:.2f}%（{err}/{refn}）'


def score_route(bs, T, refs, rj, mp, pred, pids):
    pr = {str(p): pred[p] for p in pids}
    # 严
    st = bs.score_system(pr, {p: refs[p] for p in pids}, mp, None)
    # 主
    tol = T.score_tolerant(bs, pr, {'set': rj['set'], 'pages': [pg for pg in rj['pages'] if pg['page'] in pids]}, mp, 'in')
    K = ('ref_chars', 'matches', 'deletions', 'substitutions', 'insertions')
    E = lambda o: o['deletions'] + o['substitutions'] + o['insertions']

    def agg(sc):
        n = sum(sc['per_page'][p]['overall']['ref_chars'] for p in pids)
        m = sum(sc['per_page'][p]['overall']['matches'] for p in pids)
        e = sum(E(sc['per_page'][p]['overall']) for p in pids)
        return {
            '读出率': round(100 * m / n, 2) if n else None,
            '错字率': round(100 * e / n, 2) if n else None,
            '错字': e,
            '匹配': m,
            '参照字数': n,
            '逐页': {p: {'错字': E(sc['per_page'][p]['overall']), '参照字数': sc['per_page'][p]['overall']['ref_chars'],
                        '读出率': sc['per_page'][p]['overall'].get('readout'),
                        '错字率': sc['per_page'][p]['overall'].get('cer')} for p in pids},
        }

    uo, uop = unordered(bs, refs, pred, pids)
    return {'主': agg(tol), '严': agg(st), '不计顺序': {**uo, '逐页': uop}}


def main():
    import blind20_score as bs
    import tolerant_score as T

    chatgpt, chat_src, chat_dir = load_chatgpt()
    preds, pred_src, pred_note = collect_preds()
    all_ids = [p['盲测页号'] for p in _G("清单")['页']]
    missing_ref = [i for i in all_ids if i not in chatgpt or is_empty_ref(chatgpt[i])]

    lines = []
    def w(s=''):
        lines.append(s)
        print(s)

    w('盲测集_v1 比对（ChatGPT 转写作参照）')
    w('离线运行，付费调用 0 次。主口径＝tolerant_score（看顺序），严口径＝score_system，不计顺序＝逐页按字多重集。')
    w(f'ChatGPT 结果目录：{chat_dir}')
    w(f'已读到转写：{len(chatgpt)} / 42 页')
    if missing_ref:
        w('尚未放入的参照页：' + '、'.join(f'{i:02d}({_G("图名")[i]})' for i in missing_ref))
        w('请把转写另存为 ChatGPT结果/<同名>.txt 后再跑。')
    w('')

    report = {'参照页数': len(chatgpt), '缺少参照': missing_ref, '路线': {}}
    for route in ('第六轮新版', '整合版', '分流版'):
        have = [i for i in all_ids if i in chatgpt and i in preds[route] and not is_empty_ref(chatgpt[i])]
        need = [i for i in all_ids if i not in preds[route]]
        wait = [i for i in all_ids if i in preds[route] and (i not in chatgpt or is_empty_ref(chatgpt[i]))]
        w(f'【{route}】')
        if pred_src[route]:
            w(f'  识别结果文件：{pred_src[route]}')
        ready = len([i for i in all_ids if i in preds[route]])
        if ready:
            w(f'  已有输出 {ready} 页' + (f'（其中 {len(wait)} 页还缺 ChatGPT 参照）' if wait else ''))
        if need:
            w(f'  需实跑：{len(need)} 页均无识别结果，不编数字' + ('：' + '、'.join(f'{i:02d}' for i in need) if len(need) <= 42 else ''))
        if not have:
            if need and not ready:
                w('  本路线目前无法计分。')
            elif wait:
                w('  已有输出的页还缺 ChatGPT 参照，暂不计分。')
            report['路线'][route] = {'状态': '需实跑' if need and not ready else '待参照', '已计分页': [], '需实跑页': need, '待参照页': wait, '成绩': None}
            w('')
            continue
        refs, rj, mp = to_official_ref(chatgpt, have)
        sc = score_route(bs, T, refs, rj, mp, preds[route], have)
        a, b, c = sc['主'], sc['严'], sc['不计顺序']
        w(f'  已计分 {len(have)} 页' + (f'，另有 {len(need)} 页需实跑' if need else ''))
        w(f'  主：{fmt(a["读出率"], a["错字率"], a["错字"], a["参照字数"])}')
        w(f'  严：{fmt(b["读出率"], b["错字率"], b["错字"], b["参照字数"])}')
        w(f'  不计顺序：{c["错字率"]:.2f}%（{c["错字"]}/{c["参照字数"]}）' if c['参照字数'] else '  不计顺序：—')
        if need:
            w('  需实跑页：' + '、'.join(f'{i:02d}' for i in need) + '（这几页没有计入上面的成绩）')
        # 逐页一行
        for i in have:
            src = pred_note[route].get(i, '')
            w(f"    第{i:02d}页 {_G("图名")[i]}  主错{sc['主']['逐页'][i]['错字']} 严错{sc['严']['逐页'][i]['错字']} 序错{sc['不计顺序']['逐页'][i]['错字']}  参照{sc['主']['逐页'][i]['参照字数']}字  {src}")
        report['路线'][route] = {
            '状态': '部分计分' if need else '已计分',
            '已计分页': have,
            '需实跑页': need,
            '成绩来源说明': {str(i): pred_note[route].get(i) for i in have},
            '成绩': {'主': {k: a[k] for k in ('读出率', '错字率', '错字', '参照字数')},
                    '严': {k: b[k] for k in ('读出率', '错字率', '错字', '参照字数')},
                    '不计顺序': {k: c[k] for k in ('错字率', '错字', '参照字数')}},
            '逐页': {str(i): {'主': sc['主']['逐页'][i], '严': sc['严']['逐页'][i], '不计顺序': sc['不计顺序']['逐页'][i]} for i in have},
        }
        w('')

    w('识别结果放置位置（有结果后再跑同一条命令即可）：')
    w(f'  {_G("ROOT") / "识别结果" / "第六轮新版.json"}')
    w(f'  {_G("ROOT") / "识别结果" / "整合版.json"}')
    w(f'  {_G("ROOT") / "识别结果" / "分流版.json"}（可选；缺省时①′当场取文字层，④复用第六轮，④′/②′复用整合版）')
    w('  文件格式：{"1": {"sections": [{"section": "body", "text": "…"}], "seal": ""}, …}，键为盲测页号。')
    w('参照来源一律视为「ChatGPT看图（未人工核对）」。')

    outp = HERE / '比对成绩.json'
    json.dump(report, open(outp, 'w'), ensure_ascii=False, indent=2)
    (HERE / '比对成绩_中文.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    w(f'已写入 {outp} 和 {HERE / "比对成绩_中文.md"}')


if __name__ == '__main__':
    main()
