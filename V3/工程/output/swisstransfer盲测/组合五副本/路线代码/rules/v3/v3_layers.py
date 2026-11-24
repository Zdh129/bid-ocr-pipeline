"""第三版新增层（全部通用：只看本页识别文字和本页百度坐标／置信度，不看参照，不按文件名或页码）。
  层 A 物理行重排（税务／社保类表单）：页首出现完税证明、缴税凭证、纳税信用评价、社保缴费等表单标题时，
       用 phys_order 按物理行重排本页正文（只改顺序不增删字）。其他页面保持第二版的逻辑顺序。
  层 B 认可标志与发证机构页脚：本页出现 ≥2 种认可标志（中国认可、国际互认、IAF、CNAS 等）时，从标志区（含紧挨其上的签发人栏）到页底的文字移出正文。
  层 C 低置信度英文杂字：百度置信度 < 0.4、以英文字母为主的文字块（页边防伪纹、底纹误读）。
  层 D 超大字且置信度 < 0.9：字高 ≥2.5 倍本页中位字高（手写签名、图形徽标）。
  层 E 页首英文徽标：位于本页主标题之上的纯英文字母块。
  层 F 表单盖章栏补括号：在层 A 生效的税务／社保表单里，独立成块的「盖章」「签章」补全为「(盖章)」「(签章)」。
  层 A 另：竖排条带的单字归入中心最近的物理行（第 3 轮起）。
其余层在后续轮次追加，每层有独立开关（配置_第三版.json）。"""
import re
import phys_order as PO
FORM_KW = ('完税证明', '完税凭证', '缴税付款凭证', '电子缴税', '税收缴款书', '纳税缴费信用评价', '纳税信用评价', '社会保险费', '缴费证明')
def head(pg, n=60): return PO.norm(''.join(s.get('text') or '' for s in pg.get('sections') or []))[:n]
def is_tax_form(pg): return any(k in head(pg) for k in FORM_KW)
SEAL_LABEL = re.compile(r'(^|\s)(盖章|签章)(?=\s|$)')
def fix_seal_label(pg):
    """层 F：税务／社保表单里独立成块的「盖章」「签章」栏目标签，补全为标准写法「(盖章)」（识别常漏掉括号）。"""
    n = 0; secs = []
    for s in pg.get('sections') or []:
        t, k = SEAL_LABEL.subn(lambda m: m.group(1) + '(' + m.group(2) + ')', s.get('text') or ''); n += k; secs.append({**s, 'text': t})
    return ({**pg, 'sections': secs}, n)
def layer_phys(sub, sa_dir, cfg=None):
    out, log = {}, {}
    for q, pg in sub.items():
        if not is_tax_form(pg): out[q] = pg; continue
        u = PO.load_units(sa_dir, int(q))
        if u is None: out[q] = pg; log[q] = '无坐标，跳过'; continue
        out[q], log[q] = PO.reorder(pg, u)
        if (cfg or {}).get('第三版F_表单盖章栏补括号'):
            out[q], k = fix_seal_label(out[q]); log[q]['盖章栏补括号'] = k
    return out, log

# ---------------- 第三版 B/C/D/E：页面噪声与页脚（只删字，按本页百度坐标与置信度判断） ----------------
import re
from statistics import median
MARKS = re.compile(r'中国认可|国际互认|^IAF$|^CNAS|MANAGEMENTSYSTEM|^ILAC|^CMA$|MULTILATER|RECOGNITION|ARRANGEMENT')
SIGN_KW = ('签发人', '批准人', '授权签字人', '签字')
def _lat(t): return t and sum(c.isascii() for c in t) / len(t) >= 0.8 and any(c.isalpha() for c in t)
def _cjk(t): return any('\u4e00' <= c <= '\u9fff' for c in t)
def noise_page(pg, units, cfg):
    lines, P, lid, own = PO.locate(pg, units)
    n = len(P)
    if not n: return pg, {}
    hs = [u['h'] for u in units if not u['vert']] or [1]; med = median(hs); H = max(u['y'] + u['h'] for u in units)
    flag = {}   # 文字块下标 -> 规则
    # C 低置信度英文杂字（置信度 < 0.4 且以英文字母为主，多为页边防伪纹、底纹被误读）
    if cfg.get('第三版C_低置信度英文杂字'):
        for i, u in enumerate(units):
            if u['prob'] is not None and u['prob'] < 0.4 and _lat(u['t']): flag[i] = 'C'
    # D 超大字且置信度 < 0.9（手写签名、图形徽标）
    if cfg.get('第三版D_超大字低置信度'):
        for i, u in enumerate(units):
            if not u['vert'] and u['h'] >= 2.5 * med and u['prob'] is not None and u['prob'] < 0.9: flag.setdefault(i, 'D')
    # E 页首英文徽标：位于本页主标题（页面上部 35% 内字高最大的高置信度中文块，字高 ≥1.8 倍中位数）之上的纯英文块
    if cfg.get('第三版E_页首英文徽标'):
        cand = [u for u in units if not u['vert'] and _cjk(u['t']) and (u['prob'] or 0) >= 0.9 and u['y'] < 0.35 * H]
        if cand:
            T = max(cand, key=lambda u: u['h'])
            if T['h'] >= 1.8 * med:
                for i, u in enumerate(units):
                    if not u['vert'] and u['y'] + u['h'] <= T['y'] and re.fullmatch(r'[A-Za-z]{2,}', u['t']): flag.setdefault(i, 'E')
    # B 认可标志与发证机构页脚：本页出现 ≥2 种认可标志时，从标志区（含紧挨其上的签发人栏）到页底的文字移出正文
    foot_y = None
    if cfg.get('第三版B_认可标志页脚'):
        mk = [u for u in units if MARKS.search(PO.norm(u['t']).upper())]
        if len({MARKS.search(PO.norm(u['t']).upper()).group(0) for u in mk}) >= 2:
            foot_y = min(u['y'] for u in mk)
            sg = [u['y'] for u in units if u['t'].startswith(SIGN_KW) and foot_y - 4 * med <= u['y'] < foot_y]
            if sg: foot_y = min(sg)
            for i, u in enumerate(units):
                if u['y'] + u['h'] / 2 >= foot_y and not u['vert']: flag.setdefault(i, 'B')
    if not flag: return pg, {}
    drop = set(i for i in range(n) if own[i] in flag)
    # D 的补充：被标记大字块里的字若以无人认领的零散字形式挂在某行首尾，也删掉
    for i, u in enumerate(units):
        if flag.get(i) != 'D': continue
        for j in range(n):
            if own[j] < 0 and P[j] in u['t'] and (j == 0 or lid[j - 1] != lid[j] or j == n - 1 or lid[j + 1] != lid[j]): drop.add(j)
    # B 的补充：整行认领字都在页脚 → 整行删；页脚开始后无人认领的整行（补读来源）也删
    if foot_y is not None:
        started = False
        for li in range(len(lines)):
            idx = [i for i in range(n) if lid[i] == li]
            if not idx: continue
            owned = [i for i in idx if own[i] >= 0]
            if owned and all(own[i] in flag and flag[own[i]] == 'B' for i in owned): drop.update(idx); started = True
            elif owned: started = False if any(own[i] not in flag for i in owned) else started
            elif started: drop.update(idx)
    if not drop: return pg, {'删除字数': 0, '标记文字块': {r: sum(1 for v in flag.values() if v == r) for r in 'BCDE'}}
    log = {'删除字数': len(drop), '按规则': {r: sum(1 for i in drop if flag.get(own[i]) == r) for r in 'BCDE'}, '页脚起点y': foot_y,
           '删除内容': ''.join(P[i] for i in sorted(drop))[:400]}
    return PO.delete_chars(pg, lines, P, lid, drop), log
def layer_noise(sub, sa_dir, cfg):
    out, log = {}, {}
    for q, pg in sub.items():
        u = PO.load_units(sa_dir, int(q))
        if u is None or not pg.get('sections'): out[q] = pg; continue
        out[q], lg = noise_page(pg, u, cfg)
        if lg: log[q] = lg
    return out, log
