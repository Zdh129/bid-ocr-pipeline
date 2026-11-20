"""方法6 表格行拼回（整合路线之后的后处理；只读缓存，不调用百度，不读参照、不读 manifest）。

背景：整合路线 S1 把乙′的字框换成颜色去章图高精度后，synth_blind_l 第9页乙′的改写（按坐标把折行拼回明细行、F4 把折到下一行的
单价尾数「1」接回原格）恰好与原文字顺序完全相同，乙′按“与原顺序一致，不改”丢掉了改写，于是输出退回基线C的物理行文本：
明细行被拆成 2～4 条物理行，栏位之间也没有分隔。

规则（每条只改空白/换行，不增删、不改动任何非空白字符，字序也不变；不满足就不动；最后整页断言去空白文本不变）：
  A 行拼回：对发票页每个含明细表头的表格段，用与乙′相同的输入（基线C文本、整合路线 S1 用的去章图高精度字框、表格线缓存）
    重算乙′的逻辑行/栏位划分（坐标：表头标签/竖线定栏，y 间距<1 字高并入下一起头行，F4 续行归格）；若乙′算出的新文本与原文
    去空白后完全相同（即乙′因“与原顺序一致”而丢弃的改写），且整合路线最终输出里仍原样保留这段原文（恰好一处），就换成乙′的分行分栏文本。
  B 半截行拼回（兜底，A 没处理的表格段）：见 rule_B。以「*」起头、还没读到税率栏的半截明细行，下一行不以「*」/合计等起头，
    y 在上一物理行 -0.5～gy 字高内、x 落在品名栏（文字续行）或上一行某个数字子段内（以数字/金额开头的续行，F4 同款），就去掉换行。
  C 栏内分隔：明细行（非表头）同一个空白片段里，某字与它前面最近一个同物理行的已定位字 x 间距 > gx×字高 → 插一个空格
    （单价与金额粘连等）；不在「%」「.」「,」前、「¥」后切，两边各至少 2 个字。
  D 表头分隔：「项目名称」表头行去空白后能被表头标签词表完整切分时，按标签加空格。
冻结参数：gx=0.4，gy=1.6（多轮迭代见 报告_方法6_中文.md）。
"""
import re, sys, json, copy, os
from pathlib import Path
sys.dont_write_bytecode = True
try:   # 仓库版：用 pipeline 在内存里加载 rules/round3（不改冻结文件）
    from pipeline.load_frozen import load_round
    _m = load_round('round3'); K, lp3 = _m['core'], _m['lp3']
except ImportError:   # 试验机：直接 import 第3轮冻结目录（只读）
    ROOT = Path(os.environ.get('M6_LAB_ROOT', '/workspace/pdf-test/baidu'))
    R3 = ROOT / 'output/盲测_版面约束/版面分析优化计划/试算/第3轮/冻结'
    sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(R3)]
    import core as K          # 第3轮冻结（只 import）
    import lp3                # 第3轮冻结乙′（只 import，不改）
from layout import nch, cx, cy, ch_h, phys_lines

NS = lambda s: re.sub(r'\s', '', s)
P_DEFAULT = {'A': True, 'B': True, 'C': True, 'D': True, 'gy': 1.6, 'gx': 0.4, 'gx_min_tok': 2}


class Ctx:
    def __init__(self, cfg):
        self.cfg = cfg
        self.C = json.load(open(cfg['C']))
        self.sa = Path(cfg['sa'])
        self.asum = {pg['page']: pg for pg in json.load(open(self.sa / 'call_summary.json'))['pages']}
        self.cls = {p: K.VB.classify(''.join(x['text'] for x in v['sections'])) for p, v in self.C.items()}
        self._a = {}
    def achars(self, p):
        p = int(p)
        if p not in self._a: self._a[p] = K.a_chars_for_page(self.sa, self.asum[p]) if p in self.asum else []
        return self._a[p]
    def lines(self, p):
        ld = Path(self.cfg['lines']); K.LN.D = ld
        return K.LN.detect(str(self.cfg['pdf']), self.cfg['name'], int(p))
    def is_inv(self, p): return K.VB.is_inv(''.join(x['text'] for x in self.C[p]['sections']), self.cls[p])


def yi_rewrite(text, achars, lines):
    """重算乙′，返回 (R 原明细区文本, 乙′新文本, 乙′日志)；乙′跳过时 new 为 None。"""
    cap = {}
    orig = lp3.check_moves
    def spy(R, new):
        cap['R'], cap['new'] = R, new
        return orig(R, new)
    lp3.check_moves = spy
    try:
        lg = []; out = lp3.invoice_region(text, achars, lines, '乙′', lg)
    finally:
        lp3.check_moves = orig
    return cap.get('R'), cap.get('new'), (lg[-1] if lg else {}), out


ROW_START = re.compile(r'^\s*[*＊]')
STOP = re.compile(r'^\s*(合\s*计|价\s*税|小\s*计|总\s*计|备\s*注|销售方|购买方)')


DONE = re.compile(r'%|免税|不征税|\*\*\*')


def rule_B(R, achars, h, gy):
    """兜底（A 没处理时）：半截行拼回。按 lp3.locate2 把明细区文本逐字定位到字框，然后：
    当前明细行以「*」起头、还没读到税率栏（行内无「%」/免税/不征税），下一行不以「*」/合计/价税合计等起头，且下一行首字
      ① 在当前行最后一条物理行上下 -0.5～gy 字高内（栏内垂直居中的字与上一物理行同高）；
      ② 横向：文字续行首字落在当前行首字 x 附近（品名栏，±1 字高）；以数字/金额开头的续行首字落在上一行某个数字子段 x 范围内（±0.3 字高，F4 同款）；
    就把换行去掉（拼成一行）。只删换行，不动字序。返回 (新R, 次数)。"""
    if not achars: return R, 0
    pls = phys_lines(achars); srt = [c for l in pls for c in l]
    loc = lp3.locate2(R, srt)
    box = {i: c for i, ch, c in loc if c is not None}
    ls = R.split('\n'); starts = []; o = 0
    for l in ls: starts.append(o); o += len(l) + 1
    def bx(k):
        bs = [(i, box[i]) for i in range(starts[k], starts[k] + len(ls[k])) if i in box]
        if not bs: return []
        ys = sorted(cy(c) for _, c in bs); my = ys[len(ys) // 2]
        keep = [(i, c) for i, c in bs if abs(cy(c) - my) < 1.5 * h]   # 去掉配错到页面别处的字（同字异处）
        first_ok = bool(keep) and keep[0][0] == min(i for i in range(starts[k], starts[k] + len(ls[k])) if not ls[k][i - starts[k]].isspace())
        return [c for _, c in keep], first_ok
    out = []; n = 0; k = 0
    while k < len(ls):
        cur = ls[k]; row = [k]
        while k + 1 < len(ls):
            nx = ls[k + 1]
            if not (ROW_START.match(ls[row[0]]) and not DONE.search(cur) and nx.strip() and not ROW_START.match(nx) and not STOP.match(nx)): break
            (A0, _), (A1, _), (B, b_first) = bx(row[0]) or ([], 0), bx(k) or ([], 0), bx(k + 1) or ([], 0)
            if not A0 or not A1 or not B: break
            last_y = max(cy(c) for c in A1); dy = cy(B[0]) - last_y
            if not (-0.5 * h < dy < gy * h): break   # 续行可与上一物理行同高（栏内垂直居中）或在其下方 gy 字高内
            x0 = B[0]['bbox'][0]
            if re.match(r'\s*[0-9¥￥]', nx):
                ok = False; run = []
                for c in sorted([c for c in A1 if abs(cy(c) - last_y) < 0.5 * h], key=lambda c: c['bbox'][0]) + [None]:
                    if c is not None and re.match(r'[0-9.,]', c['ch']) and (not run or c['bbox'][0] - run[-1]['bbox'][2] < 0.35 * h): run.append(c); continue
                    if run and run[0]['bbox'][0] - 0.3 * h <= x0 <= run[-1]['bbox'][2] + 0.3 * h: ok = True
                    run = [c] if c is not None and re.match(r'[0-9.,]', c['ch']) else []
            else:
                ok = abs(x0 - A0[0]['bbox'][0]) < 1.0 * h or not b_first   # 首字没能可靠定位时只看 y
            if not ok: break
            cur = cur.rstrip() + ('' if re.match(r'\s*[0-9¥￥]', nx) else '') + nx.lstrip(); row.append(k + 1); k += 1; n += 1
        out.append(cur); k += 1
    return '\n'.join(out), n


def _chunks(q, astr, used, min_len):
    """把片段 q 贪心切成在 A 串里唯一、未用、长度≥min_len 的最长前缀块；找不到的字记 None。返回每个字在 A 里的序号。"""
    loc = [None] * len(q); i = 0
    while i < len(q):
        best = None
        for e in range(len(q), i + min_len - 1, -1):
            s = q[i:e]; ks = [k for k in _find_all(astr, s) if not any(used[k:k + len(s)])]
            if len(ks) == 1: best = (e, ks[0]); break
        if best:
            e, k = best
            for t in range(i, e): loc[t] = k + (t - i); used[k + (t - i)] = True
            i = e
        else: i += 1
    return loc


def _find_all(a, s):
    out = []; k = a.find(s)
    while k >= 0: out.append(k); k = a.find(s, k + 1)
    return out


def rule_C(R, achars, h, gx, min_tok):
    """栏内分隔：明细行（非表头）每个空白片段内，第 j 个字与它前面最近一个落在同一物理行的已定位字 x 间距 > gx×字高 → 在 j 前插空格。
    片段先按 A 串唯一子串（≥3 字）定位；F4 接回的跨行尾数不参与比较。两边各至少 min_tok 个字才切。"""
    if not achars: return R, 0
    pls = phys_lines(achars); srt = [c for l in pls for c in l]
    lid = {id(c): k for k, l in enumerate(pls) for c in l}
    astr = ''.join(nch(c['ch']) for c in srt)
    used = [False] * len(srt); n = 0; out = []
    for ln in R.split('\n'):
        if ln.lstrip().startswith('项目名称'): out.append(ln); continue
        nl = []
        for t in re.split(r'(\s+)', ln):
            if not t or t.isspace() or len(t) < 2 * min_tok: nl.append(t); continue
            q = ''.join(nch(c) for c in t)
            if len(q) != len(t): nl.append(t); continue
            loc = _chunks(q, astr, used, 3)
            piece = t[0]
            for j in range(1, len(t)):
                b = srt[loc[j]] if loc[j] is not None else None
                if b is not None and min_tok <= j <= len(t) - min_tok and t[j] not in '%％.,，' and t[j - 1] not in '¥￥':
                    prev = [srt[loc[i]] for i in range(j - 1, -1, -1) if loc[i] is not None and lid[id(srt[loc[i]])] == lid[id(b)]]
                    if prev and prev[0]['bbox'][2] <= b['bbox'][0] + 0.2 * h and b['bbox'][0] - prev[0]['bbox'][2] > gx * h \
                            and (loc[j - 1] is None or loc[j - 1] != loc[j] - 1 or lid[id(srt[loc[j - 1]])] == lid[id(b)]):
                        piece += ' '; n += 1
                piece += t[j]
            nl.append(piece)
        out.append(''.join(nl))
    return '\n'.join(out), n


LABS = ['税率/征收率', '项目名称', '货物或应税劳务、服务名称', '货物或应税劳务名称', '规格型号', '征收率', '税率', '单位', '数量', '单价', '金额', '税额']


def rule_D(R):
    """表头分隔：「项目名称」表头行若去空白后能被表头标签词表完整切分，就按标签加空格。"""
    out = []; n = 0
    for ln in R.split('\n'):
        s = NS(ln)
        if s.startswith(('项目名称', '货物或应税')):
            toks = []; i = 0
            while i < len(s):
                lab = next((x for x in LABS if s.startswith(x, i)), None)
                if not lab: toks = None; break
                toks.append(lab); i += len(lab)
            if toks and ' '.join(toks) != ln.strip():
                lead = ln[:len(ln) - len(ln.lstrip())]
                out.append(lead + ' '.join(toks)); n += 1; continue
        out.append(ln)
    return '\n'.join(out), n


def region(t):
    """明细区：「项目名称」所在行行首 → 「价税合计」所在行行首。"""
    hs = t.find('项目名称')
    if hs < 0: hs = t.find('货物或应税')
    if hs < 0: return None
    m = re.search(r'价\s*税\s*合\s*计', t[hs:])
    if not m: return None
    a = t.rfind('\n', 0, hs) + 1; b = t.rfind('\n', 0, hs + m.start()) + 1
    return (a, b) if b > a else None


def apply(pred, cfg, P=None):
    P = {**P_DEFAULT, **(P or {})}
    S = Ctx(cfg); new = copy.deepcopy(pred); logs = []
    for p in sorted(pred, key=int):
        if p not in S.C or not S.is_inv(p) or int(p) not in S.asum: continue
        ach = S.achars(p); h = ch_h(ach) if ach else 0
        doneA = set()
        # A：乙′丢弃的分行分栏改写
        if P['A']:
            for i, x in enumerate(S.C[p]['sections']):
                if x['section'] != 'table' or not K.G.region_span(x['text']): continue
                R, Y, info, out = yi_rewrite(x['text'], ach, S.lines(p))
                if R is None or Y is None or out is not None or NS(Y) != NS(R) or Y == R: continue
                tgt = [j for j, y in enumerate(new[p]['sections']) if y['section'] == 'table' and y['text'].count(R) == 1]
                if len(tgt) != 1: logs.append({'页': int(p), '规则': 'A', '结果': '最终输出里找不到原样明细区，不动'}); continue
                j = tgt[0]; new[p]['sections'][j]['text'] = new[p]['sections'][j]['text'].replace(R, Y, 1); doneA.add(j)
                logs.append({'页': int(p), '段': j, '规则': 'A', 'A行拼回': {'物理行': R.count('\n') + 1, '逻辑行': Y.count('\n') + 1}, '原': R, '新': Y})
        # B/C/D：在最终输出的明细区上做（只改空白）
        for j, y in enumerate(new[p]['sections']):
            if y['section'] != 'table': continue
            rg = region(y['text'])
            if not rg: continue
            a, b = rg; T0 = y['text'][a:b]; T = T0; rec = {'页': int(p), '段': j}
            if P['B'] and j not in doneA:
                T2, nb = rule_B(T, ach, h, P['gy'])
                if nb: T = T2; rec['B半截行拼回'] = nb
            if P['C']:
                T2, nc = rule_C(T, ach, h, P['gx'], P['gx_min_tok'])
                if nc: T = T2; rec['C栏内分隔'] = nc
            if P.get('D', True):
                T2, nd = rule_D(T)
                if nd: T = T2; rec['D表头分隔'] = nd
            if T != T0:
                assert NS(T) == NS(T0), '只许改空白'
                new[p]['sections'][j]['text'] = y['text'][:a] + T + y['text'][b:]
                rec.update({'规则': 'BCD', '原': T0, '新': T}); logs.append(rec)
    for p in pred:
        assert NS(''.join(s['text'] for s in new[p]['sections'])) == NS(''.join(s['text'] for s in pred[p]['sections'])), p
    return new, logs
