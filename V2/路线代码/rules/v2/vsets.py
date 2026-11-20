from pathlib import Path

def _dir(parent, preferred, must, must_not=()):
    """优先使用中性目录名；若不存在，则按子路径标记在同级目录中定位。
    这样本地试验目录可以继续使用历史名称，不必改名，也不改识别逻辑。"""
    parent = Path(parent)
    pref = parent / preferred
    if pref.exists():
        return pref
    if parent.is_dir():
        hits = [p for p in sorted(parent.iterdir())
                if p.is_dir()
                and all((p / m).exists() for m in must)
                and not any((p / m).exists() for m in must_not)]
        if hits:
            return hits[0]
    return pref

O = Path('/workspace/pdf-test/baidu/output'); TS = O / '盲测_版面约束/版面分析优化计划/试算'
BK = _dir(O, '测试样本A文件_最优路线测试',
          ['优化_第3轮/留出验证/冻结规则', '优化_第4轮/江苏留出'])
BK_PACK = _dir(BK, '测试样本A包', ['输入/bk-scan.pdf', '参照'])
BK_SOCIAL = _dir(BK, '测试样本A包_社保重渲', ['输入/bk-scan.pdf', '运行'], ['参照'])
TOOLS = _dir(O, '主测试集清零', ['工具/计分.py'])
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
 'bk社保': (BK_SOCIAL / '运行/bk/vertical/results.json', BK_SOCIAL / '运行/orig/work/output/scheme_a/renders'),
}
