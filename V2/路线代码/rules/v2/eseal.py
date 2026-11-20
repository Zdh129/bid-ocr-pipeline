"""电子印章字样与章下验证码清理（候选第二版，开关 eseal；只删字，不加字，不读参照）。
依据（全部来自本页已有缓存，通用特征，不看文件名/页码/文档类型）：
  1) 印章位置：scheme_a 的 call_summary 里按红色像素检出的印章框 route.seal_boxes（颜色检测）；
     框向左右各扩 10%、向上扩 5%、向下扩 25% 框高（容纳章下验证码）。
  2) 章内文字：原图高精度 main 字框中心落在扩展框内的文字行。只取两类：
     K 印章关键词行：0–12 个汉字前缀＋「专用章/公章/用章/监制章」结尾，且不含「盖、（、(、：、:」；
     C 验证码：纯大写字母和数字、8–20 位、字母和数字各 ≥2 个。
     同一串若也出现在本页印章框以外的高精度文字里（例如正文里印着的号码），就不算印章字，不动。
     符合统一社会信用代码格式（GB 32100，18 位）的串不算验证码：发票专用章里按规定刻有销售方识别号，正文里同一号码是真字段。
  3) 参照口径：八套参照把印章字单独记成页末【印章：…】，不计入正文；博凯、江苏参照取自文字层，印章字根本不在参照里。
     所以「印章字不应出现在正文段里」在三套口径下一致；本规则只从正文段删，不改 seal 字段。
删除方式：
  a 整行删：一行（去空白）能完整切分为「K 全串」与「C 的全串或长度 ≥3 的前缀/后缀」的拼接，整行删。
  b 行内删：C 全串出现在更长的一行里、并且与前后文字直接粘连（被粘到别的字段后面），删掉该串；每个验证码最多删它在章内出现的次数。
     前面是空白或冒号、作为独立取值出现的不删（那是字段值，不是印章残字）。
  c 行首/行尾的 K 全串（≥4 字）删掉；每个关键词最多删它在章内出现的次数。"""
import json, re
from pathlib import Path
KW = re.compile(r'^[\u4e00-\u9fff]{0,12}(专用章|公章|用章|监制章)$')
KWSPLIT = re.compile(r'^([\u4e00-\u9fff]{0,12}(?:专用章|公章|用章|监制章))([A-Z0-9]{8,20})$')
BAD = re.compile(r'[盖（(：:]')
CODE = re.compile(r'^[A-Z0-9]{8,20}$')
nz = lambda s: re.sub(r'\s', '', s or '')
USCC = re.compile(r'^[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}$')
def is_code(t): return bool(CODE.match(t)) and not USCC.match(t) and sum(c.isalpha() for c in t) >= 2 and sum(c.isdigit() for c in t) >= 2
def glued_find(s, c):
    """找 c 在 s 里第一个「前面不是空白/冒号/行首」的出现位置；找不到返回 -1。"""
    i = s.find(c)
    while i >= 0:
        if i > 0 and not re.match(r'[\s:：]', s[i - 1]): return i
        i = s.find(c, i + 1)
    return -1
def words(sa, page):
    f = Path(sa) / 'raw'
    for k in ('main_accurate', 'main_general'):
        p = f / f'p{page:02d}_{k}.json'
        if p.exists():
            return [{'t': w['words'], 'cx': w['location']['left'] + w['location']['width'] / 2, 'cy': w['location']['top'] + w['location']['height'] / 2}
                    for w in json.load(open(p)).get('words_result', [])]
    return []
def seal_tokens(sa, page, summ):
    boxes = (summ.get(page) or {}).get('route', {}).get('seal_boxes') or []
    if not boxes: return [], []
    def inside(w):
        for x0, y0, x1, y1 in boxes:
            bw, bh = x1 - x0, y1 - y0
            if x0 - .1 * bw <= w['cx'] <= x1 + .1 * bw and y0 - .05 * bh <= w['cy'] <= y1 + .25 * bh: return True
        return False
    ws = words(sa, page); ins = [w for w in ws if inside(w)]; out = ''.join(nz(w['t']) for w in ws if not inside(w))
    K, C = [], []
    for w in ins:
        t = nz(w['t']); m = KWSPLIT.match(t)
        parts = [m.group(1), m.group(2)] if m else [t]
        for x in parts:
            if KW.match(x) and not BAD.search(x) and x not in out: K.append(x)
            elif is_code(x) and x not in out: C.append(x)
    return K, C
def segmentable(t, K, C):
    pieces = set(K)
    for c in C:
        pieces.add(c)
        for n in range(3, len(c)): pieces.add(c[:n]); pieces.add(c[-n:])
    ok = [True] + [False] * len(t)
    for i in range(1, len(t) + 1):
        for j in range(max(0, i - 40), i):
            if ok[j] and t[j:i] in pieces: ok[i] = True; break
    return ok[len(t)]
def clean_page(pg, K, C):
    log = []
    if not K and not C: return pg, log
    budC = {c: C.count(c) for c in set(C)}; budK = {k: K.count(k) for k in set(K)}
    secs = []
    for si, s in enumerate(pg['sections']):
        keep = []
        for ln in (s.get('text') or '').split('\n'):
            t = nz(ln)
            if len(t) >= 3 and segmentable(t, K, C):
                log.append({'段': si, '规则': 'a整行', '删字数': len(t)})
                for c in budC:
                    if c in t: budC[c] -= 1
                continue
            new = ln
            for c in sorted(budC, key=len, reverse=True):
                while budC[c] > 0 and nz(new) != c and glued_find(new, c) >= 0:
                    i = glued_find(new, c); new = new[:i] + new[i + len(c):]; budC[c] -= 1; log.append({'段': si, '规则': 'b行内验证码', '删字数': len(c)})
            for k in sorted(budK, key=len, reverse=True):
                if len(k) < 4: continue
                st = new.strip()
                if budK[k] > 0 and st.startswith(k) and nz(st) != k: new = st[len(k):]; budK[k] -= 1; log.append({'段': si, '规则': 'c行首关键词', '删字数': len(k)})
                st = new.strip()
                if budK[k] > 0 and st.endswith(k) and nz(st) != k: new = st[:-len(k)]; budK[k] -= 1; log.append({'段': si, '规则': 'c行尾关键词', '删字数': len(k)})
            keep.append(new)
        secs.append({**s, 'text': '\n'.join(keep)})
    return {**pg, 'sections': secs}, log
def apply(pred, sa):
    summ = {pg['page']: pg for pg in json.load(open(Path(sa) / 'call_summary.json'))['pages']}
    out, log = {}, []
    for p, pg in pred.items():
        K, C = seal_tokens(sa, int(p), summ)
        new, lg = clean_page(pg, K, C); out[p] = new
        log += [{'页': p, '章内关键词数': len(K), '章内验证码数': len(C), **x} for x in lg]
    return out, log
