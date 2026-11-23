"""候选路线_v2 末端后处理（由 对齐ChatGPT版/工具/对齐规则.py 复制，不改原文件）。
对齐ChatGPT版新增规则（全部通用：只看本页输出文字和本页 OCR 坐标，不看参照，不按文件名或页码）。
规则乙 证书页脚过滤：证书类页面（页首 80 字内有「证书」，且全文有「认证」「有效期」「证明」之一）的底部（后 40% 行）里，
    删掉签名栏（公司代表/签发人等＋(签名)）、证书信息查询句（本证书信息…查询、网址、电话、致电）、认证机构地址行（含省/市且有路/道/街/号/楼或六位邮编），
    以及国际认可论坛标志环上的英文（MEMBER OF MULTILATERAL RECOGNITION ARRANGEMENT 的片段）。删字，不改其它字。
规则丙 发票明细单元格拼接：发票页（与第四轮 版式() 同一判定）的明细区，按表头各列的横向中线划列，以「*」开头的项目名为行锚，
    同一列里上下相邻的文字块拼成一个单元格（单元格内折行拼回），每行按列从左到右输出「表格行→单元格」。只改顺序，不增删字（自检）。
规则丁 发票背景水印：发票页（版式为发票，或页内至少两个发票关键词）上，删掉整行只由「国家税务总局」这些字组成的水印（可重复、可残缺），
    并从行内去掉连续的水印字串（长度≥2，可夹空白）。「价税合计」「税额」「税率」「税务局」等正文用语先保护，不删。
    在评估集上对照过发票截图，结果可能偏乐观。"""
import re, sys
from collections import Counter
from pathlib import Path
O = Path('/workspace/pdf-test/baidu/output'); R = O / 'swisstransfer盲测/组合五副本/路线代码/rules'
sys.path[:0] = [str(R / 'v4'), str(R / 'v3')]
import phys_order as PO, layout_order as L4
norm = PO.norm
# ---------------- 规则乙 ----------------
CERT_ANY = ('认证', '有效期', '证明')
SIGN = re.compile(r'(公司代表|法定代表人|授权代表|签发人|批准人|负责人|总经理)?\s*[（(]\s*(签名|签字|签章)\s*[)）]')
QUERY = re.compile(r'本证书信息|证书信息|可在.{0,30}查询|查询.{0,6}网|www\.|http|网址|致电|电话[:：]?\s*\d')
ADDR = re.compile(r'(省|市).{0,30}(路|大道|街|号|楼|大厦)|[(（]\d{6}[)）]|邮编|邮政编码')
RING = 'MEMBEROFMULTILATERALRECOGNITIONARRANGEMENT'
KEEP = {'MANAGEMENT', 'SYSTEM', 'CNAS', 'IAF', 'ILAC', 'CMA', 'CNCA', 'ISO', 'QUALITY', 'ENVIRONMENTAL', 'OCCUPATIONAL', 'HEALTH', 'SAFETY', 'CERTIFICATE'}
def is_cert(pg):
    t = norm(''.join(s.get('text') or '' for s in pg.get('sections') or []))
    return '证书' in t[:80] and any(k in t for k in CERT_ANY) and not L4.版式(pg) == '发票'
def _subseq(a, b):
    it = iter(b); return all(c in it for c in a)
def strip_ring(line, ring_seen):
    """标志环英文：整段是环上文字的子串（≥3 字母）直接删；页上已确认有环文字（≥5 字母子串）时，环字母的子序列碎片（OCR 打乱）也删；
    与其它英文粘连时只削掉前后缀里的环文字。MANAGEMENT SYSTEM、CNAS、IAF 等含环外字母的保留。"""
    def rep(m):
        s = m.group(0)
        if s in KEEP: return s
        if s in RING or (ring_seen and _subseq(s, RING)): return ''
        for keep in ('MANAGEMENTSYSTEM', 'CNAS'):
            if keep in s:
                a, b = s.split(keep, 1)
                return ('' if (len(a) >= 3 and (a in RING or (ring_seen and _subseq(a, RING)))) else a) + keep + ('' if (len(b) >= 3 and (b in RING or (ring_seen and _subseq(b, RING)))) else b)
        for k in range(len(s) - 2, 2, -1):   # 前缀
            if s[:k] in RING: return s[k:]
        return s
    return re.sub(r'[A-Z]{3,}', rep, line)
LOGO_W = ('中国认可', '国际互认', '管理体系', '认可', 'MANAGEMENT', 'SYSTEM', 'CNAS', 'IAF', 'ILAC', 'CMA', 'ISO')
def is_foot(n): return bool(QUERY.search(n) or (ADDR.search(n) and len(n) >= 12) or SIGN.search(n))
def is_logo(n): return bool(re.fullmatch(r'[A-Za-z0-9\-.:/()（）]+', n)) or (len(n) <= 40 and any(k in n for k in LOGO_W) and '：' not in n and ':' not in n)
def footer(pg):
    if not is_cert(pg): return pg, None
    rows = [(s.get('section') or 'body', ln) for s in pg.get('sections') or [] for ln in (s.get('text') or '').split('\n')]
    idx = [i for i, (_, l) in enumerate(rows) if norm(l)]; cut = idx[int(len(idx) * 0.6)] if idx else 0
    # 页脚块：从页末往上，连续的「页脚行（签名栏/查询/地址）或标志行（认可标志文字、纯英文数字）」，最多到后 40% 行的起点；遇到正文行就停
    j = len(rows) - 1
    while j >= cut:
        n = norm(rows[j][1])
        if n and not (is_foot(n) or is_logo(n) or len(n) <= 2): break
        j -= 1
    cut = j + 1
    out, log = [], []
    ring_seen = any(len(x) >= 5 and x in RING for _, l in rows[cut:] for x in re.findall(r'[A-Z]{5,}', norm(l)))
    for i, (sec, l) in enumerate(rows):
        if i < cut: out.append((sec, l)); continue
        n = norm(l)
        if QUERY.search(n) or (ADDR.search(n) and len(n) >= 12) or (n and len(n) <= 2 and not n.isdigit()): log.append(('整行', l)); continue
        l2 = SIGN.sub('', l); l2 = strip_ring(l2, ring_seen)
        if l2 != l: log.append(('片段', l, l2))
        if norm(l2): out.append((sec, l2))
    if not log: return pg, None
    return {**pg, 'sections': [{'section': s, 'text': t} for s, t in out]}, log
# ---------------- 规则丙 ----------------
HDR0 = ('项目名称', '货物或应税', '货物或服务', '货物名称')
TOTAL = re.compile(r'^(合计|小计|价税合计)')
def invoice_cells(pg, units):
    if units is None or L4.版式(pg) != '发票': return pg, None
    rows0 = [(s.get('section') or 'body', ln) for s in pg.get('sections') or [] for ln in (s.get('text') or '').split('\n')]
    lines, P, lid, own = PO.locate(pg, units)
    if not P: return pg, None
    cy = lambda u: u['y'] + u['h'] / 2
    hu = [u for u in units if any(k in u['t'] for k in HDR0)]
    if not hu: return pg, None
    h0 = min(hu, key=lambda u: u['y']); hmed = h0['h']
    hdr = sorted([u for u in units if abs(cy(u) - cy(h0)) <= 0.6 * hmed and not u['vert']], key=lambda u: u['x'])
    if len(hdr) < 3: return pg, None
    bottom = [cy(u) for u in units if cy(u) > cy(h0) + 0.5 * hmed and TOTAL.match(u['t'])]
    if not bottom: return pg, None
    ybot = min(bottom) - 0.5 * hmed; ytop = cy(h0) + 0.6 * hmed
    k = L4.skew(hdr); cyk = lambda u: cy(u) - k * (u['x'] + u['w'] / 2)   # 倾斜校正后的纵坐标
    toty = [cyk(u) for u in units if TOTAL.match(u['t'])]
    reg = [i for i, u in enumerate(units) if ytop < cy(u) < ybot and not re.match(r'^[¥￥]', u['t']) and all(abs(cyk(u) - t) > 0.6 * hmed for t in toty) and not u['vert'] and not (len(u['t']) >= 2 and u['h'] > 1.2 * u['w']) and u['x'] <= hdr[-1]['x'] + hdr[-1]['w'] + 2 * hmed]
    anc = [i for i in reg if units[i]['t'].startswith('*')]
    if not anc: return pg, None
    cen = [u['x'] + u['w'] / 2 for u in hdr]; bnd = [(a + b) / 2 for a, b in zip(cen, cen[1:])]
    col = lambda u: sum(u['x'] + u['w'] / 2 > b for b in bnd) if True else 0
    # 每列：上下相邻（间距 < 0.8 字高）的文字块合成一个单元格
    ay = sorted((cyk(units[i]), i) for i in anc)
    def aligned(i):   # 与某个行锚同一物理行（倾斜校正后中心差 ≤0.35 字高）则返回该行锚，否则 None
        d, a = min((abs(y - cyk(units[i])), a) for y, a in ay)
        return a if d <= 0.35 * hmed else None
    cells = {}
    for c in range(len(hdr)):
        us = sorted([i for i in reg if col(units[i]) == c], key=lambda i: units[i]['y']); grp = []
        for i in us:
            al = aligned(i); g_al = {aligned(j) for j in grp[-1]} - {None} if grp else set()
            close = grp and units[i]['y'] - (units[grp[-1][-1]]['y'] + units[grp[-1][-1]]['h']) < 0.8 * hmed
            if close and not units[i]['t'].startswith('*') and not (al is not None and g_al): grp[-1].append(i)   # 一个单元格里最多一个与行锚对齐的块
            else: grp.append([i])
        cells[c] = grp
    rowcells = {i: {} for _, i in ay}
    for c, gs in cells.items():
        for g in gs:
            al = [aligned(j) for j in g if aligned(j) is not None]
            if al: rowcells[al[0]].setdefault(c, []).extend(g); continue
            gy = (units[g[0]]['y'] + units[g[-1]]['y'] + units[g[-1]]['h']) / 2 - k * (units[g[0]]['x'] + units[g[0]]['w'] / 2)
            a = min(ay, key=lambda t: abs(t[0] - gy))[1]; rowcells[a].setdefault(c, []).extend(g)
    regset = set(reg); n = len(P)
    txt = lambda ui: ''.join(P[j] for j in range(n) if own[j] == ui)
    # 守卫：只在「有折行单元格、且折行内容在现有输出里没有连在一起」时才重建；已经按行列输出的（如第四轮甲2 已重建的）不动
    wrapped = [g for gs in cells.values() for g in gs if len(g) >= 2 and sum('\u4e00' <= ch <= '\u9fff' for ui in g for ch in units[ui]['t']) >= 2]   # 只看含汉字的折行单元格（纯数字折行不触发）
    if not wrapped: return pg, None
    full = ''.join(P)
    if all(''.join(txt(ui) for ui in g) in full for g in wrapped): return pg, None
    def cell_order(us):   # 单元格内：先按物理行（倾斜校正后中心差 ≤0.35 字高为同一行）自上而下，行内自左而右
        us = sorted(us, key=lambda ui: cyk(units[ui])); lines = []
        for ui in us:
            if lines and abs(cyk(units[ui]) - cyk(units[lines[-1][0]])) <= 0.35 * hmed: lines[-1].append(ui)
            else: lines.append([ui])
        return [ui for L in lines for ui in sorted(L, key=lambda ui: units[ui]['x'])]
    newrows = []
    for _, a in ay:
        cs = []
        for c in sorted(rowcells[a], key=lambda c: min(units[ui]['x'] for ui in rowcells[a][c])):   # 单元格按实际横坐标排，不按表头列号（窄列表头中线不准）
            s = ''.join(txt(ui) for ui in cell_order(rowcells[a][c]))
            if s: cs.append(s)
        if cs: newrows.append(' '.join(cs))
    if not newrows: return pg, None
    # 替换：受影响行里属于明细区文字块的字移走，新行插在第一处受影响行的位置
    aff = sorted({lid[j] for j in range(n) if own[j] in regset})
    out = []; inserted = False
    li_chars = {}
    for j in range(n): li_chars.setdefault(lid[j], []).append(j)
    for li, (sec, l) in enumerate(rows0):
        if li not in aff: out.append((sec, l)); continue
        js = li_chars.get(li, []); first = min([j for j in js if own[j] in regset], default=None)
        pre = ''.join(P[j] for j in js if own[j] not in regset and (first is None or j < first))
        post = ''.join(P[j] for j in js if own[j] not in regset and first is not None and j > first)
        if not inserted:   # 同一行里明细区之前的字（如表头）放在新行前，之后的字（如合计）放在新行后
            if pre: out.append((sec, pre))
            out += [('table', r) for r in newrows]; inserted = True
            if post: out.append((sec, post))
        elif pre + post: out.append((sec, pre + post))
    new = {**pg, 'sections': [{'section': s, 'text': t} for s, t in out]}
    a, b = Counter(P), Counter(norm(''.join(t for _, t in out)))
    if a != b: return pg, {'跳过': '字符不一致'}
    if norm(''.join(t for _, t in out)) == ''.join(P): return pg, None
    return new, {'行': len(newrows), '列': len(hdr)}

# ---------------- 规则丁 ----------------
WM_CHARS = set('国家税务总局')
WM_KEEP = ('价税合计', '税额', '税率', '征收率', '纳税人', '税务局', '税务机关', '发票', '税收', '完税', '应税', '含税', '不含税', '计税', '货物或应税', '增值税', '服务', '中国', '国际', '业务', '事务', '公务', '国家级')
INV_KEYS = ('电子发票', '发票号码', '价税合计', '开票日期', '货物或应税', '增值税专用发票', '增值税普通发票')
WM_FRAG = {'国', '家', '税', '务', '总', '局', '总局', '税务', '国家'}
def is_invoice_page(pg):
    if L4.版式(pg) == '发票':
        return True
    t = norm(''.join(s.get('text') or '' for s in pg.get('sections') or []))
    return sum(k in t for k in INV_KEYS) >= 2
def is_wm_line(n):
    if not n:
        return False
    if all(c in WM_CHARS for c in n):
        return True
    if any(k in n for k in WM_KEEP):
        return False
    return n in WM_FRAG
def _mask_keep(s):
    spans = []
    def add(j, k):
        if any(a <= j < b or a < j + len(k) <= b for a, b, _ in spans):
            return False
        spans.append((j, j + len(k), k)); return True
    # 「国家税务总局」后接非水印汉字时，视为机关名称（如「国家税务总局××市税务局」），不是背景水印
    i = 0
    while True:
        j = s.find('国家税务总局', i)
        if j < 0:
            break
        nxt = s[j+6] if j+6 < len(s) else ''
        if nxt and ('\u4e00' <= nxt <= '\u9fff') and nxt not in WM_CHARS:
            add(j, '国家税务总局')
        i = j + 1
    # 「国家」后接非水印汉字时视为正文（国家级、国家高新、国家自然保护区），后接「税」则留给水印串处理
    i = 0
    while True:
        j = s.find('国家', i)
        if j < 0:
            break
        nxt = s[j+2] if j+2 < len(s) else ''
        if nxt and ('\u4e00' <= nxt <= '\u9fff') and nxt not in WM_CHARS:
            add(j, '国家')
        i = j + 1
    for k in sorted(WM_KEEP, key=len, reverse=True):
        i = 0
        while True:
            j = s.find(k, i)
            if j < 0:
                break
            add(j, k); i = j + 1
    if not spans:
        return s, []
    spans.sort()
    chars = list(s)
    for a, b, k in spans:
        for i in range(a, b):
            chars[i] = '\0'
    return ''.join(chars), spans
def _is_digit_neighbor(ch):
    return bool(ch) and ch.isdigit()
def _strip_inline(line):
    """行内去掉连续水印字（≥2 个水印字，中间可夹空白）；已保护的正文用语（服务、中国、价税合计等）不动。
    另：紧贴阿拉伯数字的单个水印字也去掉（如日期里夹入的「税20总26」），不因普通标点误伤「服务*」。"""
    if not line:
        return line
    masked, spans = _mask_keep(line)
    n = len(masked)
    drop = [False] * n
    i = 0
    while i < n:
        if masked[i] == '\0' or not (masked[i] in WM_CHARS or masked[i].isspace()):
            i += 1
            continue
        j, wm = i, 0
        while j < n and masked[j] != '\0' and (masked[j] in WM_CHARS or masked[j].isspace()):
            if masked[j] in WM_CHARS:
                wm += 1
            j += 1
        if wm >= 2:
            for k in range(i, j):
                if masked[k] != '\0':
                    drop[k] = True
        i = j
    def prev(k):
        return masked[k-1] if k-1 >= 0 else ''
    def nxt(k):
        return masked[k+1] if k+1 < n else ''
    for k, ch in enumerate(masked):
        if drop[k] or ch == '\0' or ch not in WM_CHARS:
            continue
        if _is_digit_neighbor(prev(k)) or _is_digit_neighbor(nxt(k)):
            drop[k] = True
    if not any(drop):
        return line
    return ''.join(ch for ch, d in zip(line, drop) if not d)
def watermark(pg):
    if not is_invoice_page(pg):
        return pg, None
    rows = [(s.get('section') or 'body', ln) for s in pg.get('sections') or [] for ln in (s.get('text') or '').split('\n')]
    out, log = [], []
    for sec, l in rows:
        n = norm(l)
        if is_wm_line(n):
            log.append(('整行', l)); continue
        l2 = _strip_inline(l)
        # 去掉行内剥离后剩下的整行水印残片（规范后为空或仍是残片）
        n2 = norm(l2)
        if not n2:
            if n:
                log.append(('整行', l))
            continue
        if is_wm_line(n2):
            log.append(('整行', l)); continue
        if l2 != l:
            log.append(('片段', l, l2))
        out.append((sec, l2))
    if not log:
        return pg, None
    return {**pg, 'sections': [{'section': s, 'text': t} for s, t in out]}, log
