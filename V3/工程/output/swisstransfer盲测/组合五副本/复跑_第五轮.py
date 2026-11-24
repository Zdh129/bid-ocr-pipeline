"""第五轮最佳组合（组合五）：在组合一（第四轮最佳组合）基础上叠加 rules/v5/table_title.py（丙0 清单识别扩充＋丙1 表格页顶部标题置首；只改顺序、拼接和空白，不改字符）。
另与组合一（../组合一副本/预测）逐页比较：原字符多重集必须相同，列出逐页退步。
第四轮说明：在第三版基础上叠加 rules/v4/layout_order.py（只改顺序、拼接和空白，不改字符）。
原第三版说明：在第二版基础上叠加 rules/v3 的通用层。一条命令复跑＋计分（离线，只用缓存，0 次百度调用；不改参照、冻结物、生产配置；不 push）。
流程（每个数据集按输入段分别处理，博凯是两段：博凯包、社保重渲）：
  0 安全检查：无网络、无百度环境变量；冻结副本与第3轮冻结规则 sha256 一致；本目录代码与 sha256 清单一致（首次运行时生成）。
  1 底座＝冻结路线原样：路线代码/rules/round5/route5.py（combo 1,4,2，内部调用 round3/frozen_stack3.py，S6 删除门槛 0.4）→ round5_method6/tablerow（gx=0.4, gy=1.6）。
  2 按 配置_第二版.json 叠加：竖排去重 → 印章清理 → 第一层表格按物理行拆分 → [电子章清理] → 第三层字段金额核对（只标记）。
  3 页面类型标签（杨工 classify 思路）写进每页的 meta 字段和 元数据/，不改正文。
  4 缓存核对：按新键（图片 sha256＋接口＋参数哈希）给现有缓存建只读索引并逐条校验；整条路线所需的主识别结果全部能按新键命中。
  5 计分（原参照）：主口径 tolerant_score、严格口径 score_system；与候选路线第二版逐字比对正文、逐页找退步；另加「不计顺序」口径。
用法：bash 复跑.sh [数据集 ...]"""
import copy, hashlib, json, os, socket, subprocess, sys
from collections import Counter
from pathlib import Path
sys.dont_write_bytecode = True
for _k in range(1, 9):
    _s = Path(__file__).resolve().parents[_k] / 'scripts'
    if (_s / 'ocr_lab_paths.py').exists():
        sys.path.insert(0, str(_s)); break
import ocr_lab_paths as LP
H = Path(__file__).resolve().parent; ROOT = LP.baidu_root_path(); O = ROOT / 'output'
BK = O / '博凯文件_最优路线测试'; TS = O / '盲测_版面约束/版面分析优化计划/试算'; FZD = TS / '第5轮/当前最优_冻结_竖排门槛0.4'
FR = BK / '优化_第3轮/留出验证/冻结规则'; R = H / '路线代码/rules'; V2 = R / 'v2'
_py_lab = ROOT / '.venv/bin/python'
PY = str(_py_lab) if _py_lab.is_file() else sys.executable
CFG = json.load(open(H / '配置_第五轮.json')); V2D = O / '候选路线_第二版'; V3 = R / 'v3'; V4 = R / 'v4'; V5 = R / 'v5'; C1D = H.parent / '组合一副本'; V3D = O / '候选路线_第三版_博凯清零'
# ---------- 0 安全检查（原机冻结校验与断网检查：仅当 OCR_LAB_CHECKS=1 时执行） ----------
def _lab_checks():
    assert not [k for k in os.environ if 'BAIDU' in k.upper()], '环境里还有百度变量'
    try:
        socket.create_connection(('aip.baidubce.com', 443), timeout=3).close(); raise SystemExit('网络可达：请用 复跑.sh（unshare -rn）运行')
    except OSError:
        pass
    assert subprocess.run(['sha256sum', '-c', '--quiet', '冻结_sha256.txt'], cwd=FZD).returncode == 0, '冻结副本被改'
    for line in (FR / '冻结_sha256.txt').read_text().splitlines():
        h, f = line.split(None, 1); assert hashlib.sha256((FR / f.strip()).read_bytes()).hexdigest() == h, f'第3轮冻结规则被改：{f}'
    for f in ('layer1.py', 'layer3.py'):
        assert (V2 / f).read_bytes() == (FR / f).read_bytes(), f'v2/{f} 与第3轮冻结规则不一致'
if LP.lab_checks_enabled():
    _lab_checks()
else:
    LP.skip_lab_checks_notice()
sys.path[:0] = [str(V5), str(V4), str(V3), str(V2), str(LP.v3_root() / '工程/output/盲测_版面约束/版面分析优化计划/试算/第5轮/综合测试_2776d4b'), str(LP.repo_root() / 'src'), str(LP.repo_root() / 'scripts')]
import sets_all as SA, synth18b_score as SB, tolerant_score as T
import layer1, layer3, eseal, page_type, baidu_cache as BC
import v3_layers as V3L; import layout_order as V4L; import table_title as V5L; sys.path.insert(0, str(LP.v3_root() / '工程/output/博凯_清零/工具')); import 计分 as S3
from dedup import dedup; from seal_rule import clean; from vsets import VS
# ---------- 数据集与输入段 ----------
def seg_bk(tag, d, off, keep):
    W = d / '运行'; WO, WP = W / 'orig/work', W / 'P2/work'
    return dict(tag=tag, C=d / '预测/bk_C.json', sa_o=WO / 'output/scheme_a', sa_p2=WP / 'output/scheme_a', pdf=d / '输入/bk-scan.pdf', name='bk',
                lines=H / '线缓存' / tag, doc=WO / 'output/final/scheme_b_standard/blind20-scan.json', rd=WO / 'output/scheme_a/renders',
                rr=W / 'reread/A_P2/results.json', vert=W / 'bk/vertical/results.json', off=off, keep=keep)
JS = BK / '优化_第4轮/江苏留出'
DATA = {'博凯': [seg_bk('博凯包', BK / '博凯包', 0, lambda p: not 8 <= p <= 14), seg_bk('社保重渲', BK / '博凯包_社保重渲', 7, lambda p: p <= 7)],
        '江苏': [dict(tag='江苏', C=JS / '预测/js_C.json', sa_o=JS / '运行/orig/work/output/scheme_a', sa_p2=JS / '运行/P2/work/output/scheme_a', pdf=JS / '输入/bk-scan.pdf',
                     name='bk', lines=H / '线缓存/江苏', doc=JS / '运行/orig/work/output/final/scheme_b_standard/blind20-scan.json', rd=JS / '运行/orig/work/output/scheme_a/renders',
                     rr=JS / '运行/reread/A_P2/results.json', vert=JS / '运行/bk/vertical/results.json', off=0, keep=lambda p: True)]}
for s, c in SA.SETS.items():
    DATA[s] = [dict(tag=s, C=Path(c['C']), sa_o=Path(c['sa_o']), sa_p2=Path(c['sa_p2']), pdf=Path(c['pdf']), name=c['name'], lines=H / '线缓存/synth' / s,
                    doc=Path(c['doc']), rd=Path(c['rd']), rr=Path(c['rr']), vert=VS[s][0], off=0, keep=lambda p: True)]
REFS = {'博凯': BK / '博凯包/参照', '江苏': BK / '优化_第3轮/留出验证/江苏凭证/参照', **{s: Path(c['ref']) for s, c in SA.SETS.items()}}
def ref8(s):   # 对照：候选路线第二版的预测（只读）
    return {p: {k: v for k, v in pg.items() if k != 'meta'} for p, pg in json.load(open(V2D / '预测' / f'{s}.json')).items()}
# ---------- 1 底座：冻结路线原样 ----------
ENV = {k: v for k, v in os.environ.items() if 'BAIDU' not in k.upper()}
ENV.update(PYTHONDONTWRITEBYTECODE='1', R5_MODS=str(R / 'round5'), R5_STACK3=str(R / 'round3' / 'frozen_stack3.py'))
def base(sg, outdir):
    out = outdir / f"{sg['tag']}_route.json"; fin = outdir / f"{sg['tag']}_底座.json"
    (Path(sg['lines']) / 'renders300').mkdir(parents=True, exist_ok=True); (Path(sg['lines']) / 'lines_cache').mkdir(parents=True, exist_ok=True)
    a = [PY, str(R / 'round5/route5.py'), str(sg['C']), str(sg['sa_o']), str(sg['sa_p2']), str(sg['pdf']), sg['name'], str(sg['lines']), str(sg['doc']), str(sg['rd']), str(sg['rr']), str(out), '1,4,2']
    r = subprocess.run(a, capture_output=True, text=True, cwd=ROOT, env=ENV)
    if r.returncode: raise SystemExit(f"{sg['tag']} 底座失败：" + r.stderr[-2000:])
    code = f'''
import json, sys; sys.dont_write_bytecode = True; sys.path.insert(0, {str(R / "round5_method6")!r})
import tablerow as TR
cfg = dict(name={sg["name"]!r}, C={str(sg["C"])!r}, sa={str(sg["sa_p2"])!r}, pdf={str(sg["pdf"])!r}, lines={str(sg["lines"])!r})
new, logs = TR.apply(json.load(open({str(out)!r}))["pred"], cfg, {{"gx": 0.4, "gy": 1.6}})
json.dump({{"pred": new}}, open({str(fin)!r}, "w"), ensure_ascii=False, indent=1)
'''
    r = subprocess.run([PY, '-c', code], capture_output=True, text=True, cwd=ROOT, env=ENV)
    if r.returncode: raise SystemExit(f"{sg['tag']} 方法6 失败：" + r.stderr[-2000:])
    return json.load(open(fin))['pred']
# ---------- 2 各层 ----------
def layers(sub, sg, cfg):
    log = {}
    if cfg['竖排去重']:
        units = json.load(open(sg['vert'])).get('units') or [] if Path(sg['vert']).exists() else []
        sub, log['竖排去重'] = dedup(sub, units)
    if cfg['印章清理']: sub, log['印章清理'] = clean(sub)
    if cfg['第一层表格按物理行拆分']: sub, log['第一层'] = layer1.apply(sub, str(sg['doc']), str(sg['sa_o']))
    if cfg['电子章清理']: sub, log['电子章清理'] = eseal.apply(sub, sg['sa_o'])
    if any(cfg.get(k) for k in ('第三版B_认可标志页脚', '第三版C_低置信度英文杂字', '第三版D_超大字低置信度', '第三版E_页首英文徽标')): sub, log['第三版BCDE'] = V3L.layer_noise(sub, sg['sa_o'], cfg)
    if cfg.get('第三版A_物理行重排_税务社保表单'): sub, log['第三版A'] = V3L.layer_phys(sub, sg['sa_o'], cfg)
    if any(cfg.get(k) for k in V4KEYS): sub, log['第四轮'] = layer_v4(sub, sg['sa_o'], cfg)
    if any(cfg.get(k) for k in V5MAP): sub, log['第五轮'] = layer_v5(sub, sg['sa_o'], cfg)
    if cfg['第三层字段金额核对_只标记']: log['第三层核对'] = layer3.run(sub)
    return sub, log
# 第四轮：配置键 → layout_order.apply 的开关
V4MAP = {'第四轮甲1_发票表头分栏': '发票表头', '第四轮甲1_无标题也分栏': '表头_无标题也分栏', '第四轮甲2_发票明细行列重建': '发票明细',
         '第四轮甲3_完税社保表倾斜校正': '完税社保倾斜', '第四轮甲4_偏离表行列重建': '偏离表', '第四轮甲5_合同明细行列重建': '合同明细',
         '第四轮甲6_设备清单行列重建': '设备清单', '第四轮甲7_发票页边注记移到页尾': '发票页边注记',
         '第四轮表格_倾斜校正': '倾斜校正', '第四轮表格_合并单元格归首行': '合并单元格归首行', '第四轮表格_跨行拼回': '跨行拼回', '第四轮表格_表头列顺序': '表头列顺序'}
V4KEYS = [k for k, v in V4MAP.items() if v in ('发票表头', '发票明细', '完税社保倾斜', '偏离表', '合同明细', '设备清单', '发票页边注记')]
def layer_v4(sub, sa_dir, cfg):
    c = {v: bool(cfg.get(k)) for k, v in V4MAP.items()}; out, log = {}, {}
    for q, pg in sub.items():
        u = V3L.PO.load_units(sa_dir, int(q))
        out[q], lg = V4L.apply(pg, u, c)
        if lg: log[q] = lg
    return out, log
# 第五轮：配置键 → table_title.apply 的开关
V5MAP = {'第五轮丙0_清单识别扩充': ['清单识别扩充'], '第五轮丙1_表格页顶部标题置首': ['标题置首'],
         '第五轮丙2_偏离表合同明细设备清单守卫重建': ['偏离表守卫重建', '合同明细守卫重建', '设备清单守卫重建'], '第五轮丙4_完税社保表倾斜校正': ['完税社保倾斜']}
def layer_v5(sub, sa_dir, cfg):
    c = {v: True for k, vs in V5MAP.items() if cfg.get(k) for v in vs}; out, log = {}, {}
    for q, pg in sub.items():
        u = V3L.PO.load_units(sa_dir, int(q))
        out[q], lg = V5L.apply(pg, u, c)
        if lg: log[q] = lg
    return out, log
def meta(pred, chk, cfg):
    out = {}
    for p, pg in pred.items():
        m = {}
        if cfg['页面类型标签_只写元数据']: m['页面类型'] = page_type.tag_page(pg)
        if cfg['第三层字段金额核对_只标记'] and p in chk: m['字段金额核对'] = chk[p]
        out[p] = m
    return out
# ---------- 4 缓存核对 ----------
def cache_check(names):
    LI = BC.LegacyIndex(); n_dirs = 0
    seen = set()
    for s in names:
        for sg in DATA[s]:
            for d in (sg['sa_o'], sg['sa_p2']):
                if str(d) in seen or not (Path(d) / 'call_summary.json').exists(): continue
                seen.add(str(d)); LI.add_scheme_a(d, Path(d) / 'renders'); n_dirs += 1
    ny = LI.add_yang_style(BK / '杨工分流对比/缓存')
    caller = BC.CachedCaller(BC.KeyedCache(H / '缓存_新键'), BC.UnitGate(H / '日志/实际单位_本次.json', 0), LI, call_fn=None)
    ok = bad = 0; errs = []
    for k, e in LI.idx.items():
        try:
            if e.get('图片'): caller.recognize(e['图片'], e['接口'], e['参数'])
            else: assert LI.get(e['图片sha256'], e['接口'], e['参数']) is not None
            ok += 1
        except Exception as ex:
            bad += 1; errs.append(f'{Path(e["旧文件"]).name}: {ex}')
    LI.dump(H / '缓存索引.json')
    return {'scheme_a目录数': n_dirs, '新键条目': len(LI.idx), '其中杨工式': ny, '逐条校验通过': ok, '校验失败': bad, '失败明细': errs[:20],
            '未建键的旧文件数（裁剪图输入，路线仍按原路径读取）': len(LI.skipped), '命中统计': caller.stats, '真实调用': caller.stats['真实调用']}
# ---------- 5 计分（与第5轮 run5.py 相同） ----------
K = ('ref_chars', 'matches', 'deletions', 'substitutions', 'insertions'); E = lambda o: o['deletions'] + o['substitutions'] + o['insertions']
rate = lambda a: {'错字': E(a), '参照字数': a['ref_chars'], '读出率': round(100 * a['matches'] / a['ref_chars'], 3), '错字率': round(100 * E(a) / a['ref_chars'], 3)}
_RC = {}
def load(rd):
    if str(rd) not in _RC:
        bc, bs, hs, refs, rj, mp, rng = SB.load_bs(rd); f = Path(rd) / '图片区不计分行.json'
        img = {int(k): v for k, v in json.load(open(f)).items()} if f.exists() else {}
        rj2 = copy.deepcopy(rj)
        for pg in rj2['pages']: pg['ref_lines'] = [l for l in pg['ref_lines'] if l.get('kind') != 'image']
        _RC[str(rd)] = (bs, refs, rj2, mp, img)
    return _RC[str(rd)]
def score(rd, pr):
    bs, refs, rj, mp, img = load(rd); pr = copy.deepcopy(pr)
    tol = T.score_tolerant(bs, pr, rj, mp, 'in'); st = bs.score_system(pr, refs['full'], mp, None); pages = sorted(tol['per_page'])
    agg = lambda x, ps: {k: sum(x['per_page'][p]['overall'][k] for p in ps) for k in K}
    uo, uop = S3.unordered(bs, S3.load(rd)[1], pr, pages)
    return {'主': rate(agg(tol, pages)), '严': rate(agg(st, pages)), '不计顺序': {'错字': uo['错字'], '错字率': uo['错字率']}, '不计顺序_逐页': uop, '主_逐页': {str(p): E(tol['per_page'][p]['overall']) for p in pages},
            '严_逐页': {str(p): E(st['per_page'][p]['overall']) for p in pages}, '页类型': {str(p): mp[p]['doc_type'] for p in pages}}
body = lambda pr: {p: ([(x['section'], x['text']) for x in v['sections']], v.get('seal') or '') for p, v in pr.items()}
# ---------- 主流程 ----------

def _require_official(names):
    LP.baidu_root(required=True)
    missing = []
    for s in names:
        if s not in DATA:
            raise LP.MissingInputError(f'未知数据集：{s}。请设置 {LP.ENV_BAIDU} 并确认该集输入存在。')
        for sg in DATA[s]:
            if not Path(sg['C']).is_file():
                missing.append(f"{s}/{sg['tag']} 底座输入 C.json：{sg['C']}")
            if not (Path(sg['sa_o']) / 'call_summary.json').is_file():
                missing.append(f"{s}/{sg['tag']} 原图 scheme_a 缓存（缺 call_summary.json）：{sg['sa_o']}")
            if not (Path(sg['sa_p2']) / 'call_summary.json').is_file():
                missing.append(f"{s}/{sg['tag']} 去章图 scheme_a 缓存（缺 call_summary.json）：{sg['sa_p2']}")
            if not Path(sg['pdf']).is_file():
                missing.append(f"{s}/{sg['tag']} 扫描件：{sg['pdf']}")
        ref = REFS.get(s)
        if ref is not None and not Path(ref).exists():
            missing.append(f"{s} 参照：{ref}")
    if missing:
        raise LP.MissingInputError('正式运行缺少必要输入、缓存或参照：\n' + '\n'.join(missing) + f'\n请设置环境变量 {LP.ENV_BAIDU} 指向原试验机 baidu 工作根后重试。未写入任何成绩文件。')

def main(argv=None):
    argv = list(argv or sys.argv)
    ONLY = argv[1:] or ['博凯'] + list(SA.SETS) + ['江苏']
    VARS = {'按配置': CFG}
    _require_official(ONLY)
    for d in ('中间', '预测', '元数据', '成绩', '日志'): (H / d).mkdir(exist_ok=True)
    summary = json.load(open(H / '汇总.json')) if (H / '汇总.json').exists() else {}
    summary['缓存核对'] = cache_check(list(DATA)); print('缓存核对', {k: v for k, v in summary['缓存核对'].items() if k != '失败明细'}, flush=True)
    assert summary['缓存核对']['校验失败'] == 0 and summary['缓存核对']['真实调用'] == 0
    for s in ONLY:
        a = {}
        for sg in DATA[s]:
            a[sg['tag']] = base(sg, H / '中间')
        res = {}; ref8_pred = ref8(s); r8 = score(REFS[s], ref8_pred)
        for vn, cfg in VARS.items():
            pred, logs, chk = {}, {}, {}
            for sg in DATA[s]:
                sub, lg = layers(a[sg['tag']], sg, cfg); logs[sg['tag']] = lg
                for q, v in sub.items():
                    p = int(q) + sg['off']
                    if sg['keep'](int(q)):   # keep 按段内页号判断
                        pred[str(p)] = v
                for q, v in (lg.get('第三层核对') or {}).items(): chk[str(int(q) + sg['off'])] = v
            pred = {p: pred[p] for p in sorted(pred, key=int)}
            M = meta(pred, chk, cfg); full = {p: {**v, 'meta': M[p]} for p, v in pred.items()}
            sc = score(REFS[s], pred)
            reg = {k: sorted([p for p in sc[k + '_逐页'] if sc[k + '_逐页'][p] > r8[k + '_逐页'][p]], key=int) for k in ('主', '严')}
            imp = {k: sorted([p for p in sc[k + '_逐页'] if sc[k + '_逐页'][p] < r8[k + '_逐页'][p]], key=int) for k in ('主', '严')}
            res[vn] = {'配置': cfg, '成绩': sc, '与第二版正文逐字相同': body(pred) == body(ref8_pred),
                       '与第二版正文逐字相同_计分页': all(body({p: pred[p]}) == body({p: ref8_pred[p]}) for p in sc['主_逐页']),
                       '与第二版正文不同的页': sorted([p for p in pred if body({p: pred[p]}) != body({p: ref8_pred[p]})], key=int),
                       '相对第二版_主': sc['主']['错字'] - r8['主']['错字'], '相对第二版_严': sc['严']['错字'] - r8['严']['错字'], '逐页退步': reg, '逐页改善': imp}
            if vn == '按配置':
                json.dump(full, open(H / '预测' / f'{s}.json', 'w'), ensure_ascii=False, indent=1, default=str)
                json.dump(M, open(H / '元数据' / f'{s}_逐页.json', 'w'), ensure_ascii=False, indent=1, default=str)
            else:
                json.dump(full, open(H / '预测' / f'{s}_{vn}.json', 'w'), ensure_ascii=False, indent=1, default=str)
            json.dump(logs, open(H / '日志' / f'{s}_{vn}_规则日志.json', 'w'), ensure_ascii=False, indent=1, default=str)
            print(s, vn, '主', sc['主']['错字'], '严', sc['严']['错字'], '不计顺序', sc['不计顺序']['错字'], '与第二版相同', res[vn]['与第二版正文逐字相同'], '计分页相同', res[vn]['与第二版正文逐字相同_计分页'], '不同页', res[vn]['与第二版正文不同的页'], '退步', reg, flush=True)
        res['第二版'] = {'成绩': r8}
        v3p = {p: {k: v for k, v in pg.items() if k != 'meta'} for p, pg in json.load(open(V3D / '预测' / f'{s}.json')).items()}; r3 = score(REFS[s], v3p); sc4 = res['按配置']['成绩']
        res['第三版'] = {'成绩': r3, '相对第三版_主': sc4['主']['错字'] - r3['主']['错字'], '相对第三版_严': sc4['严']['错字'] - r3['严']['错字'],
                      '相对第三版逐页退步': {k: sorted([p for p in sc4[k + '_逐页'] if sc4[k + '_逐页'][p] > r3[k + '_逐页'][p]], key=int) for k in ('主', '严')},
                      '与第三版正文不同的页': sorted([p for p in pred if body({p: pred[p]}) != body({p: v3p.get(p, {'sections': []})})], key=int)}
        print(s, '相对第三版 主', res['第三版']['相对第三版_主'], '严', res['第三版']['相对第三版_严'], '逐页退步', res['第三版']['相对第三版逐页退步'], '改动页', res['第三版']['与第三版正文不同的页'], flush=True)
        c1p = {p: {k: v for k, v in pg.items() if k != 'meta'} for p, pg in json.load(open(C1D / '预测' / f'{s}.json')).items()}; r1 = score(REFS[s], c1p)
        rawc = lambda pg: Counter(c for x in pg.get('sections') or [] for c in (x.get('text') or '') if not c.isspace())
        res['组合一'] = {'成绩': r1, '相对组合一_主': sc4['主']['错字'] - r1['主']['错字'], '相对组合一_严': sc4['严']['错字'] - r1['严']['错字'],
                      '相对组合一逐页退步': {k: sorted([p for p in sc4[k + '_逐页'] if sc4[k + '_逐页'][p] > r1[k + '_逐页'][p]], key=int) for k in ('主', '严')},
                      '与组合一正文不同的页': sorted([p for p in pred if body({p: pred[p]}) != body({p: c1p.get(p, {'sections': []})})], key=int),
                      '原字符多重集与组合一不同的页': sorted([p for p in pred if rawc(pred[p]) != rawc(c1p.get(p, {'sections': []})) or (pred[p].get('seal') or '') != (c1p.get(p, {}).get('seal') or '')], key=int)}
        print(s, '相对组合一 主', res['组合一']['相对组合一_主'], '严', res['组合一']['相对组合一_严'], '逐页退步', res['组合一']['相对组合一逐页退步'], '改动页', res['组合一']['与组合一正文不同的页'], '原字符多重集不同的页', res['组合一']['原字符多重集与组合一不同的页'], flush=True)
        json.dump(res, open(H / '成绩' / f'{s}.json', 'w'), ensure_ascii=False, indent=1)
        summary.setdefault('数据集', {})[s] = {vn: {k: v for k, v in r.items() if k not in ('配置',)} for vn, r in res.items()}
        json.dump(summary, open(H / '汇总.json', 'w'), ensure_ascii=False, indent=1, default=str)
    print('完成；百度调用 0 次')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        if type(e).__name__ == 'MissingInputError':
            print(e, file=sys.stderr)
            sys.exit(1)
        raise
