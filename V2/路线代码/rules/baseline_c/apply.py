"""生成变体甲/乙/丙的预测（纯本地），并记录每个区域的触发与改写。结果写 output/版面约束/variants.json。"""
import json, re, sys, copy
from pathlib import Path
sys.path.insert(0, 'output/版面约束')
from common_lc import *
import layout as L, lines as LN
from baidu_ocr.hybrid.inputs import a_chars_for_page
def is_inv(pagetext, cls): return cls == '增值税发票' or '发票号码' in pagetext
def region_span(t):
    hs = t.find('项目名称'); vm = re.search(r'价\s*税\s*合\s*计', t[hs + 1:]) if hs >= 0 else None
    if hs < 0 or not vm: return None
    ve = t.rfind('\n', 0, hs + 1 + vm.start()) + 1
    return (hs, ve) if ve > hs else None
def main():
  OUT = {}
  for s, pf, ver, sa, pdf in SETS:
      C, cls = base_C(pf, ver)
      asum = {pg['page']: pg for pg in json.load(open(Path(sa) / 'call_summary.json'))['pages']}
      V = {'甲': copy.deepcopy(C), '乙': copy.deepcopy(C), '丙': copy.deepcopy(C)}
      LOG = []
      for p in sorted(C, key=int):
          secs = C[p]['sections']; tabs = [i for i, x in enumerate(secs) if x['section'] == 'table']
          if not tabs: continue
          pt = ''.join(x['text'] for x in secs)
          ach = a_chars_for_page(Path(sa), asum[int(p)]) if int(p) in asum else []
          if not ach: LOG.append({'页': int(p), '跳过': '无 A 字框'}); continue
          ln = LN.detect(pdf, s, int(p))
          inv = is_inv(pt, cls[p])
          for i in tabs:
              t = secs[i]['text']
              for vn in ('甲', '乙'):
                  if inv and region_span(t):
                      lg = []; new = L.invoice_region(t, ach, ln, vn, lg)
                      LOG.append({'页': int(p), '段': i, '规则': '规则一', '变体': vn, **(lg[-1] if lg else {})})
                      if new is not None: V[vn][p]['sections'][i]['text'] = new
              # 丙 = 乙 + 规则二
              t2 = V['乙'][p]['sections'][i]['text']; V['丙'][p]['sections'][i]['text'] = t2
              lg = []; new = L.ruled_table(t2, ach, ln, lg, region_span(t2) if inv else None)
              LOG.append({'页': int(p), '段': i, '规则': '规则二', '变体': '丙', **(lg[-1] if lg else {})})
              if new is not None: V['丙'][p]['sections'][i]['text'] = new
          print(s, p, [ (x['规则'], x.get('变体'), x.get('结果') or x.get('跳过')) for x in LOG if x['页'] == int(p)], flush=True)
      OUT[s] = {'C': C, 'V': V, 'log': LOG}
  json.dump(OUT, open('output/版面约束/variants.json', 'w'), ensure_ascii=False, indent=1)

if __name__ == "__main__": main()
