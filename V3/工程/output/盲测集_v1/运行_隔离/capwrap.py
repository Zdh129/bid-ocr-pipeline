"""逐次调用闸门（盲测集_v1 30页实跑隔离副本，2026-11-23；不改原脚本）。用法：python capwrap.py 脚本.py 参数...
给 BaiduOcrClient.recognize_image（每次 1 次）和 DocParserClient.parse_pdf（按页计）打补丁，不改仓库代码：
- 有停止标记（运行/停止.json）就拒绝任何调用；
- 页上限：只处理第 1～1000 页，超出的页不调用；
- 各接口次数上限＝恢复配置.json 里该路线估算值×1.3（向上取整），超了就停；估算为 0 的接口一次都不许调；
- 后付费金额：本次累计（按 恢复配置 单价）≤ 本次后付费上限（¥7.50；若填了控制台已用则再与月上限取小），超了就停；
- 每次调用前先记账（失败也计，偏保守），记在 运行/恢复账本.json；
- 返回或异常里有额度/权限/未开通类错误（stoprule.py）就写停止标记并立即退出进程。
不记录密钥、令牌。路线由环境变量 SWISS_ROUTE 指定（后付费 / 替代B_标准版跑去章图 / 替代A_复用原图标准版）。"""
import fcntl, json, math, os, runpy, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
# 运行时读取 key，去掉末尾空白；不打印、不写入日志
for _k in ('BAIDU_OCR_API_KEY', 'BAIDU_OCR_SECRET_KEY'):
    if _k in os.environ:
        os.environ[_k] = os.environ[_k].rstrip()
NET_RETRIES = 3
NET_SLEEP = (2, 5, 10)
# 【仓库副本：只改路径】代码根＝仓库根；恢复配置.json 与 运行/ 在本份材料的运行隔离目录（OCR_RUN_ISO）。
for _k in range(1, 9):
    _s = Path(__file__).resolve().parents[_k] / 'scripts'
    if (_s / 'ocr_lab_paths.py').exists():
        sys.path.insert(0, str(_s)); break
import ocr_lab_paths as LP
ISO = LP.run_iso(required=True)
sys.path[:0] = [str(HERE), str(LP.repo_root() / 'src'), str(LP.repo_root() / 'scripts')]
import stoprule as SR
RC = json.load(open(os.environ.get('SWISS_TEST_RC') or ISO / '恢复配置.json'))   # SWISS_TEST_RC 只给离线模拟测试用
ROUTE = os.environ.get('SWISS_ROUTE', '')
RUN = Path(os.environ.get('SWISS_STOP_TEST_DIR') or ISO / '运行')   # 环境变量只给离线模拟测试用
LED = RUN / '恢复账本.json'; STOPF = RUN / '停止.json'
PAGE_CAP = int(RC['页上限'])
def caps():
    est = RC['估算'].get(ROUTE)
    if est is None: raise SystemExit(f'未知路线 {ROUTE!r}，拒绝调用')
    return {k: math.ceil(v * RC['上限倍数']) for k, v in est.items()}
def money_cap():
    used = RC.get('控制台本月后付费已用_元')
    return min(RC['本次后付费上限_元'], RC['本月后付费上限_元'] - used) if used is not None else RC['本次后付费上限_元']
def stop(what, code, msg):
    STOPF.parent.mkdir(parents=True, exist_ok=True)
    STOPF.write_text(json.dumps({'调用': what, 'error_code': str(code), 'error_msg': str(msg)[:200]}, ensure_ascii=False, indent=1))
    kind = '网络错误重试后仍失败' if str(code) == 'network' else f'额度/权限/计费类错误 {code}'
    sys.stderr.write(f'{kind}，停止全部百度调用，已写 {STOPF.name}\n'); sys.stderr.flush()
    os._exit(97)
def charge(api, units, what):
    if STOPF.exists(): raise SystemExit('已有停止标记，拒绝再调用')
    C = caps(); LED.parent.mkdir(parents=True, exist_ok=True)
    with open(str(LED) + '.lock', 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        d = json.loads(LED.read_text()) if LED.exists() else {'路线': ROUTE, '各接口': {}, '后付费_元': 0.0, '明细': [], '拦截': []}
        n = d['各接口'].get(api, 0); add = RC['单价_后付费_元'].get(api, 0.0) * units
        if api not in C or n + units > C[api]:
            d['拦截'].append({'调用': what, '接口': api, '单位': units, '已用': n, '上限': C.get(api, 0)}); LED.write_text(json.dumps(d, ensure_ascii=False, indent=1))
            raise SystemExit(f'次数闸门：{api} 已用 {n} + 本次 {units} > 上限 {C.get(api, 0)}（估算×{RC["上限倍数"]}），停止')
        if d['后付费_元'] + add > money_cap() + 1e-9:
            d['拦截'].append({'调用': what, '接口': api, '单位': units, '后付费': round(d['后付费_元'] + add, 4)}); LED.write_text(json.dumps(d, ensure_ascii=False, indent=1))
            raise SystemExit(f'金额闸门：本次后付费 {d["后付费_元"]:.2f} + {add:.2f} > {money_cap():.2f}，停止')
        d['各接口'][api] = n + units; d['后付费_元'] = round(d['后付费_元'] + add, 4); d['明细'].append({'调用': what, '接口': api, '单位': units})
        LED.write_text(json.dumps(d, ensure_ascii=False, indent=1))
from baidu_ocr import client as C_, docparser as DP
_ri = C_.BaiduOcrClient.recognize_image
def ri(self, image_path, api, **k):
    what = f'{api}:{Path(str(image_path)).name}'
    pg = k.get('page')
    if isinstance(pg, int) and pg > PAGE_CAP: raise SystemExit(f'页上限：第 {pg} 页超过 {PAGE_CAP}，不调用')
    if self.cfg.get('allow_external') and k.get('mock_response') is None: charge(api, 1, what)
    last = None
    for attempt in range(1, NET_RETRIES + 1):
        try:
            r = _ri(self, image_path, api, **k)
        except Exception as e:
            if SR.exc_stop(e):
                stop(what, 'exception', str(e)[:200])
            if SR.is_network(f'{type(e).__name__}: {e}') and attempt < NET_RETRIES:
                time.sleep(NET_SLEEP[min(attempt-1, len(NET_SLEEP)-1)])
                last = e
                continue
            if SR.is_network(f'{type(e).__name__}: {e}'):
                stop(what, 'network', f'网络错误重试{NET_RETRIES}次仍失败：{type(e).__name__}: {str(e)[:180]}')
            raise
        hit = SR.find_stop(r)
        if hit: stop(what, hit[1], hit[2])
        return r
    stop(what, 'network', f'网络错误重试{NET_RETRIES}次仍失败：{last}')
C_.BaiduOcrClient.recognize_image = ri
_pp = DP.DocParserClient.parse_pdf
def pp(self, path, *a, **k):
    what = f'docparser:{Path(str(path)).name}'
    if self.cfg.get('allow_external') and k.get('mock_result') is None:
        import fitz
        n = k.get('billed_pages') or (len(k['page_filter']) if k.get('page_filter') else len(fitz.open(str(path))))
        if n > PAGE_CAP: raise SystemExit(f'页上限：本次 {n} 页超过 {PAGE_CAP}，不调用')
        charge(f'docparser_{(self.cfg.get("docparser") or {}).get("edition")}', int(n), what)
    last = None
    for attempt in range(1, NET_RETRIES + 1):
        try:
            r = _pp(self, path, *a, **k)
        except Exception as e:
            if SR.exc_stop(e):
                stop(what, 'exception', str(e)[:200])
            if SR.is_network(f'{type(e).__name__}: {e}') and attempt < NET_RETRIES:
                time.sleep(NET_SLEEP[min(attempt-1, len(NET_SLEEP)-1)])
                last = e
                continue
            if SR.is_network(f'{type(e).__name__}: {e}'):
                stop(what, 'network', f'网络错误重试{NET_RETRIES}次仍失败：{type(e).__name__}: {str(e)[:180]}')
            raise
        hit = SR.find_stop(r)
        if hit: stop(what, hit[1], hit[2])
        return r
    stop(what, 'network', f'网络错误重试{NET_RETRIES}次仍失败：{last}')
DP.DocParserClient.parse_pdf = pp
if __name__ == '__main__':
    script = sys.argv[1]; sys.argv = sys.argv[1:]
    runpy.run_path(script, run_name='__main__')
