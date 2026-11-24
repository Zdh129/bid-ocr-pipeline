"""综合测试 2776d4b：八套输入（全部只读缓存）。线缓存复制到本目录 线缓存/<集>（本地 pdftoppm+OpenCV 结果，与百度无关）。"""
from pathlib import Path
import sys as _sys
for _k in range(1, 9):
    _s = Path(__file__).resolve().parents[_k] / 'scripts'
    if (_s / 'ocr_lab_paths.py').exists():
        _sys.path.insert(0, str(_s)); break
import ocr_lab_paths as LP
O = LP.baidu_root_path() / 'output'; TS = O / '盲测_版面约束/版面分析优化计划/试算'; HERE = Path(__file__).resolve().parent
def run_set(R, name, pdf, ref, C=None):
    return dict(name=name, C=str(C or R / 'pred_C.json'), sa_o=str(R / 'orig/work/output/scheme_a'), sa_p2=str(R / 'P2/work/output/scheme_a'), pdf=str(pdf),
                doc=str(R / 'orig/work/output/final/scheme_b_standard/blind20-scan.json'), rd=str(R / 'orig/work/output/scheme_a/renders'),
                rr=str(R / 'reread/A_P2/results.json'), ref=str(ref))
I, J, K, L = TS / '第2轮/新盲测_i/运行_i', TS / '第3轮/新盲测_j/运行_j', TS / '第4轮/新盲测_k/运行_k', TS / '第5轮/新盲测_l/运行_l'
SETS = {
 'synth18f': {**run_set(O / 'synth18f', 'synth18f', str(LP.pdf_test_root_path() / 'synth18f/synth18f-scan.pdf'), str(LP.pdf_test_root_path() / 'synth18f'), C=TS / '第3轮/正式计分_18f/pred_C.json'),
              'r3_hist': TS / '第3轮/正式计分_18f/pred_R3.json', 'lines_src': TS / '第5轮/整合路线/开发/线缓存_18f', 'grp': '开发集'},
 'synth18': dict(name='synth18', C=str(HERE / '输入/C_synth18.json'), sa_o=str(O / 'synth18/work/output/scheme_a'), sa_p2=str(O / 'synth18_opt2/P2/work/output/scheme_a'),
                 pdf=str(LP.pdf_test_root_path() / 'synth18/synth18-scan.pdf'), doc=str(O / 'synth18/work/output/final/scheme_b_standard/blind20-scan.json'),
                 rd=str(O / 'synth18/work/output/scheme_a/renders'), rr=str(O / 'synth18_opt2/reread/A_P2/results.json'), ref=str(LP.pdf_test_root_path() / 'synth18'),
                 r3_hist=TS / '第3轮/dev/结果/全部3_synth18_pred.json', lines_src=TS / '表格线缓存', grp='开发集'),
 'synth18c': {**run_set(O / 'synth18c', 'synth18c', str(LP.pdf_test_root_path() / 'synth18c/synth18c-scan.pdf'), str(LP.pdf_test_root_path() / 'synth18c'), C=HERE / '输入/C_synth18c.json'),
              'r3_hist': TS / '第3轮/dev/结果/全部3_synth18c_pred.json', 'lines_src': TS / '表格线缓存', 'grp': '开发集'},
 'synth18d': {**run_set(O / 'synth18d', 'synth18d', str(LP.pdf_test_root_path() / 'synth18d/synth18d-scan.pdf'), str(LP.pdf_test_root_path() / 'synth18d'), C=HERE / '输入/C_synth18d.json'),
              'r3_hist': TS / '第3轮/dev/结果/全部3_synth18d_pred.json', 'lines_src': TS / '表格线缓存', 'grp': '开发集'},
 'synth_blind_i': {**run_set(I, 'synth_blind_i', TS / '第2轮/新盲测_i/synth_blind_i/synth_blind_i-scan.pdf', TS / '第2轮/新盲测_i/synth_blind_i'),
                   'r3_hist': None, 'lines_src': I / '表格线缓存_R2', 'grp': '盲测集'},
 'synth_blind_j': {**run_set(J, 'synth_blind_j', TS / '第3轮/新盲测_j/synth_blind_j/synth_blind_j-scan.pdf', TS / '第3轮/新盲测_j/synth_blind_j'),
                   'r3_hist': J / 'pred_R3.json', 'lines_src': TS / '第5轮/整合路线/开发/线缓存_j', 'grp': '盲测集'},
 'synth_blind_k': {**run_set(K, 'synth_blind_k', TS / '第4轮/新盲测_k/synth_blind_k/synth_blind_k-scan.pdf', TS / '第4轮/新盲测_k/synth_blind_k'),
                   'r3_hist': K / 'pred_R3.json', 'lines_src': TS / '第5轮/整合路线/开发/线缓存_k', 'grp': '盲测集'},
 'synth_blind_l': {**run_set(L, 'synth_blind_l', TS / '第5轮/新盲测_l/synth_blind_l/synth_blind_l-scan.pdf', TS / '第5轮/新盲测_l/synth_blind_l'),
                   'r3_hist': L / 'pred_R3.json', 'lines_src': L / '表格线缓存_R5', 'grp': '盲测集'},
}
for s, c in SETS.items(): c['lines'] = str(HERE / '线缓存' / s)
EXIST_R5 = {'synth_blind_k': TS / '第5轮/整合路线/开发/k_1,4,2.json', 'synth_blind_l': L / 'pred_R5.json', 'synth_blind_j': TS / '第5轮/整合路线/开发/j_1,4,2.json', 'synth18f': TS / '第5轮/整合路线/开发/f_1,4,2.json'}
EXIST_R6 = {'synth_blind_k': 'k', 'synth_blind_l': 'l', 'synth_blind_j': 'j', 'synth18f': 'f'}
