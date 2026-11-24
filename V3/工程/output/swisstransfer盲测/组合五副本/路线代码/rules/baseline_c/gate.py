"""乙′＋门控 的门控条件（2026-11-19 在生成盲测集 synth_blind_g 之前写定并冻结；之后不改）。
来源：donghao 2026-11-19 语音要求「只在出现『合计和明细在同一行』或『表头和明细在同一行』时才触发」。
乙′（output/版面约束/layout_posthoc.py）本身不含此门控：只要发票明细区（「项目名称」…「价税合计」前）定位覆盖率达标且顺序不同就改写。
门控只看基线 C（方案E＋改进⊗，即 v3 发票重排后的文字）里该表格段的明细区文字，不看参照、不看 A 字框：
  区域   = apply.region_span(t)（从「项目名称」起，到「价税合计」所在行之前），按换行切行，去掉空行。
  表头行 = 区域第一行（以「项目名称」开头的那一行）。
  条件一「表头和明细同一行」：表头行里出现品名类别标记 *xx*（正则 \\*[^*\\s]{1,16}\\*），或出现两位小数金额（(?<![\\d.])\\d+\\.\\d{2}(?!\\d)）。
  条件二「合计和明细同一行」：区域里某一行含「合计」（合\\s*计），且该行同时含 *xx* 标记，或「合计」不在该行行首（去掉行首空白后）。
  任一条件成立 → 触发（用乙′的改写结果，乙′自己仍可能因覆盖率不足/顺序相同而不改）；都不成立 → 保留 C 原文。
说明：此门控是在看过 synth18b 第16、17页、synth18e 第11页、blind20 第19页（开发集上乙′改对/改错的页）之后写的，只对新集 synth_blind_g 是盲的。
"""
import re
STAR = re.compile(r'\*[^*\s]{1,16}\*')
AMT2 = re.compile(r'(?<![\d.])\d+\.\d{2}(?!\d)')
HEJI = re.compile(r'合\s*计')


def region_span(t):
    """与 output/版面约束/apply.py 的 region_span 逐字相同（复制，避免导入 apply 的副作用）。"""
    hs = t.find('项目名称'); vm = re.search(r'价\s*税\s*合\s*计', t[hs + 1:]) if hs >= 0 else None
    if hs < 0 or not vm: return None
    ve = t.rfind('\n', 0, hs + 1 + vm.start()) + 1
    return (hs, ve) if ve > hs else None


def gate(t):
    """返回 (是否触发, 原因列表)。t = C 的一个表格段文字。"""
    r = region_span(t)
    if not r: return False, ['无明细区']
    lines = [l for l in t[r[0]:r[1]].split('\n') if l.strip()]
    if not lines: return False, ['明细区为空']
    why = []
    hdr = lines[0]
    if STAR.search(hdr) or AMT2.search(hdr): why.append('表头和明细同一行')
    for l in lines:
        if HEJI.search(l) and (STAR.search(l) or not HEJI.match(l.lstrip())):
            why.append('合计和明细同一行'); break
    return bool(why), why
