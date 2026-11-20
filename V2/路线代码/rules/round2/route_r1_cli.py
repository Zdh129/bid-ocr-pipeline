"""命令行包装：调用第1轮冻结路线 ../冻结_新盲测前/frozen_stack.run_stack（不改它）。参数同 冻结/frozen_stack2.py。"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / '冻结_新盲测前'))
import frozen_stack as Z
c, sa, pdf, name, ld, dp, rd, rr, out = sys.argv[1:10]
st, LOG = Z.run_stack(json.load(open(c)), sa, pdf, name, ld, dp, rd, rr)
json.dump({'pred': st['S6_竖排融合'], 'steps': {k: v for k, v in st.items()}, 'log': LOG}, open(out, 'w'), ensure_ascii=False, indent=1, default=str)
