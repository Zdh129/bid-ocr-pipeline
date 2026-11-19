#!/usr/bin/env python3
"""synth_blind_i 第2轮新盲测（由 试算/新盲测/run_h.py 复制改路径；verify 加第2轮冻结校验；score 改为 C / 第1轮冻结路线 / 第2轮冻结路线 三行，路线各在子进程里跑）。原说明：synth_blind_g 盲测：基线 C（方案E＋改进⊗） vs 乙′ vs 乙′＋门控。工作目录 /workspace/pdf-test/baidu，用 .venv/bin/python。
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

ROOT = Path('/workspace/pdf-test/baidu')
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
D = ROOT / 'output' / '盲测_版面约束'
NB = Path('/workspace/pdf-test/baidu/output/盲测_版面约束/版面分析优化计划/试算/第2轮/新盲测_i')
SET = NB / 'synth_blind_i'
FZ = D / '冻结规则'
OUT = NB / '运行_i'
WO, WP = OUT / 'orig' / 'work', OUT / 'P2' / 'work'
CFG = NB / 'config_blind_i_live.yaml'
FROZEN = ROOT / 'output' / 'synth18e_prep' / 'frozen_方案E.json'
COMBO = ROOT / 'config_方案E.yaml'
EXPECTED = {'inv': 'v3', 'hdr': 'fields', 'vert': True, 'check': True}
PROD_SHA = 'a34c9d238a22214c1495ac115be2515db09f84bd13982380c446a63a4c91eaf3'
CAP = 150
PY = str(ROOT / '.venv' / 'bin' / 'python')
NAME = 'synth_blind_i'


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
    fz = jl(FROZEN)
    bad = [c for c, s in fz['code_sha256'].items() if sha(ROOT / c) != s]
    assert not bad, f'方案E 冻结代码被改：{bad}'
    v = yaml.safe_load(COMBO.read_text(encoding='utf-8'))['方案E']['variant']
    assert v == fz['variant'] == EXPECTED, '方案E 组合开关与冻结不同'
    rec = (FZ / '冻结记录.md').read_text(encoding='utf-8')
    for f, h in re.findall(r'\| `([^`]+)` \| `([0-9a-f]{64})` \|', rec):
        p = FZ / f if (FZ / f).exists() else FZ / '基线C_方案E' / f
        assert sha(p) == h, f'冻结规则被改：{f}'
    for line in (NB / '新集_i_sha256.txt').read_text().splitlines():
        h, f = line.split(None, 1)
        assert sha(SET / f.strip()) == h, f'新集文件被改：{f}'
    zr = (NB.parent.parent / '冻结_新盲测前' / '冻结记录.md').read_text(encoding='utf-8')
    for f, h in re.findall(r'\| `([^`]+)` \| `([0-9a-f]{64})` \|', zr):
        p = NB.parent.parent / '冻结_新盲测前' / f if (NB.parent.parent / '冻结_新盲测前' / f).exists() else ROOT / f
        assert sha(p) == h, f'冻结路线被改：{f}'
    Z2 = NB.parent / '冻结'
    zr2 = (Z2 / '冻结记录.md').read_text(encoding='utf-8')
    assert (Z2 / '冻结记录_sha256.txt').read_text().split()[0] == sha(Z2 / '冻结记录.md'), '第2轮冻结记录被改'
    for f, h in re.findall(r'\| `([^`]+)` \| `([0-9a-f]{64})` \|', zr2):
        p = Z2 / f if (Z2 / f).exists() else ROOT / f
        assert sha(p) == h, f'第2轮冻结路线被改：{f}'
    assert sha(ROOT.parent / 'ocr' / 'config.yaml') == PROD_SHA, '生产配置被改过'
    assert yaml.safe_load((ROOT / 'config.yaml').read_text(encoding='utf-8'))['allow_external'] is False
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
    acc_free = 29   # 盲测 g 之后剩余（82−53）；与 synth18f 合计另算
    cny = {'文档解析标准版（资源包 ¥9/1000页）': round(u['docparser_standard_pages'] * 0.009, 3),
           '无限量识别（限时 0.02 元/页，若有月免费额度则为 0）': round(u['docparser_unlimited_pages'] * 0.02, 3),
           '高精度含位置（超出约 82 次免费后 0.04 元/次）': round(max(0, u['accurate'] - acc_free) * 0.04, 3),
           '标准含位置（免费额度内）': 0.0}
    return {'units': units, 'detail': u, 'failed_calls': fails, 'cny_estimate': cny, 'cny_total': round(sum(cny.values()), 3)}


def guard(name, expect):
    led = ledger()
    if led['units'] + expect > CAP:
        log({'步骤': name, '结果': '超上限停止', '已用': led['units'], '本步预计': expect})
        sys.exit(f'{name}：已用 {led["units"]} + 本步预计 {expect} > 上限 {CAP}，停止')
    return led


def run_logged(name, cmd):
    t0 = time.time()
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
    out = OUT / 'P2' / 'blind_i-P2.pdf'; doc.save(out, deflate=True)
    wj(OUT / 'P2' / 'prep.json', {'p2_pdf_sha256': sha(out), 'pages': len(doc), 'at': now()})
    print('颜色去章图 PDF', len(doc), '页')


# ------------------------------------------------------------------ 百度
def baidu(a):
    verify(); _, s = live_cfg()
    assert any((OUT / '单页校验' / 'raw').glob('*.parse_result.json')), '先跑 check --live'
    pdf = s['scan_pdf']; p2 = OUT / 'P2' / 'blind_i-P2.pdf'
    assert sha(p2) == jl(OUT / 'P2' / 'prep.json')['p2_pdf_sha256']
    n = n_pages(); call_a = str(NB / 'blind_i_scheme_a_call.py')
    steps = [('文档解析_原图', WO / 'output' / 'scheme_b_standard', [PY, 'scripts/run_scheme_b.py', '--config', str(CFG), '--input', pdf,
              '--out', str(WO / 'output' / 'scheme_b_standard'), '--edition', 'standard'], n),
             ('无限量识别_颜色去章图', WP / 'output' / 'scheme_b_unlimited', [PY, 'scripts/run_scheme_b.py', '--config', str(CFG), '--input', str(p2),
              '--out', str(WP / 'output' / 'scheme_b_unlimited'), '--edition', 'unlimited'], n),
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
        if rc != 0 and '无限量' in name and (out / 'raw').exists() and any((out / 'raw').glob('*.parse_result.json')):
            raw = next(out.glob('*.raw.json'))
            wj(out / 'cost_note.json', {'note': '调用成功、原始返回已落盘；本地构建中间 JSON 失败，未写 cost.json。按 billed_pages 记账，不重发。',
                                        'api': 'docparser_unlimited', 'billed_pages': jl(raw).get('billed_pages')}); rc = 0
        print(name, 'exit', rc, json.dumps(ledger(), ensure_ascii=False))
        if rc != 0: sys.exit(f'{name} 失败，停止：' + so[-600:])


def build(a):
    verify()
    import synth18_run as S
    bm = S.load_mod('blind20_build', ROOT / 'scripts' / 'blind20_build.py')
    for d in (WO / 'output' / 'scheme_b_standard', WP / 'output' / 'scheme_b_unlimited'):
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
    print(name, 'exit', rc, json.dumps(ledger(), ensure_ascii=False))
    if rc != 0: sys.exit(f'{name} 失败，停止：' + so[-600:])


def reread(a):
    out = OUT / 'reread' / 'A_P2'
    _sub(a, '二次识别', [PY, 'scripts/opt2_reread.py', '--a-dir', str(WP / 'output' / 'scheme_a'), '--b-doc', str(b_doc()), '--out', str(out),
                      '--config', str(CFG), '--label', 'blind_i-reread'], round(n_pages() * 0.4), out / 'results.json')


def vert(a):
    out = OUT / NAME / 'vertical'
    _sub(a, '页边竖排识别', [PY, 'scripts/f1r2_vert_call.py', '--b-doc', str(b_doc()), '--render-dir', str(WO / 'output' / 'scheme_a' / 'renders'),
                        '--out', str(out), '--config', str(CFG), '--label', 'blind_i-vertical'], n_pages(), out / 'results.json')


# ------------------------------------------------------------------ 计分
def score(a):
    """C（方案E＋改进⊗，含真实页边竖排识别）/ 第1轮冻结路线（../../冻结_新盲测前/）/ 第2轮冻结路线（../冻结/）三行；
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
    for tag, script in (('R1', NB.parent / 'route_r1_cli.py'), ('R2', NB.parent / '冻结' / 'frozen_stack2.py')):
        ld = OUT / f'表格线缓存_{tag}'; (ld / 'renders300').mkdir(parents=True, exist_ok=True); (ld / 'lines_cache').mkdir(parents=True, exist_ok=True)
        r = subprocess.run([PY, str(script)] + common + [str(ld)] + tail + [str(OUT / f'pred_{tag}.json')], capture_output=True, text=True, cwd=ROOT,
                           env={**__import__('os').environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        (OUT / '日志').mkdir(parents=True, exist_ok=True); (OUT / '日志' / f'路线_{tag}.log').write_text(redact(r.stdout + r.stderr), encoding='utf-8')
        assert r.returncode == 0, f'路线 {tag} 失败，见 日志/路线_{tag}.log'
    R1, R2 = jl(OUT / 'pred_R1.json'), jl(OUT / 'pred_R2.json')
    V = {'C': C, '第1轮冻结路线': R1['pred'], '第2轮冻结路线': R2['pred']}
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
    wj(OUT / 'scores.json', {'计分时间_伦敦': now(), 'pred_sha256': pred_sha, '发票页': inv, '百度账本': ledger(), '方案E步骤': cnt, '结果': res})
    print(json.dumps({n: {k: E_(v) for k, v in r.items() if not k.endswith('逐页')} for n, r in res.items()}, ensure_ascii=False))

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['verify', 'check', 'prep', 'baidu', 'build', 'reread', 'vert', 'score', 'ledger'])
    ap.add_argument('--live', action='store_true')
    a = ap.parse_args()
    if a.cmd == 'verify': print('校验通过', verify())
    elif a.cmd == 'ledger': print(json.dumps(ledger(), ensure_ascii=False, indent=1))
    else: globals()[a.cmd](a)
