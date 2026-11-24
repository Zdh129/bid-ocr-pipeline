"""方案E → 基线C（离线，不联网，不读参照）。与 江苏留出/make_preds.py 前半段相同。
用法：python predc.py 运行目录 U路径 输出目录 [名字]
  运行目录：含 orig/work、P2/work、reread/A_P2/results.json、bk/vertical/results.json
  U路径：Unlimited（或替代路线的文档解析）原始返回 parse_result.json"""
import json, sys
from pathlib import Path
sys.dont_write_bytecode = True
ROOT = Path('/workspace/pdf-test/baidu'); NB = Path('/workspace/pdf-test/baidu/output/盲测集_v1/运行_隔离')
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(NB)]
import run_30 as RB
RUN, U, OUTD = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]); NAMEP = sys.argv[4] if len(sys.argv) > 4 else 'bk'
variant = RB.verify()
import f1r2 as F
sys.path.insert(0, str(RB.FZ)); import variants_blind as VB
WO, WP = RUN / 'orig/work', RUN / 'P2/work'
entry = {'dir': None, 'b': WO / 'output/final/scheme_b_standard/blind20-scan.json', 'root': WO, 'a': WP / 'output/scheme_a', 'u': U,
         'reread': RUN / 'reread/A_P2/results.json', 'render': WO / 'output/scheme_a/renders', 'llm_caches': [], 'llm_cfg': None, 'llm_spent_before': 0.0,
         'blind': True, 'a_orig_summary': WO / 'output/scheme_a/call_summary.json'}
F.OUT = RUN; F.DS[NAMEP] = entry
E, cnt, lg = F.Ctx(NAMEP).run(variant, llm=None)
OUTD.mkdir(parents=True, exist_ok=True)
json.dump({'方案E': E}, open(OUTD / f'{NAMEP}_方案E.json', 'w'), ensure_ascii=False, indent=1)
C = VB.base_C(json.load(open(OUTD / f'{NAMEP}_方案E.json'))['方案E']); json.dump(C, open(OUTD / f'{NAMEP}_C.json', 'w'), ensure_ascii=False, indent=1)
json.dump({'方案E步骤': cnt, 'U': str(U)}, open(OUTD / f'{NAMEP}_方案E步骤.json', 'w'), ensure_ascii=False, indent=1, default=str)
print('C 页数', len(C))
