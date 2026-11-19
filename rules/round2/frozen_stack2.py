"""第2轮冻结路线（2026-11-19，在生成 synth_blind_i 之前写定）：
  乙′第2轮（lp2.py：乙′ ＋ F1 合计行单列 ＋ F2 区域下沿含合计行 ＋ F3 表头带/标签词表切分；不加门控）
  → 合计行切分 → 表头定位 → 行/格检测（versions.py，与第1轮相同）
  → 安全闸事后版（发票区：覆盖率≥85%＋字符多重集合不变）→ 非发票有线表格（过含 y 跨度项的完整安全闸）
  → 栏位纠正（field_correct.py 第2轮：数字型栏位取到的串后面紧跟字母/数字时不动）→ 竖排融合（fuse_vertical，最后一步）。
与第1轮冻结路线（../../冻结_新盲测前/）的差别只在 lp2.py 与 field_correct.py；final_stack.py / versions.py / fuse_vertical.py / core.py 逐字相同。
命令行：frozen_stack2.py C.json sa_dir pdf setname lines_dir doc_path renders_dir reread_path out.json
不读参照、不读 manifest。"""
import json, sys
from pathlib import Path
FD = Path(__file__).resolve().parent
sys.path.insert(0, str(FD))
import core as K, final_stack as FS, lp2
class NewSet:
    def __init__(self, C, sa_dir, pdf, name, lines_dir, fusion_paths):
        self.C, self.sa, self.pdf, self.name = C, Path(sa_dir), str(pdf), name
        self.fusion_paths = fusion_paths; self.lines_dir = Path(lines_dir)
        self.asum = {pg['page']: pg for pg in json.load(open(self.sa / 'call_summary.json'))['pages']}
        self.cls = {p: K.VB.classify(''.join(x['text'] for x in v['sections'])) for p, v in C.items()}
        self._a = {}
    def achars(self, p):
        p = int(p)
        if p not in self._a: self._a[p] = K.a_chars_for_page(self.sa, self.asum[p]) if p in self.asum else []
        return self._a[p]
    def lines(self, p):
        K.LN.D = self.lines_dir; (self.lines_dir / 'lines_cache').mkdir(parents=True, exist_ok=True); (self.lines_dir / 'renders300').mkdir(parents=True, exist_ok=True)
        return K.LN.detect(self.pdf, self.name, int(p))
    def is_inv_page(self, p): return K.VB.is_inv(''.join(x['text'] for x in self.C[p]['sections']), self.cls[p])
def run_stack(C, sa_dir, pdf, setname, lines_dir, doc_path, renders_dir, reread_path):
    import copy
    S = NewSet(C, sa_dir, pdf, setname, lines_dir, (Path(doc_path), Path(renders_dir), Path(reread_path)))
    Yp = copy.deepcopy(C); lg = []
    for p in sorted(C, key=int):
        if not S.is_inv_page(p): continue
        for i, x in enumerate(C[p]['sections']):
            if x['section'] != 'table' or not K.G.region_span(x['text']) or int(p) not in S.asum: continue
            l1 = []; new = lp2.invoice_region(x['text'], S.achars(p), S.lines(p), '乙′', l1)
            lg.append({'页': int(p), '段': i, **(l1[-1] if l1 else {})})
            if new is not None: Yp[p]['sections'][i]['text'] = new
    S.Yp = Yp
    st, LOG = FS.stack(S); LOG['乙′'] = lg
    return st, LOG
if __name__ == '__main__':
    c, sa, pdf, name, ld, dp, rd, rr, out = sys.argv[1:10]
    st, LOG = run_stack(json.load(open(c)), sa, pdf, name, ld, dp, rd, rr)
    json.dump({'pred': st['S6_竖排融合'], 'steps': st, 'log': LOG}, open(out, 'w'), ensure_ascii=False, indent=1, default=str)
