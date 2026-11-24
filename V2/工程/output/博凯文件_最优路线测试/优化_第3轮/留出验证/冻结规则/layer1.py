"""第一层：表格「多条明细被并进一个单元格行（按列合并）」检测与按物理行重排。只用缓存：文档解析表格框＋高精度行字框；不读参照，不调接口。
检测（通用，不看文档类型）：文档解析表格的某一行里，至少 3 个单元格各含 ≥2 条纵向互不重叠的高精度文字行，且这些单元格行数相同、不是「名称：…」这类键值块（购买方/销售方并排块不触发）
  → 判为多条明细被并成一行（单纯一格折行只有 1 格多行，不触发）。
只改「仍未被前面步骤修好」的行（预测里对应那一截的行数若已不少于记录数＝多行单元格的共同行数，且最长一行占比不超过 1/记录数＋0.25，视为已拆好，不动）：该行按文档解析原样拼成的文字（去空白）必须原封不动地出现在当前预测的表格段里（前面的乙′/方法6 已经拆过的行不会再匹配，不动）。
重排：把落在该行框内的高精度文字行按纵向区间重叠（≥70% 较矮行高）归成物理行，行内按 x 排序、空格连接，替换预测段里对应那一截文字。
安全闸：重排结果与原行文字的字符多重集合重合度 ≥0.95 才换（防止高精度行漏字、重复）。"""
import json, re
from collections import Counter
from pathlib import Path
PX = 200 / 72.0
OVL = float(__import__('os').environ.get('L1_OVL', '0.7'))   # 同一物理行的纵向重叠比例门槛
MINC = int(__import__("os").environ.get("L1_MINC", "3"))   # 至少 3 个单元格同为多行（多条记录并行的各个字段列）；带「：」的「名称：…」式字段块不计
nz = lambda s: re.sub(r'\s', '', s or '')
def a_lines(sa, page):
    raw = Path(sa) / 'raw'
    for k in ('main_accurate', 'main_general'):
        f = raw / f'p{page:02d}_{k}.json'
        if f.exists():
            out = []
            for w in json.load(open(f)).get('words_result', []):
                L = w['location']; out.append({'t': w['words'], 'x0': L['left'], 'y0': L['top'], 'x1': L['left'] + L['width'], 'y1': L['top'] + L['height']})
            return out
    return []
def inbox(l, b, pad=2):
    cx, cy = (l['x0'] + l['x1']) / 2, (l['y0'] + l['y1']) / 2
    return b[0] * PX - pad <= cx <= b[2] * PX + pad and b[1] * PX - pad <= cy <= b[3] * PX + pad
def phys_rows(ls):
    """物理行：两条文字行的纵向区间重叠 ≥ OVL×较矮者高度，才算同一物理行（比按中心距离更不怕轻微倾斜，也能把折行单元格的上一行分开）。"""
    rows = []
    for l in sorted(ls, key=lambda l: (l['y0'] + l['y1']) / 2):
        for r in rows:
            ov = min(l['y1'], r['y1']) - max(l['y0'], r['y0'])
            if ov >= OVL * min(l['y1'] - l['y0'], r['y1'] - r['y0']):
                r['ls'].append(l); r['y0'] = min(r['y0'], l['y0']) if False else r['y0']; break
        else: rows.append({'y0': l['y0'], 'y1': l['y1'], 'ls': [l]})
    rows.sort(key=lambda r: (r['y0'] + r['y1']) / 2)
    return [' '.join(x['t'] for x in sorted(r['ls'], key=lambda l: l['x0'])) for r in rows]
def nlines(ls):
    ys = sorted(((l['y0'] + l['y1']) / 2, l['y1'] - l['y0']) for l in ls); n, last = 0, None
    for cy, h in ys:
        if last is None or cy - last > 0.6 * h: n += 1; last = cy
    return n
def overlap(a, b):
    A, B = Counter(nz(a)), Counter(nz(b)); return sum((A & B).values()) / max(1, max(sum(A.values()), sum(B.values())))
def find_span(text, target):
    """在 text 里找去空白后等于 target 的一截，返回原文起止下标。"""
    idx = [i for i, ch in enumerate(text) if not ch.isspace()]; flat = ''.join(text[i] for i in idx); k = flat.find(target)
    if k < 0 or not target: return None
    return idx[k], idx[k + len(target) - 1] + 1
def apply(pred, doc_path, sa, row_gate=0.95):
    doc = json.load(open(doc_path)); pred = json.loads(json.dumps(pred)); log = []
    for p, pg in pred.items():
        tabs = [b for b in doc['blocks'] if b['page'] == int(p) and b['type'] == 'table' and b.get('table')]
        if not tabs or not any(s.get('section') == 'table' for s in pg['sections']): continue
        AL = a_lines(sa, int(p))
        for tb in tabs:
            cells = tb['table']['cells']
            for r in sorted({c['row'] for c in cells}):
                rc = sorted([c for c in cells if c['row'] == r], key=lambda c: c['col'])
                cl = [[l for l in AL if inbox(l, c['bbox'])] for c in rc]; cnt = [nlines(x) for x in cl]
                multi = Counter(n for n, x in zip(cnt, cl) if n >= 2 and sum('：' in l['t'] or ':' in l['t'] for l in x) <= len(x) / 2)
                if not multi or max(multi.values()) < MINC: continue
                orig = nz(''.join(c['text'] or '' for c in rc))
                hit = [(i, find_span(s['text'], orig)) for i, s in enumerate(pg['sections']) if s.get('section') == 'table']
                hit = [(i, sp) for i, sp in hit if sp]
                rb = [min(c['bbox'][0] for c in rc), min(c['bbox'][1] for c in rc), max(c['bbox'][2] for c in rc), max(c['bbox'][3] for c in rc)]
                nr = '\n'.join(phys_rows([l for l in AL if inbox(l, rb)])); ov = overlap(orig, nr)
                if ov < row_gate: log.append({'页': p, '行': r, '未改_闸门': round(ov, 3)}); continue
                if hit:
                    i, (a, b) = hit[0]; t = pg['sections'][i]['text']
                    pg['sections'][i] = {**pg['sections'][i], 'text': t[:a] + nr + t[b:], 'layout_role': '表格按物理行重排'}
                    log.append({'页': p, '行': r, '重排': '原位替换', '重合度': round(ov, 3)}); continue
                # 原样文字不在预测里：若预测里本行文字已按记录拆好（重排结果的每个物理行都已原样出现在预测里）→不动；否则在表格段里找与本行多重集合最像的连续若干行整体替换
                tsecs = [i for i, s in enumerate(pg['sections']) if s.get('section') == 'table']
                if all(any(nz(x) in nz(pg['sections'][i]['text']) for i in tsecs) for x in nr.split('\n') if nz(x)):
                    log.append({'页': p, '行': r, '未改': '预测里已按物理行拆好'}); continue
                best = None
                for i in tsecs:
                    L = pg['sections'][i]['text'].split('\n')
                    for a in range(len(L)):
                        for b in range(a + 1, min(len(L), a + 12) + 1):
                            o = overlap('\n'.join(L[a:b]), orig)
                            if best is None or o > best[0]: best = (o, i, a, b)
                if not best or best[0] < row_gate: log.append({'页': p, '行': r, '未改': '预测里找不到对应行', '最佳重合': best and round(best[0], 3)}); continue
                o, i, a, b = best; L = pg['sections'][i]['text'].split('\n')
                nrec = max(n for n, k in multi.items() if k >= MINC)
                W = [len(nz(x)) for x in L[a:b]]
                if (b - a) >= nrec and max(W) <= (1 / nrec + 0.25) * sum(W):
                    log.append({'页': p, '行': r, '未改': '预测里这一截的行数已不少于记录数，且没有一行独占，视为已拆好', '已有行数': b - a, '记录数': nrec, '最长行占比': round(max(W) / sum(W), 2)}); continue
                # 窗口里有、重排结果里没有的字（别的字段混进来的，如「税务机关」）不丢：按原顺序取出连续 ≥2 字的片段另起一行保留
                left = Counter(nz('\n'.join(L[a:b]))) - Counter(nz(nr)); keep, cur = [], ''
                for ch in nz('\n'.join(L[a:b])):
                    if left[ch] > 0: left[ch] -= 1; cur += ch
                    else:
                        if len(cur) >= 2: keep.append(cur)
                        cur = ''
                if len(cur) >= 2: keep.append(cur)
                pg['sections'][i] = {**pg['sections'][i], 'text': '\n'.join(L[:a] + [nr] + keep + L[b:]), 'layout_role': '表格按物理行重排'}
                log.append({'页': p, '行': r, '重排': '按多重集合定位替换', '重合度': round(ov, 3), '定位重合': round(o, 3)})
    return pred, log
