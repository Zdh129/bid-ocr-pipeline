"""表格结构评分（开发用，只计分不写 SCORED）：只看发票明细区（「项目名称」表头行 至「价税合计」之前）。
参照里「 | 」是栏界、换行是行界；预测里换行是行界、行内空白是栏界。按去空白后的字符对齐（difflib），统计：
  行完整：参照明细行（去空白）恰好等于预测某一行的比例
  行界 F1：预测换行与参照行界的位置吻合
  栏界 F1：预测所有空白分隔（含换行）与参照所有分隔（栏界+行界）的位置吻合"""
import re, unicodedata
from difflib import SequenceMatcher
from pathlib import Path
N = lambda s: unicodedata.normalize('NFKC', s)
def ref_pages(sdir):
    pages = {}; cur = None
    for ln in (Path(sdir) / 'blind-ref.txt').read_text(encoding='utf-8').splitlines():
        m = re.match(r'^=====第(\d+)页=====', ln.strip())
        if m: cur = str(int(m.group(1))); pages[cur] = []; continue
        if cur: pages[cur].append(ln)
    return pages
def ref_region(lines):
    i = next((k for k, l in enumerate(lines) if re.sub(r'\s', '', l).startswith('项目名称')), None)
    if i is None: return None
    j = next((k for k in range(i + 1, len(lines)) if re.sub(r'\s', '', lines[k]).startswith('价税合计')), None)
    if j is None: return None
    return [[c.strip() for c in l.split('|')] for l in lines[i:j] if l.strip()]
def pred_region(page):
    for s in page['sections']:
        t = s['text']; hs = t.find('项目名称')
        if hs < 0: continue
        m = re.search(r'价\s*税\s*合\s*计', t[hs:])
        if not m: continue
        hs = t.rfind('\n', 0, hs) + 1
        return [l for l in t[hs:hs + m.start()].split('\n') if l.strip()]
    return None
def seq(rows_cells):
    """rows_cells: [[cell,...],...] → (去空白串, {位置: 'R'/'C'})；位置 = 该分隔前面的字数。"""
    s = ''; b = {}
    for r in rows_cells:
        for k, c in enumerate(r):
            c = re.sub(r'\s', '', N(c))
            if not c: continue
            if s and len(s) not in b: b[len(s)] = 'C'
            s += c
        if s: b[len(s)] = 'R'
    b.pop(len(s), None); return s, b
def f1(tp, np_, nr): p = tp / np_ if np_ else 1.0; r = tp / nr if nr else 1.0; return round(2 * p * r / (p + r), 3) if p + r else 0.0
def score_page(ref_lines, page):
    rr = ref_region(ref_lines); pr = pred_region(page)
    if not rr or pr is None: return None
    rs, rb = seq(rr)
    ps, pb = seq([re.split(r'\s+', l.strip()) for l in pr])
    # 预测分隔映射到参照位置
    m = {}
    for a, b2, n in SequenceMatcher(None, ps, rs, autojunk=False).get_matching_blocks():
        for t in range(n + 1): m[a + t] = b2 + t
    pbm = {m[k]: v for k, v in pb.items() if k in m}
    rowR = {k for k, v in rb.items() if v == 'R'}; rowP = {k for k, v in pbm.items() if v == 'R'}
    allR, allP = set(rb), set(pbm)
    refrows = [re.sub(r'\s', '', N(''.join(r))) for r in rr]; predrows = {re.sub(r'\s', '', N(l)) for l in pr}
    return {'参照行': len(rr), '预测行': len(pr), '行完整': sum(r in predrows for r in refrows),
            '行界F1': f1(len(rowR & rowP), len(rowP), len(rowR)), '栏界F1': f1(len(allR & allP), len(allP), len(allR)),
            '_tp': (len(rowR & rowP), len(rowP), len(rowR), len(allR & allP), len(allP), len(allR))}
def score_set(sdir, pred):
    R = ref_pages(sdir); out = {}; T = [0] * 6; rows = [0, 0]
    for p in sorted(pred, key=int):
        if p not in R: continue
        r = score_page(R[p], pred[p])
        if not r: continue
        out[p] = {k: v for k, v in r.items() if k != '_tp'}
        T = [a + b for a, b in zip(T, r['_tp'])]; rows[0] += r['行完整']; rows[1] += r['参照行']
    tot = {'行完整': f'{rows[0]}/{rows[1]}', '行界F1': f1(T[0], T[1], T[2]), '栏界F1': f1(T[3], T[4], T[5])}
    return tot, out
