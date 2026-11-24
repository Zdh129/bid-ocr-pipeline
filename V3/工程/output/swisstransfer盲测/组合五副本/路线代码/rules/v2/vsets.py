from pathlib import Path
import sys
for _k in range(1, 9):
    _s = Path(__file__).resolve().parents[_k] / 'scripts'
    if (_s / 'ocr_lab_paths.py').exists():
        sys.path.insert(0, str(_s)); break
import ocr_lab_paths as LP
O = LP.baidu_root_path() / 'output'; TS = O / '盲测_版面约束/版面分析优化计划/试算'; BK = O / '博凯文件_最优路线测试'
# 集名: (竖排结果, 渲染目录)
VS = {
 'synth18': (O / 'f1_round2/synth18/vertical/results.json', O / 'synth18/work/output/scheme_a/renders'),
 'synth18c': (O / 'f1_round2/synth18c/vertical/results.json', O / 'synth18c/orig/work/output/scheme_a/renders'),
 'synth18d': (O / 'synth18d/synth18d/vertical/results.json', O / 'synth18d/orig/work/output/scheme_a/renders'),
 'synth18e': (O / 'synth18e/synth18e/vertical/results.json', O / 'synth18e/orig/work/output/scheme_a/renders'),
 'synth18f': (O / 'synth18f/synth18f/vertical/results.json', O / 'synth18f/orig/work/output/scheme_a/renders'),
 'synth_blind_h': (TS / '新盲测/运行_h/synth_blind_h/vertical/results.json', TS / '新盲测/运行_h/orig/work/output/scheme_a/renders'),
 'synth_blind_i': (TS / '第2轮/新盲测_i/运行_i/synth_blind_i/vertical/results.json', TS / '第2轮/新盲测_i/运行_i/orig/work/output/scheme_a/renders'),
 'synth_blind_j': (TS / '第3轮/新盲测_j/运行_j/synth_blind_j/vertical/results.json', TS / '第3轮/新盲测_j/运行_j/orig/work/output/scheme_a/renders'),
 'synth_blind_k': (TS / '第4轮/新盲测_k/运行_k/synth_blind_k/vertical/results.json', TS / '第4轮/新盲测_k/运行_k/orig/work/output/scheme_a/renders'),
 'synth_blind_l': (TS / '第5轮/新盲测_l/运行_l/synth_blind_l/vertical/results.json', TS / '第5轮/新盲测_l/运行_l/orig/work/output/scheme_a/renders'),
 'blind20': (O / 'blind20_frozen/blind20/vertical/results.json', O / 'scheme_a/renders'),
 'bk社保': (BK / '博凯包_社保重渲/运行/bk/vertical/results.json', BK / '博凯包_社保重渲/运行/orig/work/output/scheme_a/renders'),
}
