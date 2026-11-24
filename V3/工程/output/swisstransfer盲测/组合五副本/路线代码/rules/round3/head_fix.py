"""第3轮（试算）F5 页首行坐标复位：发票页上第一个表格段之前的正文段里，若某段排在「购买方：/销售方：」开头的正文段前面，
但它在 A（高精度含位置）里的 y 比这些后面的购买方/销售方段都低 ≥0.5 字高，就把它挪到 y 在它之上的最后一个购买方/销售方段后面。
只调换整段顺序，不改字；每段要在 A 物理行里找到 ≥80% 字的连续匹配才参与；任一购买方/销售方段定位不到就不动。不看参照，不按页码/文件名。
来由：方案E 的发票表头重排把「所属发票代码/发票号码/开票日期」提到页首，对清单页（购买方/销售方是正文行、表头字段在它们下面）是错的。"""
import re, unicodedata
from difflib import SequenceMatcher
from layout import phys_lines, cy, ch_h
BS = re.compile(r'^(购买方|销售方)[:：]')
def N(s): return ''.join(ch for ch in unicodedata.normalize('NFKC', s or '') if not ch.isspace())
def locate_y(t, pls):
    best = None
    for l in pls:
        L = N(''.join(c['ch'] for c in l))
        m = SequenceMatcher(None, t, L, autojunk=False).find_longest_match(0, len(t), 0, len(L))
        if m.size >= max(4, 0.8 * len(t)) and (best is None or m.size > best[0]): best = (m.size, sum(cy(c) for c in l) / len(l))
    return best[1] if best else None
def fix_page(secs, achars, log):
    """secs：该页 sections（list）；返回新 sections 或 None。"""
    if not achars: return None
    ft = next((i for i, x in enumerate(secs) if x['section'] == 'table'), len(secs))
    lead = list(range(ft))
    if any(secs[i]['section'] == 'table' for i in lead): return None
    bs = [i for i in lead if BS.match(N(secs[i]['text']))]
    if not bs: return None
    pls = phys_lines(achars); h = ch_h(achars)
    ys = {i: locate_y(N(secs[i]['text']), pls) for i in lead if N(secs[i]['text'])}
    if any(ys.get(i) is None for i in bs): return None
    order = list(lead); moved = []
    for i in lead:
        if i in bs or ys.get(i) is None: continue
        later = [b for b in bs if order.index(b) > order.index(i)]
        if not later or not all(ys[i] > ys[b] + 0.5 * h for b in later): continue
        above = [b for b in bs if ys[b] < ys[i]]
        tgt = max(above, key=lambda b: order.index(b))
        order.remove(i); order.insert(order.index(tgt) + 1, i); moved.append(i)
    if not moved: return None
    log.append({'挪动段': moved, '新顺序': order})
    return [secs[i] for i in order] + secs[ft:]
