"""第三层：字段级语义对应与金额校验（只读预测文本，不改字；不读参照，不调接口）。
对每页表格段做：
 1) 明细行抽取：一行里能同时认出「18 位凭证号 …… 入库日期 + 行末金额」→ 完税/社保明细行；以「*」开头且末尾是「金额 税率 税额」→ 发票明细行。
 2) 合计校验：完税/社保——明细金额之和 = 「金额合计」小写 ¥ 金额，且大写金额 = 小写金额；发票——明细金额之和 = 合计金额，明细税额之和 = 合计税额，金额＋税额 = 价税合计小写 = 价税合计大写。
 3) 纳税信用：「评价年度」「评价得分」「年度评价结果」等键能找到对应值；指标记分行能拆成「代码 名称 记分」。
输出每页的抽取结果与校验结论；校验不过的页标「待复核」。这一层不改文字，所以对错字数没有直接影响，作用是质量闸门和给后续结构化输出用。"""
import re
from decimal import Decimal
MONEY = r'[¥￥]?-?\d{1,3}(?:,\d{3})*\.\d{2}|[¥￥]?-?\d+\.\d{2}'
D = lambda s: Decimal(re.sub(r'[¥￥,]', '', s))
CN = {'零': 0, '壹': 1, '贰': 2, '叁': 3, '肆': 4, '伍': 5, '陆': 6, '柒': 7, '捌': 8, '玖': 9}
U = {'拾': 10, '佰': 100, '仟': 1000}
def cn2num(s):
    s = re.sub(r'[^零壹贰叁肆伍陆柒捌玖拾佰仟万亿元圆角分整]', '', s or '')
    if not s or '元' not in s and '圆' not in s: return None
    ip, _, fp = re.split('[元圆]', s, 1)[0], None, re.split('[元圆]', s, 1)[1]
    def sec(t):
        n, cur = 0, 0
        for ch in t:
            if ch in CN: cur = CN[ch]
            elif ch in U: n += (cur or 1) * U[ch]; cur = 0
        return n + cur
    tot = 0
    for part, mul in ((ip.split('亿')[0] if '亿' in ip else '', 10 ** 8),):
        tot += sec(part) * mul
    rest = ip.split('亿')[-1]
    if '万' in rest: w, rest = rest.split('万', 1); tot += sec(w) * 10000
    tot += sec(rest)
    j = re.search('([零壹贰叁肆伍陆柒捌玖])角', fp); f = re.search('([零壹贰叁肆伍陆柒捌玖])分', fp)
    return Decimal(tot) + Decimal(CN[j.group(1)] if j else 0) / 10 + Decimal(CN[f.group(1)] if f else 0) / 100
TAXROW = re.compile(r'(\d{18})\s.*?(\d{4}-\d{2}-\d{2}(?:至\d{4}-\d{2}-\d{2})?)?.*?(\d{4}-\d{2}-\d{2})\s+(' + MONEY + r')\s*$')
def check_page(pg):
    tab = '\n'.join(s['text'] for s in pg['sections'] if s.get('section') == 'table'); allt = '\n'.join(s['text'] for s in pg['sections'])
    lines = [l.strip() for l in tab.split('\n') if l.strip()]; res = {}
    if '金额合计' in tab:   # 完税/社保
        rows = [m.groups() for l in lines for m in [TAXROW.search(re.sub(r'\s+', ' ', l))] if m]
        tl = next((l for l in lines if '金额合计' in l), ''); sm = re.findall(MONEY, tl)
        res = {'类型': '完税/社保', '明细行数': len(rows), '明细金额': [r[3] for r in rows], '合计小写': sm[-1] if sm else None, '合计大写': cn2num(tl)}
        ok = []
        if rows and sm: ok.append(('明细之和=合计', sum(D(r[3]) for r in rows) == D(sm[-1])))
        if sm and res['合计大写'] is not None: ok.append(('大写=小写', res['合计大写'] == D(sm[-1])))
        res['校验'] = ok; res['结论'] = ('明细未抽出（结构乱，只校验了大写=小写）' if not rows else '通过') if ok and all(x[1] for x in ok) else '待复核'
    elif '价税合计' in allt:   # 发票
        items = []
        for l in lines:
            if l.startswith('*'):
                t = re.findall(MONEY + r'|\d+%|免税|不征税', l)
                if len(t) >= 3 and re.match(r'\d+%|免税|不征税', t[-2]): items.append((t[-3], t[-1]))
        hl = next((l for l in lines if re.match(r'^合\s*计', l)), ''); hm = re.findall(MONEY, hl)
        pl = next((l for l in allt.split('\n') if '价税合计' in l), ''); pm = re.findall(MONEY, pl)
        res = {'类型': '发票', '明细行数': len(items), '合计': hm, '价税合计小写': pm[-1] if pm else None, '价税合计大写': cn2num(pl)}
        ok = []
        if items and len(hm) >= 2:
            ok.append(('明细金额之和=合计金额', sum(D(a) for a, _ in items) == D(hm[0]))); ok.append(('明细税额之和=合计税额', sum(D(b) for _, b in items) == D(hm[-1])))
        if len(hm) >= 2 and pm: ok.append(('金额+税额=价税合计', D(hm[0]) + D(hm[-1]) == D(pm[-1])))
        if pm and res['价税合计大写'] is not None: ok.append(('大写=小写', res['价税合计大写'] == D(pm[-1])))
        res['校验'] = ok; res['结论'] = ('明细未抽出' if not items else '通过') if ok and all(x[1] for x in ok) else '待复核'
    elif '评价得分' in allt or '纳税缴费信用' in allt:
        kv = {k: (re.search(k + r'\s*([^\s\n]+)', allt) or [None, None])[1] for k in ('评价年度', '评价得分', '年度评价结果')}
        recs = re.findall(r'(\d{6})\s+\d{6}\.(\S+?)\s+([+-]\d+)', allt)
        res = {'类型': '纳税信用', '键值': kv, '指标记分行': len(recs), '结论': '通过' if all(kv.values()) and recs else '待复核'}
    return res
def run(pred): return {p: r for p, pg in pred.items() for r in [check_page(pg)] if r}
