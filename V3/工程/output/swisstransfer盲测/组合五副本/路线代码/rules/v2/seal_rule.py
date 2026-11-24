"""印章残字清理（窄规则，只删整行，不在行内删字）：
 一行（去空白后）满足以下之一才删：
  A 整行就是印章关键词残片：可带最多 20 字的单位名前缀，以「专用章／用章／公章／监制章」结尾，且整行不含「盖、（、(、：、:」等说明性符号，
    长度 ≤ 30；例：「征税专用章」「发票专用章」「用章」「贵州XX有限公司发票专用章」。
  （曾试过规则 B：整行是本页印章识别文字里的单位名片段就删。回归发现单位名、「国家税务总局」「XX省税务局」在正文落款/票头里本来就有，会误删真正文，已弃用。）
 不删：句子里提到的「加盖公章」「（合同专用章）」「盖章」之类说明文字（带括号、冒号或「盖」字的行一律不动）。"""
import re
KW = re.compile(r'^[\u4e00-\u9fffA-Za-z0-9（）()]{0,20}?(专用章|用章|公章|监制章)$')
BAD = re.compile(r'[盖（(：:]')
def nz(s): return re.sub(r'\s', '', s or '')
def is_seal_line(line, seal_text):
    t = nz(line)
    if not t: return None
    if len(t) <= 30 and KW.match(t) and not BAD.search(t): return 'A'
    return None
def clean(pred):
    out, log = {}, []
    for p, pg in pred.items():
        secs = []
        for i, s in enumerate(pg['sections']):
            keep = []
            for ln in (s['text'] or '').split('\n'):
                r = is_seal_line(ln, pg.get('seal') or '')
                if r: log.append({'页': p, '段': i, '规则': r, '删': ln.strip()})
                else: keep.append(ln)
            secs.append({**s, 'text': '\n'.join(keep)})
        out[p] = {**pg, 'sections': secs}
    return out, log
