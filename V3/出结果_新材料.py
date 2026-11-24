#!/usr/bin/env python3
"""V3 新材料入口（离线，0 次百度调用）：由上游两份中间结果（第六轮新版.json、整合版.json）和识别缓存，
按 v6 候选（方向1＋方向5a，与 评分.py 的「1+5a」完全相同的 公共v6.v6 调用）出 V3 最终结果。

每页的输入组装与 公共v3._set25 相同（integ/six/sa_o/sa_p2/doc），只是页号取自中间结果里实际有的页，而不是固定 1～30。
本脚本不计分（新材料没有人工参照）。缺运行隔离目录、中间结果或缓存时中文报错、退出码 1，不写结果。

用法：
  source 环境.sh
  export OCR_RUN_ISO=本份材料的运行隔离目录          # 含 运行/（识别缓存与 build 产物）
  export OCR_RESULT_DIR=中间结果目录                 # 可选；默认 $OCR_RUN_ISO/../识别结果
  python3 出结果_新材料.py --out V3结果.json [--rules 1+5a]
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE / '代码/整合版优化/v3优化/v6探索/工具'), str(HERE.parent / 'scripts')]
import ocr_lab_paths as LP


def build_pages(iso: Path, res: Path):
    run = iso / '运行'
    sa_o, sa_p2 = run / 'orig/work/output/scheme_a', run / 'P2/work/output/scheme_a'
    LP.require_file(sa_o / 'call_summary.json', '原图高精度缓存 scheme_a/call_summary.json', LP.ENV_RUN_ISO)
    LP.require_file(sa_p2 / 'call_summary.json', '去章图高精度缓存 scheme_a/call_summary.json', LP.ENV_RUN_ISO)
    DOC = LP.load_json(run / 'orig/work/output/final/scheme_b_standard/pred_pages.json', '文档解析 pred_pages.json（run_30.py build 产物）', LP.ENV_RUN_ISO)
    six = LP.load_json(res / '第六轮新版.json', '中间结果 第六轮新版.json', LP.ENV_RESULT_DIR)
    integ = LP.load_json(res / '整合版.json', '中间结果 整合版.json', LP.ENV_RESULT_DIR)
    if sorted(six, key=int) != sorted(integ, key=int):
        raise LP.MissingInputError('第六轮新版.json 与 整合版.json 的页号不一致，请用同一份识别缓存重新生成两份中间结果。')
    return [dict(k=str(p), q=p, integ=integ[str(p)], six=six[str(p)], sa_o=sa_o, sa_p2=sa_p2, doc=DOC.get(str(p)))
            for p in sorted(int(k) for k in six)]


def main() -> int:
    ap = argparse.ArgumentParser(description='V3 新材料：中间结果 → V3 最终结果（离线）')
    ap.add_argument('--out', required=True, help='输出 JSON')
    ap.add_argument('--rules', default='1+5a', help="规则组合，默认 1+5a（V3 冻结组合）；'v5' 为不加规则的 v5 底座")
    a = ap.parse_args()
    try:
        iso = LP.run_iso(required=True)
        res = LP.result_dir_path()
        pages = build_pages(iso, res)
    except LP.MissingInputError as e:
        print(e, file=sys.stderr); return 1
    import 公共v6 as C
    rules = () if a.rules == 'v5' else tuple(a.rules.split('+'))
    pred, route = {}, {}
    fn = (lambda d: C.v6(d, rules)) if rules else (lambda d: C.F.v5(d, **C.P5))
    for d in pages:   # 与 公共v3.run 相同的取值方式
        o = fn(d)
        if isinstance(o, tuple): pred[d['k']], route[d['k']] = o
        else: pred[d['k']], route[d['k']] = o, '第六轮'
    out = {'规则': a.rules, '页数': len(pred), '路线': route, 'pred': pred, '规则日志': C.日志}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(f'写完 {a.out}：{len(pred)} 页，走第六轮 {sum(v == "第六轮" for v in route.values())} 页，规则触发 {len(C.日志)} 条')
    return 0


if __name__ == '__main__':
    sys.exit(main())
