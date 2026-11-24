#!/usr/bin/env python3
"""V2 新材料入口（离线，0 次百度调用）：由本份材料的识别缓存和上游基线C（predc.py 生成的 运行/预测/blind30_C.json），
按 V2（候选路线第三版）路线出结果：底座＝冻结路线原样（route5 combo 1,4,2 → round5_method6/tablerow gx=0.4, gy=1.6）
→ 按 配置_第三版.json 叠加各层 → 页面类型标签等写进每页 meta。

底座、各层、元数据直接调用 复跑_第三版.py 里的 base / layers / meta 与 CFG（同一份代码，不复制、不改逻辑、不改参数）；
本脚本只负责：按运行隔离目录组装输入段、核对输入、写输出。新材料没有人工参照，不计分、不与第二版对照。
缺运行隔离目录、基线C或识别缓存时中文报错、退出码 1，不写结果。

输入段与 复跑_第三版.seg_bk 同构（目录结构与 run_30.py 一致）：
  运行/预测/blind30_C.json、运行/orig/work/output/scheme_a、运行/P2/work/output/scheme_a、输入/blind30-scan.pdf、
  运行/orig/work/output/final/scheme_b_standard/blind20-scan.json、运行/reread/A_P2/results.json、运行/blind30/vertical/results.json
线缓存（物理行渲染，离线生成）与中间文件写到 运行/V2/ 下，不碰 V3 的 运行/线缓存。

用法：
  source 环境.sh
  export OCR_RUN_ISO=本份材料的运行隔离目录
  python3 出结果_新材料.py [--out V2结果.json]      # 默认 $OCR_RUN_ISO/V2结果.json
另写：<out 同名>_元数据.json（逐页 meta）、<out 同名>_规则日志.json。
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
for _k in range(0, 9):
    _s = Path(__file__).resolve().parents[_k] / 'scripts'
    if (_s / 'ocr_lab_paths.py').exists():
        sys.path.insert(0, str(_s)); break
import ocr_lab_paths as LP


def segment(iso: Path) -> dict:
    run = iso / '运行'
    WO, WP = run / 'orig/work', run / 'P2/work'
    return dict(tag='新材料', C=run / '预测/blind30_C.json', sa_o=WO / 'output/scheme_a', sa_p2=WP / 'output/scheme_a',
                pdf=iso / '输入/blind30-scan.pdf', name='blind30', lines=run / 'V2/线缓存',
                doc=WO / 'output/final/scheme_b_standard/blind20-scan.json', rd=WO / 'output/scheme_a/renders',
                rr=run / 'reread/A_P2/results.json', vert=run / 'blind30/vertical/results.json', off=0, keep=lambda p: True)


def check(sg: dict) -> None:
    E = LP.ENV_RUN_ISO
    LP.require_file(sg['C'], '上游基线C blind30_C.json（predc.py 产物，见 新材料_端到端.sh 离线）', E)
    LP.require_file(sg['pdf'], '扫描件 输入/blind30-scan.pdf', E)
    for k in ('sa_o', 'sa_p2'):
        LP.require_file(Path(sg[k]) / 'call_summary.json', f'高精度缓存 {k}/call_summary.json', E)
    LP.require_dir(sg['rd'], '原图高精度渲染图 renders/', E)
    LP.require_file(sg['doc'], '文档解析 blind20-scan.json（run_30.py build 产物）', E)
    LP.require_file(sg['rr'], '去章图重读 reread/A_P2/results.json', E)
    LP.require_file(sg['vert'], '竖排识别 blind30/vertical/results.json', E)


def main() -> int:
    ap = argparse.ArgumentParser(description='V2 新材料：识别缓存＋基线C → V2 结果（离线，不计分）')
    ap.add_argument('--out', default=None, help='输出 JSON，默认 $OCR_RUN_ISO/V2结果.json')
    a = ap.parse_args()
    try:
        iso = LP.run_iso(required=True)
        sg = segment(iso)
        check(sg)
    except LP.MissingInputError as e:
        print(e, file=sys.stderr); return 1
    out = Path(a.out) if a.out else iso / 'V2结果.json'
    sys.path.insert(0, str(HERE))
    import 复跑_第三版 as F
    # 底座子进程工作目录：原为试验机 baidu 根（其中没有 pipeline 包）。仓库根有 pipeline 包，会让 tablerow 改走仓库分支；
    # 所以设为本份材料的运行隔离目录（只有数据、不含代码），与原机一样走 tablerow 的试验机分支。
    F.ROOT = iso
    mid = iso / '运行/V2/中间'; mid.mkdir(parents=True, exist_ok=True)
    cfg = F.CFG
    sub, lg = F.layers(F.base(sg, mid), sg, cfg)
    pred, chk = {}, {}
    for q, v in sub.items():   # 与 复跑_第三版.main 相同的取页方式（off=0，全部页保留）
        if sg['keep'](int(q)):
            pred[str(int(q) + sg['off'])] = v
    for q, v in (lg.get('第三层核对') or {}).items():
        chk[str(int(q) + sg['off'])] = v
    pred = {p: pred[p] for p in sorted(pred, key=int)}
    M = F.meta(pred, chk, cfg)
    full = {p: {**v, 'meta': M[p]} for p, v in pred.items()}
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(full, open(out, 'w'), ensure_ascii=False, indent=1, default=str)
    json.dump(M, open(out.with_name(out.stem + '_元数据.json'), 'w'), ensure_ascii=False, indent=1, default=str)
    json.dump({sg['tag']: lg}, open(out.with_name(out.stem + '_规则日志.json'), 'w'), ensure_ascii=False, indent=1, default=str)
    print(f'写完 {out}：{len(full)} 页（V2 第三版路线，按配置；未计分，百度调用 0 次）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
