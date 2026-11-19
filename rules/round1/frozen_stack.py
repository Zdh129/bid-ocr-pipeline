"""冻结路线（2026-11-19，在生成 synth_blind_h、打开 synth18f 任何新输出之前写定）：
  乙′（不加门控）→ 合计行切分 → 表头定位 → 行/格检测 → 安全闸事后版（发票区：覆盖率≥85%＋字符多重集合不变）
  → 非发票有线表格（ruled_table，过含 y 跨度项的完整安全闸）→ 栏位纠正（field_correct）→ 竖排融合（fuse_vertical，最后一步）。
与 试算/最终叠加/final_stack.py 的 stack() 逐步相同（本目录副本只改了导入路径和新集的缓存路径入口）。
入口：run_stack(C, sa_dir, pdf, setname, lines_dir, doc_path, renders_dir, reread_path) → (各步预测, 日志)。
乙′ 用冻结规则/variants_blind.make_variants 现算（与盲测 synth_blind_g 时同一函数）。不读参照、不读 manifest。"""
import json, sys
from pathlib import Path
FD = Path(__file__).resolve().parent
sys.path.insert(0, str(FD))
import core as K, final_stack as FS
class NewSet:
    def __init__(self, C, Yp, sa_dir, pdf, name, lines_dir, fusion_paths):
        self.C, self.Yp, self.sa, self.pdf, self.name = C, Yp, Path(sa_dir), str(pdf), name
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
    V, VLOG = K.VB.make_variants(C, Path(sa_dir), str(pdf), setname, Path(lines_dir))
    S = NewSet(C, V['乙′'], sa_dir, pdf, setname, lines_dir, (Path(doc_path), Path(renders_dir), Path(reread_path)))
    st, LOG = FS.stack(S)
    LOG['乙′'] = VLOG; st['乙′＋门控（参考）'] = V['乙′＋门控']
    return st, LOG
