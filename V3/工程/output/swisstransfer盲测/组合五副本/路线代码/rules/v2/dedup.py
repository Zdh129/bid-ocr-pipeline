"""页边竖排重复读出清理（窄规则）：只看有竖排条的页。
 设本页竖排条的所有缓存读法（三向 + 二次识别）里，被本页预测采用的那一条为 V（预测里有一段与某读法相同或相似度≥0.8）。
 若本页另有一整段 t（去符号后 2–(|V|+3) 字，不含换行）与 V 的最佳子串相似度 ≥0.5 且与 V 不同，并且 t 本身不是更好的读法（不在任何读法里出现为整段），
 则认为它是同一竖排条被正文识别横着读出的乱码重复，删掉 t。"""
import json, re
from difflib import SequenceMatcher
nz = lambda s: re.sub(r'[\s:：,，;；.。()（）\-_|]', '', s or '')
def psim(a, b):
    if not a or not b: return 0.0
    if len(a) > len(b): a, b = b, a
    return max(SequenceMatcher(None, a, b[i:i + len(a)]).ratio() for i in range(len(b) - len(a) + 1))
def dedup(pred, units):
    out, log = json.loads(json.dumps(pred)), []
    by = {}
    for u in units: by.setdefault(str(u['page']), []).append(u)
    for p, us in by.items():
        if p not in out: continue
        secs = out[p]['sections']; texts = [nz(s['text']) for s in secs]
        reads = {nz(r['text']) for u in us for r in (u.get('reads') or {}).values() if r and r.get('text')}
        reads.discard('')
        V = [i for i, t in enumerate(texts) if len(t) >= 3 and '\n' not in secs[i]['text'].strip() and any(t == r or (SequenceMatcher(None, t, r).ratio() >= 0.8) for r in reads)]
        drop = set()
        for vi in V:
            v = texts[vi]
            for j, t in enumerate(texts):
                if j == vi or j in V or j in drop or '\n' in secs[j]['text'].strip(): continue
                if 2 <= len(t) <= len(v) + 3 and t != v and psim(t, v) >= 0.5 and not re.search(r'[\u4e00-\u9fff]{4,}', t.replace(v[:4], '')):
                    drop.add(j); log.append({'页': p, '删': secs[j]['text'], '保留竖排': secs[vi]['text'], '相似': round(psim(t, v), 2)})
        out[p]['sections'] = [s for i, s in enumerate(secs) if i not in drop]
    return out, log
