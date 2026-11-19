"""二次高精度OCR：公共部分（只读已有输出；不改主路径代码）。工作目录须为 /workspace/pdf-test/baidu。"""
import json, re, sys, importlib.util
from pathlib import Path
sys.path.insert(0, 'src'); sys.path.insert(0, 'scripts')
D = Path('output/二次高精度OCR')
_spec = importlib.util.spec_from_file_location('oc', 'output/⊗规则复核/check_otimes.py')
# 只取 check_otimes 里的规则函数，不执行其主体：手工复制同样的定义（与 ⊗规则复核 完全一致）
PAT1 = re.compile(r'(价税合计\s*[（(]\s*大写\s*[）)])(?![\s|:：]*⊗)')
AMT = re.compile(r'^[ \t]*[:：|]?[ \t]*[零壹贰叁肆伍陆柒捌玖拾佰仟万亿]+[元圆]([零壹贰叁肆伍陆柒捌玖]角)?([零壹贰叁肆伍陆柒捌玖]分)?整?')
def classify(t):
    t = re.sub(r'\s', '', t); t = t.replace('（', '(').replace('）', ')')
    if '定额发票' in t or ('发票' in t and '定额' in t): return '定额发票'
    if '机打发票' in t or '通用机打' in t: return '机打发票'
    if sum(k in t for k in ('发票号码', '价税合计', '发票代码')) >= 2 or ('增值税' in t and '发票' in t): return '增值税发票'
    if '公民身份号码' in t or '居民身份证' in t or ('签发机关' in t and '有效期限' in t): return '证件'
    return '其他'
def r1(text, page_cls):
    if page_cls != '增值税发票': return text, 0
    n = 0
    def rep(m):
        nonlocal n
        if AMT.match(text[m.end():]): n += 1; return m.group(1) + '⊗'
        return m.group(0)
    return PAT1.sub(rep, text), n
J2 = 'output/配置甲/开发成绩_第二版/{}/pred_pages.json'
# 与 ⊗规则复核 相同的输入版本；高精度识别（原图原走法）原始返回作为置信度和坐标来源
SETS = [('synth18', J2.format('synth18'), '方案E', 'output/synth18/work/output/scheme_a', '/workspace/pdf-test/synth18/synth18-scan.pdf'),
        ('synth18b', 'output/synth18b/pred_pages.json', 'G2', 'output/synth18b/orig/work/output/scheme_a', '/workspace/pdf-test/synth18b/synth18b-scan.pdf'),
        ('synth18c', J2.format('synth18c'), '方案E', 'output/synth18c/orig/work/output/scheme_a', '/workspace/pdf-test/synth18c/synth18c-scan.pdf'),
        ('synth18d', 'output/synth18d/pred_pages.json', '参考：方案E（本轮之前冻结）', 'output/synth18d/orig/work/output/scheme_a', '/workspace/pdf-test/synth18d/synth18d-scan.pdf'),
        ('synth18e', 'output/synth18e/pred_pages.json', '方案E', 'output/synth18e/orig/work/output/scheme_a', '/workspace/pdf-test/synth18e/synth18e-scan.pdf'),
        ('blind20', 'output/blind20_frozen/完整计分/pred_pages.json', '冻结推荐组合（去掉深度求索纠错）', 'output/scheme_a', '/workspace/pdf-test/frozen20/blind20-scan.pdf')]
VER_NOTE = {'synth18b': 'synth18b 没有方案E输出，用 G2（与 ⊗规则复核 相同）', 'synth18d': '用「参考：方案E（本轮之前冻结）」',
            'blind20': '用冻结推荐组合（去掉深度求索纠错），与 ⊗规则复核 相同'}
def base_with_otimes(pf, ver):
    base = json.load(open(pf))[ver]
    cls = {p: classify(''.join(s['text'] or '' for s in v['sections'])) for p, v in base.items()}
    out = {}
    for p, v in base.items():
        out[p] = {**v, 'sections': [{**s, 'text': r1(s['text'] or '', cls[p])[0]} for s in v['sections']]}
    return out, cls
# ---- 关键字段 ----
USCC_CH = '0123456789ABCDEFGHJKLMNPQRTUWXY'
USCC_W = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]
def uscc_ok(s):
    if len(s) != 18 or any(c not in USCC_CH for c in s): return False
    t = sum(USCC_CH.index(c) * w for c, w in zip(s[:17], USCC_W))
    return USCC_CH[(31 - t % 31) % 31] == s[17]
import datetime
def date_ok(s):
    m = re.fullmatch(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', s) or re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', s)
    if not m: return False
    try: datetime.date(*map(int, m.groups())); return 1990 <= int(m.group(1)) <= 2035
    except ValueError: return False
def amt_ok(s): return bool(re.fullmatch(r'[¥￥]?-?(\d{1,3}(,\d{3})+|\d+)\.\d{2}', s.replace(' ', '')))
def no_ok(s, kind): return s.isdigit() and (len(s) in (8, 20) if kind == '发票号码' else len(s) in (10, 12))
def uscc_shape(s): return len(s) == 18 and all(c in USCC_CH for c in s)  # 合成数据的税号不满足国标校验位（参照里 0/30），只查结构
FMT = {'税号': uscc_shape, '日期': date_ok, '金额': amt_ok, '发票号码': lambda s: no_ok(s, '发票号码'), '发票代码': lambda s: no_ok(s, '发票代码')}
RX = [('发票号码', re.compile(r'发票号码\s*[:：]?\s*(\d{6,22})')), ('发票代码', re.compile(r'发票代码\s*[:：]?\s*(\d{6,14})')),
      ('日期', re.compile(r'(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|(?<!\d)\d{4}-\d{2}-\d{2}(?!\d))')),
      ('金额', re.compile(r'((?<![\d.,])[¥￥]?\s?-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?![\d%]))')),
      ('税号', re.compile(r'((?<![0-9A-Z])[0-9A-Z]{18}(?![0-9A-Z]))'))]
def fields_in(text):
    """返回 [(类型, 值, 起点, 终点)]；身份证号（上下文含 身份）不算税号；号码/代码里的数字不再当金额。"""
    out, used = [], []
    for kind, rx in RX:
        for m in rx.finditer(text):
            a, b = m.span(1)
            if any(a < y and x < b for x, y in used): continue
            v = m.group(1)
            if kind == '税号':
                ctx = text[max(0, a - 14):a]
                if '身份' in ctx or not (re.search(r'识别号|信用代码|税号', ctx) or uscc_ok(v) or re.search(r'[A-Z]', v)): continue
            out.append((kind, v, a, b)); used.append((a, b))
    return sorted(out, key=lambda x: x[2])
def norm(s): return re.sub(r'\s', '', s).replace('￥', '¥').replace('：', ':')
def lev(a, b):
    p = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        c = [i]
        for j, cb in enumerate(b, 1): c.append(min(p[j] + 1, c[j - 1] + 1, p[j - 1] + (ca != cb)))
        p = c
    return p[-1]
