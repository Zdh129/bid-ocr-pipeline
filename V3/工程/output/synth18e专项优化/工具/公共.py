"""第四轮公共部分：读入第三版预测（评估集取第三版复跑原样输出，开发集按第三版规则现算），计分，汇总。离线，0 次百度调用。"""
import json, sys, copy, os
from pathlib import Path
ROOT = Path('/workspace/pdf-test/baidu'); O = ROOT / 'output'; W = O / 'synth18e专项优化'; W4 = O / '第四轮_版式排序与表格行列'   # W4 只读：第三版副本的预测与 v2 规则
sys.path.insert(0, str(O / '第三版_多方法测试/工具'))
import 方法 as M                      # 会把 synth18e、synth18b 的参照与坐标来源挂到框架上
F = M.F; PO = F.PO; S = F.S
sys.path.insert(0, str(W4 / '第三版副本/路线代码/rules/v2')); import page_type
EVAL = F.ALL; DEVS = ['synth18e', 'synth18b']; ALLS = EVAL + DEVS
def v3pred(s):
    if s in DEVS:
        if s == 'synth18e':   # S18E_C=1：用真实基线C重跑的第二版底座；否则沿用第三版_多方法测试 的 v1 代替版
            v2 = json.load(open(W / '开发集_基线C/synth18e_第二版.json' if os.environ.get('S18E_C') == '1' else O / '第三版_多方法测试/开发集/synth18e_第二版.json'))
        else: v2 = json.load(open(O / 'synth18b/pred_pages.json'))['G2 去掉 LLM（诊断基线，用于统计 LLM 新引入错误）']
        return F.apply_v3(s, v2, F.FULL)
    return {p: {k: v for k, v in pg.items() if k != 'meta'} for p, pg in json.load(open(W4 / '第三版副本/预测' / f'{s}.json')).items()}
def units(s, p): return F.units(s, int(p))
def text(pg): return F.text(pg)
F.CACHE = W / '工具/成绩缓存.json'
BASEC = os.environ.get('S18E_C') == '1'   # 成绩缓存放在本目录，不写回消融目录
def _score_one(args):
    s, pr = args; r = S.score(s, pr)
    return {k: r[k] for k in ('主', '严', '不计顺序', '主_逐页', '严_逐页', '不计顺序_逐页')}
F._score_one = _score_one
