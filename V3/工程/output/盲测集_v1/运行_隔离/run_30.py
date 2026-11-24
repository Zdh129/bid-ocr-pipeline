#!/usr/bin/env python3
"""【2026-11-23 盲测集_v1 30页隔离副本，不改原脚本】路线由环境变量 SWISS_ROUTE 指定（后付费 / 替代B_标准版跑去章图 / 替代A_复用原图标准版），闸门见 恢复配置.json 与 capwrap.py；页上限 1000；盲测只算『组合五＋乙1』一条路线（见 一键恢复.sh）。
swisstransfer 新页 65 页补跑（由 博凯文件_最优路线测试/优化_第4轮/江苏留出/run_bk.py 复制：只改路径、CAP=380、acc_free=0 偏保守；donghao 批准只调这 65 页）。更早：博凯包测试（由 第5轮/新盲测_l/run_l.py 复制：改路径、集名、CAP=150、verify 的新集校验改为 页对照.json；score 不用，另由 ../score_bk.py 计分）。原：第5轮 synth_blind_l 新盲测（由 第4轮/新盲测_k/run_k.py 复制：改路径与集名；verify 加第5轮整合路线冻结校验；score 改为 C / 第3轮冻结路线 / 第5轮整合路线 三行）。更早：（由 第3轮/新盲测_j/run_j.py 复制：改路径与集名；verify 加第4轮冻结说明校验；计分加 A/B 两半；CAP 100）。原说明：第3轮 synth_blind_l 新盲测（由 第2轮/新盲测_i/run_i.py 复制：改路径与集名；verify 加第3轮冻结校验；score 改为 C / 第1轮 / 第2轮 / 第3轮 四行；CAP 115）。原说明：synth_blind_l 第2轮新盲测（由 试算/新盲测/run_h.py 复制改路径；verify 加第2轮冻结校验；score 改为 C / 第1轮冻结路线 / 第2轮冻结路线 三行，路线各在子进程里跑）。原说明：synth_blind_g 盲测：基线 C（方案E＋改进⊗） vs 乙′ vs 乙′＋门控。工作目录 /workspace/pdf-test/baidu，用 .venv/bin/python。
步骤与 scripts/synth18e_run.py 相同（只少「无限量识别_原图」一步：方案E 用的是颜色去章图的无限量识别，原图那份只做对照）：
  verify            核对：方案E 冻结清单 75 个文件 sha、组合开关、冻结规则（../冻结规则/冻结记录.md 全部 sha）、新集 sha、生产配置 sha、config.yaml 不外发
  check [--live]    单页校验：一页空白校验页（不含任何测试内容）提交文档解析标准版，确认额度/计费正常；失败即停
  prep              300 DPI 渲染 → 颜色去章图 PDF（不联网）
  baidu [--live]    文档解析标准版（原图）＋无限量识别（颜色去章图）＋高精度（原图原走法）＋高精度（颜色去章图强制）
  build             原始返回 → 中间 JSON（不联网）
  reread [--live]   二次识别（颜色去章图低置信度行 → 高精度）
  vert [--live]     页边竖排三种转向识别
  score             建三行预测（不看参照）→ 写 SCORED（独占）→ 打开参照计分 → 中文报告
每一步联网前都按账本检查：本任务百度累计（文档解析页＋无限量页＋高精度/标准调用次数）+ 本步预计 ≤ 100；已完成的步骤跳过，不重发。
日志不写密钥或访问令牌。计分之前本脚本不打开 synth_blind_g 的参照（blind-ref.*）和 manifest.json。
"""
from __future__ import annotations
import argparse, copy, hashlib, json, re, subprocess, sys, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import yaml

# 【仓库副本：只改路径与加载】代码走仓库内路径；本份材料的数据目录由环境变量 OCR_RUN_ISO 指定（见 ocr_lab_paths.py）。
for _k in range(1, 9):
    _s = Path(__file__).resolve().parents[_k] / 'scripts'
    if (_s / 'ocr_lab_paths.py').exists():
        sys.path.insert(0, str(_s)); break
import ocr_lab_paths as LP
HERE = Path(__file__).resolve().parent   # 本目录（capwrap.py、stoprule.py、scheme_a_call.py 所在）
ROOT = LP.repo_root()   # 原为试验机 baidu 根：src/、scripts/、config_方案E.yaml 等代码与冻结清单
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
D = LP.baidu_root_path() / 'output' / '盲测_版面约束'
NB = LP.run_iso_path()   # 原为固定的 运行_隔离 目录；现为本份材料的运行隔离目录（数据）
SET = NB / '输入'
FZ = ROOT / 'rules' / 'baseline_c'   # 原 output/盲测_版面约束/冻结规则 的逐字节副本
OUT = NB / '运行'
WO, WP = OUT / 'orig' / 'work', OUT / 'P2' / 'work'
CFG = NB / 'config_live.yaml'
FROZEN = ROOT / 'output' / 'synth18e_prep' / 'frozen_方案E.json'
COMBO = ROOT / 'config_方案E.yaml'
EXPECTED = {'inv': 'v3', 'hdr': 'fields', 'vert': True, 'check': True}
PROD_SHA = 'a34c9d238a22214c1495ac115be2515db09f84bd13982380c446a63a4c91eaf3'
class _LazyRC(dict):
    """恢复配置.json 用到时再读（import 时不读数据）；缺文件时明确中文报错。"""
    def _load(self):
        if not dict.__len__(self):
            dict.update(self, LP.load_json(LP.run_iso(required=True) / '恢复配置.json', '运行隔离目录里的 恢复配置.json', LP.ENV_RUN_ISO))
        return self
    def __getitem__(self, k): return dict.__getitem__(self._load(), k)
    def __contains__(self, k): return dict.__contains__(self._load(), k)
RC = _LazyRC()
def _page_cap(): return int(RC['页上限'])
import os as _os
for _k in ('BAIDU_OCR_API_KEY', 'BAIDU_OCR_SECRET_KEY'):
    if _k in _os.environ:
        _os.environ[_k] = _os.environ[_k].rstrip()
ROUTE = _os.environ.get('SWISS_ROUTE', '')
CAP = 10 ** 9   # 恢复版：总量不再按旧的 380 单位把关，改由 capwrap.py 按各接口上限（估算×1.2）和后付费金额把关
PY = LP.lab_python()
NAME = 'blind30'
TS = LP.v3_root() / '工程/output/盲测_版面约束/版面分析优化计划/试算'


def jl(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def wj(p, o):
    Path(p).parent.mkdir(parents=True, exist_ok=True); Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def now(): return datetime.now(ZoneInfo('Europe/London')).strftime('%Y-%m-%d %H:%M:%S')
def redact(s):
    s = re.sub(r"access_token=[^&\s\"']+", "access_token=***", s)
    return re.sub(r"(authorization=)[^\"&) \\]+", r"\1***", s, flags=re.I)
def log(ev):
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / '调用日志.jsonl', 'a', encoding='utf-8') as f: f.write(json.dumps({'时间_伦敦': now(), **ev}, ensure_ascii=False) + '\n')


def verify():
    """隔离副本：只核对生产配置未被改、仓库默认不允许外发、方案E 冻结代码与组合开关一致、本任务扫描件 sha 相符。不改冻结目录。"""
    LP.run_iso(required=True); LP.require_file(CFG, '运行隔离目录里的 config_live.yaml', LP.ENV_RUN_ISO)
    if LP.lab_checks_enabled():   # 原机检查：生产配置 sha、原 baidu 根整树冻结代码
        BR = LP.baidu_root(required=True)
        assert sha(BR.parent / 'ocr' / 'config.yaml') == PROD_SHA, '生产配置被改过'
    assert yaml.safe_load((ROOT / 'config.yaml').read_text(encoding='utf-8'))['allow_external'] is False
    fz = jl(FROZEN)
    if LP.lab_checks_enabled():
        bad = [c for c, s in fz['code_sha256'].items() if sha(BR / c) != s]
    else:
        # 仓库布局：冻结快照 rules/baseline_c/基线C_方案E/ 须与冻结清单逐文件一致；实际运行的 .py 须与冻结一致；
        # 根目录 config.yaml 导出时去敏（只改注释和路径），核对其 postprocess 段与冻结快照相同。
        SNAP = ROOT / 'rules' / 'baseline_c' / '基线C_方案E'
        bad = [c for c, s in fz['code_sha256'].items() if not (SNAP / c).is_file() or sha(SNAP / c) != s]
        bad += [c for c, s in fz['code_sha256'].items() if c.endswith('.py') and sha(ROOT / c) != s]
        if yaml.safe_load((ROOT / 'config.yaml').read_text(encoding='utf-8'))['postprocess'] != yaml.safe_load((SNAP / 'config.yaml').read_text(encoding='utf-8'))['postprocess']:
            bad.append('config.yaml（postprocess 段与冻结快照不同）')
    assert not bad, f'方案E 冻结代码被改：{bad}'
    v = yaml.safe_load(COMBO.read_text(encoding='utf-8'))['方案E']['variant']
    assert v == fz['variant'] == EXPECTED, '方案E 组合开关与冻结不同'
    _, s = yaml.safe_load(CFG.read_text(encoding='utf-8')), None
    return v




def live_cfg():
    c = yaml.safe_load(CFG.read_text(encoding='utf-8')); s = c[NAME]
    assert c['allow_external'] is True and s.get('approval')
    assert Path(s['scan_pdf']).parent == SET and sha(s['scan_pdf']) == s['scan_sha256']
    return c, s


def ledger():
    u = {'docparser_standard_pages': 0, 'docparser_unlimited_pages': 0, 'accurate': 0, 'general': 0}
    fails = 0
    for f in sorted(OUT.glob('**/cost.json')):
        for x in jl(f).get('calls') or []:
            if x.get('dry_run'): continue
            if not x.get('success'): fails += 1; continue
            a = x['api']
            if a == 'docparser_standard': u['docparser_standard_pages'] += int(x.get('units') or 0)
            elif a == 'docparser_unlimited': u['docparser_unlimited_pages'] += int(x.get('units') or 0)
            elif a in ('accurate', 'general'): u[a] += 1
    for f in sorted(OUT.glob('**/cost_note.json')):
        u['docparser_unlimited_pages'] += int(jl(f).get('billed_pages') or 0)
    units = sum(u.values())
    acc_free = 0   # 偏保守：不扣免费额度
    cny = {'文档解析标准版（资源包 ¥9/1000页）': round(u['docparser_standard_pages'] * 0.009, 3),
           '无限量识别（限时 0.02 元/页，若有月免费额度则为 0）': round(u['docparser_unlimited_pages'] * 0.02, 3),
           '高精度含位置（超出约 82 次免费后 0.04 元/次）': round(max(0, u['accurate'] - acc_free) * 0.04, 3),
           '标准含位置（免费额度内）': 0.0}
    return {'units': units, 'detail': u, 'failed_calls': fails, 'cny_estimate': cny, 'cny_total': round(sum(cny.values()), 3)}


CAPW = str(HERE / 'capwrap.py')
sys.path.insert(0, str(HERE)); import stoprule as SR
STOPF = OUT / '停止.json'
def stop_check(name, out=None, text=''):
    """每步前后：有停止标记、或该步输出里有额度/权限/未开通类错误（不论退出码），立即结束整条流水线。"""
    if STOPF.exists():
        log({'步骤': name, '结果': '停止标记存在，整条流水线停止'}); sys.exit(f'{name}：停止标记存在（{STOPF.name}），不再调用百度')
    hit = SR.scan_dir(out) if out else None
    if not hit:
        m = re.search(r'"error_code"\s*:\s*"?(\d+)', text or '')
        if m and SR.code_stop(m.group(1)): hit = ('日志', '', m.group(1), '')
    if hit:
        wj(STOPF, {'步骤': name, '文件': hit[0], '路径': hit[1], 'error_code': str(hit[2]), 'error_msg': str(hit[3])[:200]})
        log({'步骤': name, '结果': '返回含额度/权限类错误，整条流水线停止', 'error_code': str(hit[2])})
        sys.exit(f'{name}：返回含额度/权限类错误 {hit[2]}，整条流水线停止，不进入下一步')
def actual_units():
    f = OUT / '实际单位.json'
    return json.loads(f.read_text())['已用实际单位'] if f.exists() else 0


def guard(name, expect):
    stop_check(name)
    if ROUTE not in RC['估算']: sys.exit(f'{name}：未指定或未知路线 SWISS_ROUTE={ROUTE!r}，不调用')
    if n_pages() > _page_cap(): sys.exit(f'{name}：输入 {n_pages()} 页超过页上限 {_page_cap()}，不调用')
    # 江苏留出：按 donghao 要求，每步只按「已用实际单位＋1」对比上限放行；每次真实调用再由 capwrap.py 逐次把关（竖排按实际次数计）
    led = ledger(); led['units'] = max(led['units'], actual_units()); expect = 1
    if led['units'] + expect > CAP:
        log({'步骤': name, '结果': '超上限停止', '已用': led['units'], '本步预计': expect})
        sys.exit(f'{name}：已用 {led["units"]} + 本步预计 {expect} > 上限 {CAP}，停止')
    return led


def run_logged(name, cmd):
    t0 = time.time()
    if cmd and cmd[0] == PY: cmd = [PY, CAPW] + cmd[1:]   # 所有联网步骤都经逐次闸门
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    (OUT / '日志').mkdir(parents=True, exist_ok=True)
    (OUT / '日志' / f'{name}.log').write_text(redact(r.stdout + '\n' + r.stderr), encoding='utf-8')
    log({'步骤': name, 'exit': r.returncode, '秒': round(time.time() - t0, 1), '账本': ledger()})
    return r.returncode, redact(r.stdout + r.stderr)


def n_pages():
    import fitz
    return len(fitz.open(live_cfg()[1]['scan_pdf']))


# ------------------------------------------------------------------ 单页校验
def check(a):
    verify(); live_cfg()
    out = OUT / '单页校验'
    if (out / 'raw').exists() and any((out / 'raw').glob('*.parse_result.json')):
        print('单页校验已通过，跳过'); return
    import fitz
    pdf = out / 'check_blank.pdf'
    if not pdf.exists():
        out.mkdir(parents=True, exist_ok=True)
        d = fitz.open(); pg = d.new_page(width=595.28, height=841.89)
        pg.insert_text((72, 100), 'TEST 1', fontsize=24); d.save(pdf)
    guard('单页校验', 1)
    if not a.live:
        print('[预演] 单页校验：空白页 1 页 → 文档解析标准版'); return
    rc, so = run_logged('单页校验', [PY, 'scripts/run_scheme_b.py', '--config', str(CFG), '--input', str(pdf), '--out', str(out),
                                   '--edition', 'standard', '--allow-external'])
    ok = rc == 0 and (out / 'raw').exists() and any((out / 'raw').glob('*.parse_result.json'))
    err = re.findall(r'"?error_(?:code|msg)"?\s*[:=]\s*"?([^",\n}]+)', so)
    print('单页校验', '通过' if ok else '失败', err[:4], json.dumps(ledger(), ensure_ascii=False))
    if not ok: sys.exit('单页校验失败（额度或计费问题），停止，不发测试集')


# ------------------------------------------------------------------ prep（同 synth18e_run.prep）
def prep(a):
    verify(); _, s = live_cfg()
    import cv2, fitz
    import synth18_preproc as P
    if (OUT / 'P2' / 'prep.json').exists(): print('prep 已完成'); return
    wj(OUT / '输入记录.json', {'scan_pdf': s['scan_pdf'], 'scan_sha256': s['scan_sha256'], 'recorded_at': now(), 'cfg_sha256': sha(CFG)})
    src = fitz.open(s['scan_pdf']); (OUT / 'P2' / 'img').mkdir(parents=True, exist_ok=True); doc = fitz.open()
    for i, pg in enumerate(src):
        op = OUT / 'P2' / 'img' / f'orig_p{i + 1:02d}.png'; pg.get_pixmap(dpi=300).save(op)
        img = P.p2(cv2.imread(str(op))); cv2.imwrite(str(OUT / 'P2' / 'img' / f'p{i + 1:02d}.png'), img)
        ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        np_ = doc.new_page(width=pg.rect.width, height=pg.rect.height); np_.insert_image(np_.rect, stream=buf.tobytes())
    out = OUT / 'P2' / 'blind30-P2.pdf'; doc.save(out, deflate=True)
    wj(OUT / 'P2' / 'prep.json', {'p2_pdf_sha256': sha(out), 'pages': len(doc), 'at': now()})
    print('颜色去章图 PDF', len(doc), '页')


# ------------------------------------------------------------------ 百度
def baidu(a):
    verify(); _, s = live_cfg()
    pdf = s['scan_pdf']; p2 = OUT / 'P2' / 'blind30-P2.pdf'
    assert sha(p2) == jl(OUT / 'P2' / 'prep.json')['p2_pdf_sha256']
    n = n_pages(); call_a = str(HERE / 'scheme_a_call.py')
    if n > _page_cap(): sys.exit(f'输入 {n} 页超过页上限 {_page_cap()}，不调用')
    if ROUTE not in RC['估算']: sys.exit(f'未指定或未知路线 SWISS_ROUTE={ROUTE!r}')
    ud = u_dir()
    # 上次 282005 失败留下的无限量识别目录（没有 parse_result）先存档，避免误判为已完成或被旧错误挡住
    old = WP / 'output' / 'scheme_b_unlimited'
    if ROUTE == '后付费' and a.live and not STOPF.exists() and old.exists() and not any(old.glob('raw/*.parse_result.json')):
        old.rename(old.with_name('scheme_b_unlimited_282005失败存档'))
    u_step = []
    if ROUTE == '后付费':
        u_step = [('无限量识别_颜色去章图', ud, [PY, 'scripts/run_scheme_b.py', '--config', str(CFG), '--input', str(p2), '--out', str(ud), '--edition', 'unlimited'], n)]
    elif ROUTE == '替代B_标准版跑去章图':
        u_step = [('文档解析标准版_颜色去章图_路线改变代替Unlimited', ud, [PY, 'scripts/run_scheme_b.py', '--config', str(CFG), '--input', str(p2), '--out', str(ud), '--edition', 'standard'], n)]
    steps = [('文档解析_原图', WO / 'output' / 'scheme_b_standard', [PY, 'scripts/run_scheme_b.py', '--config', str(CFG), '--input', pdf,
              '--out', str(WO / 'output' / 'scheme_b_standard'), '--edition', 'standard'], n)] + u_step + [
             ('高精度识别_原图原走法', WO / 'output' / 'scheme_a', [PY, call_a, '--config', str(CFG), '--out', str(WO / 'output' / 'scheme_a')], round(n * 1.15)),
             ('高精度识别_颜色去章图', WP / 'output' / 'scheme_a', [PY, call_a, '--config', str(CFG), '--out', str(WP / 'output' / 'scheme_a'),
              '--input', str(p2), '--force-api', 'accurate'], round(n * 1.2))]
    for name, out, cmd, exp in steps:
        done = (out / 'call_summary.json').exists() if '高精度' in name else ((out / 'raw').exists() and any((out / 'raw').glob('*.parse_result.json')))
        if done: print(name, '已完成，跳过（不重发）'); continue
        led = guard(name, exp)
        if not a.live: print('[预演]', name, '预计', exp, '| 已用', led['units']); continue
        out.mkdir(parents=True, exist_ok=True)
        rc, so = run_logged(f'百度_{name}', cmd + ['--allow-external'])
        stop_check(name, out, so)
        if rc != 0 and '无限量' in name and (out / 'raw').exists() and any((out / 'raw').glob('*.parse_result.json')):
            raw = next(out.glob('*.raw.json'))
            wj(out / 'cost_note.json', {'note': '调用成功、原始返回已落盘；本地构建中间 JSON 失败，未写 cost.json。按 billed_pages 记账，不重发。',
                                        'api': 'docparser_unlimited', 'billed_pages': jl(raw).get('billed_pages')}); rc = 0
        print(name, 'exit', rc, json.dumps(ledger(), ensure_ascii=False))
        if rc != 0: sys.exit(f'{name} 失败，停止：' + so[-600:])


def u_dir():
    """方案E 的 U 输入所在目录：后付费＝颜色去章图无限量识别；替代B＝颜色去章图文档解析标准版；替代A＝原图文档解析标准版（0 次调用）。"""
    return {'后付费': WP / 'output' / 'scheme_b_unlimited', '替代B_标准版跑去章图': WP / 'output' / 'scheme_b_std_P2',
            '替代A_复用原图标准版': WO / 'output' / 'scheme_b_standard'}[ROUTE]
def u_path(): return u_dir() / 'raw' / 'blind20-scan.parse_result.json'


def build(a):
    verify()
    if ROUTE not in RC['估算']: sys.exit('build：未指定路线 SWISS_ROUTE')
    LP.require_file(WO / 'output' / 'scheme_a' / 'call_summary.json', '原图高精度缓存 scheme_a/call_summary.json', LP.ENV_RUN_ISO)
    for _d in dict.fromkeys([WO / 'output' / 'scheme_b_standard', u_dir()]):
        if not any((_d / 'raw').glob('*.parse_result.json')):
            raise LP.MissingInputError(f'缺少文档解析识别缓存：{_d}/raw/*.parse_result.json。请先在确认费用后生成识别缓存（不会自动调用百度）。')
    import synth18_run as S
    bm = S.load_mod('blind20_build', ROOT / 'scripts' / 'blind20_build.py')
    for d in dict.fromkeys([WO / 'output' / 'scheme_b_standard', u_dir()]):
        stem = next(p.name.replace('.parse_result.json', '') for p in (d / 'raw').glob('*.parse_result.json') if not p.name.startswith('blind20'))
        import shutil
        shutil.copyfile(d / 'raw' / f'{stem}.parse_result.json', d / 'raw' / 'blind20-scan.parse_result.json')
        if (d / f'{stem}.raw.json').exists(): shutil.copyfile(d / f'{stem}.raw.json', d / 'blind20-scan.raw.json')
        for f in (d / 'raw').glob('*'):
            if f.suffix in ('.json', '.md'): f.write_text(redact(f.read_text(encoding='utf-8')), encoding='utf-8')
    W = WO / 'output'; bm.OUT = W; bm.SRC_NAME = Path(live_cfg()[1]['scan_pdf']).name
    a_sum = jl(W / 'scheme_a' / 'call_summary.json')
    bm.build_scheme_a(W / 'scheme_a', W / 'final' / 'scheme_a', 'routed')
    bm.build_scheme_b(W / 'scheme_b_standard', W / 'final' / 'scheme_b_standard', 'standard', a_sum)
    print('built')


def b_doc(): return WO / 'output' / 'final' / 'scheme_b_standard' / 'blind20-scan.json'


def _sub(a, name, cmd, exp, done):
    verify(); live_cfg()
    if Path(done).exists(): print(name, '已完成，跳过（不重发）'); return
    led = guard(name, exp)
    if not a.live: print('[预演]', name, '预计', exp, '| 已用', led['units']); return
    rc, so = run_logged(name, cmd + ['--live', '--allow-external'])
    stop_check(name, None, so)
    print(name, 'exit', rc, json.dumps(ledger(), ensure_ascii=False))
    if rc != 0: sys.exit(f'{name} 失败，停止：' + so[-600:])


def reread(a):
    out = OUT / 'reread' / 'A_P2'
    _sub(a, '二次识别', [PY, 'scripts/opt2_reread.py', '--a-dir', str(WP / 'output' / 'scheme_a'), '--b-doc', str(b_doc()), '--out', str(out),
                      '--config', str(CFG), '--label', 'bk-reread'], round(n_pages() * 0.4), out / 'results.json')


def vert(a):
    out = OUT / NAME / 'vertical'
    _sub(a, '页边竖排识别', [PY, 'scripts/f1r2_vert_call.py', '--b-doc', str(b_doc()), '--render-dir', str(WO / 'output' / 'scheme_a' / 'renders'),
                        '--out', str(out), '--config', str(CFG), '--label', 'bk-vertical'], n_pages(), out / 'results.json')


# ------------------------------------------------------------------ 计分
def score(a):
    sys.exit('旧的三行计分（C／第3轮／第5轮）已停用：盲测只算组合五＋乙1，见 一键恢复.sh 与 盲测成绩.py')
    """C（方案E＋改进⊗，含真实页边竖排识别）/ 第3轮冻结路线 / 第5轮整合路线（../整合路线/冻结/route5.py，combo 1,4,2）三行；
    先生成全部预测并记 sha，再写 SCORED（独占）、打开参照，一次计分。两条路线各在子进程里跑（模块同名，避免互相覆盖）。"""
    variant = verify()
    if (OUT / 'SCORED').exists(): sys.exit('已计过分，拒绝再跑（盲测只计一次）')
    import f1r2 as F
    sys.path.insert(0, str(FZ))
    import variants_blind as VB
    entry = {'dir': SET, 'b': b_doc(), 'root': WO, 'a': WP / 'output' / 'scheme_a',
             'u': WP / 'output' / 'scheme_b_unlimited' / 'raw' / 'blind20-scan.parse_result.json',
             'reread': OUT / 'reread' / 'A_P2' / 'results.json', 'render': WO / 'output' / 'scheme_a' / 'renders',
             'llm_caches': [], 'llm_cfg': None, 'llm_spent_before': 0.0, 'blind': True,
             'a_orig_summary': WO / 'output' / 'scheme_a' / 'call_summary.json'}
    assert (OUT / NAME / 'vertical' / 'results.json').exists(), '缺页边竖排结果'
    F.OUT = OUT; F.DS[NAME] = entry
    c = F.Ctx(NAME)
    E, cnt, lg = c.run(variant, llm=None)
    wj(OUT / 'pred_方案E.json', {'方案E': E}); E = jl(OUT / 'pred_方案E.json')['方案E']
    C = VB.base_C(E); wj(OUT / 'pred_C.json', C)
    common = [str(OUT / 'pred_C.json'), str(WO / 'output' / 'scheme_a'), live_cfg()[1]['scan_pdf'], NAME]
    tail = [str(b_doc()), str(WO / 'output' / 'scheme_a' / 'renders'), str(OUT / 'reread' / 'A_P2' / 'results.json')]
    Z5 = TS / '第5轮' / '整合路线' / '冻结'
    sa_o, sa_p2 = str(WO / 'output' / 'scheme_a'), str(WP / 'output' / 'scheme_a')
    jobs = (('R3', [PY, str(TS / '第3轮' / '冻结' / 'frozen_stack3.py')] + common + ['{ld}'] + tail + [str(OUT / 'pred_R3.json')]),
            ('R5', [PY, str(Z5 / 'route5.py'), str(OUT / 'pred_C.json'), sa_o, sa_p2, live_cfg()[1]['scan_pdf'], NAME, '{ld}'] + tail + [str(OUT / 'pred_R5.json'), '1,4,2']))
    for tag, cmd in jobs:
        ld = OUT / f'表格线缓存_{tag}'; (ld / 'renders300').mkdir(parents=True, exist_ok=True); (ld / 'lines_cache').mkdir(parents=True, exist_ok=True)
        cmd = [str(ld) if x == '{ld}' else x for x in cmd]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT,
                           env={**__import__('os').environ, 'PYTHONDONTWRITEBYTECODE': '1', 'R5_MODS': str(Z5)})
        (OUT / '日志').mkdir(parents=True, exist_ok=True); (OUT / '日志' / f'路线_{tag}.log').write_text(redact(r.stdout + r.stderr), encoding='utf-8')
        assert r.returncode == 0, f'路线 {tag} 失败，见 日志/路线_{tag}.log'
    R3, R5 = jl(OUT / 'pred_R3.json'), jl(OUT / 'pred_R5.json')
    wj(OUT / '整合路线改写日志.json', R5.get('log'))
    V = {'C': C, '第3轮冻结路线': R3['pred'], '第5轮整合路线': R5['pred']}
    wj(OUT / 'pred_全部.json', V)
    pred_sha = sha(OUT / 'pred_全部.json')
    # ---- 到这里才打开参照
    with open(OUT / 'SCORED', 'x') as f: f.write(now() + ' pred_sha256=' + pred_sha)
    import synth18b_score as SB, tolerant_score as T
    bc, bs, hs, refs, rj, mp, rng = SB.load_bs(SET)
    inv = sorted(p for p in mp if mp[p]['doc_type'] == '发票')
    K = ('ref_chars', 'matches', 'deletions', 'substitutions', 'insertions')
    E_ = lambda o: o['deletions'] + o['substitutions'] + o['insertions']
    res = {}
    for n, pr in V.items():
        tol = T.score_tolerant(bs, pr, rj, mp, 'in'); st_ = bs.score_system(pr, refs['full'], mp, None)
        agg = lambda sc, pages: {k: sum(sc['per_page'][p]['overall'][k] for p in pages) for k in K}
        non = [p for p in tol['per_page'] if p not in inv]
        res[n] = {'主口径_全套': agg(tol, list(tol['per_page'])), '主口径_发票页': agg(tol, inv), '主口径_非发票页': agg(tol, non),
                  '严格_全套': agg(st_, list(st_['per_page'])), '严格_发票页': agg(st_, inv), '严格_非发票页': agg(st_, non),
                  '主口径_逐页': {p: E_(tol['per_page'][p]['overall']) for p in tol['per_page']},
                  '严格_逐页': {p: E_(st_['per_page'][p]['overall']) for p in st_['per_page']}}
    halves = {'A_同分布': [p for p in inv if '【l-同分布】' in mp[p].get('description', '')], 'B_扩展': [p for p in inv if '【l-扩展】' in mp[p].get('description', '')]}
    if sorted(halves['A_同分布'] + halves['B_扩展']) != sorted(inv): print('两半划分与发票页不一致（只记录，不影响主结果）', halves)
    for n, pr in V.items():
        tol = T.score_tolerant(bs, pr, rj, mp, 'in'); st_ = bs.score_system(pr, refs['full'], mp, None)
        for h, pages in halves.items():
            res[n]['主口径_' + h] = {k: sum(tol['per_page'][p]['overall'][k] for p in pages) for k in K}
            res[n]['严格_' + h] = {k: sum(st_['per_page'][p]['overall'][k] for p in pages) for k in K}
    wj(OUT / 'scores.json', {'计分时间_伦敦': now(), 'pred_sha256': pred_sha, '发票页': inv, '两半': halves, '百度账本': ledger(), '方案E步骤': cnt, '结果': res})
    print(json.dumps({n: {k: E_(v) for k, v in r.items() if not k.endswith('逐页')} for n, r in res.items()}, ensure_ascii=False))

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['verify', 'check', 'prep', 'baidu', 'build', 'reread', 'vert', 'score', 'ledger', 'upath'])
    ap.add_argument('--live', action='store_true')
    a = ap.parse_args()
    try:   # 缺运行隔离目录、配置或识别缓存时：中文报错、退出码 1，不写成绩
        LP.run_iso(required=True)
        if a.cmd == 'verify': print('校验通过', verify())
        elif a.cmd == 'ledger': print(json.dumps(ledger(), ensure_ascii=False, indent=1))
        elif a.cmd == 'upath': print(u_path())
        else: globals()[a.cmd](a)
    except LP.MissingInputError as e:
        print(e, file=sys.stderr); sys.exit(1)
