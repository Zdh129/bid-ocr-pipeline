"""停止规则（盲测集_v1 30 页实跑隔离副本，不改原脚本、不进生产）：
百度返回里任何一层出现额度/权限/未开通类错误码，或错误消息含 quota/limit/额度/权限/未开通 等字样，就判为“必须停”。
网络错误（如 Max retries exceeded）不含在内，由 capwrap 做有限次重试，仍失败再停并汇报。
"""
import json, re
from pathlib import Path

# 百度通用：4/17/18/19 额度或 QPS 上限，6/110/111 无权限或令牌问题，13/14 鉴权，3 接口不存在/未开通；
# 2820xx 文档解析类（282000 内部错误、282005 quota exceed 等）一律停；2161xx 除图片格式类外一律停。
STOP_CODES = {'3', '4', '6', '13', '14', '15', '17', '18', '19', '100', '110', '111', '216102', '216110', '282000', '282003', '282004', '282005', '282006'}
STOP_EXCEPT = {'216100', '216101', '216200', '216201', '216202', '216630', '216634'}
NET = re.compile(r'Max retries exceeded|Retries? exceeded|Connection(?:Error| aborted| reset)|ConnectTimeout|ReadTimeout|timed out|Timeout|NameResolutionError|Temporary failure in name resolution|Remote end closed|SSLError|ChunkedEncodingError|NewConnectionError|ProxyError', re.I)
# 不用单独的 exceed，以免把 Max retries exceeded 误判成额度错误；配额仍靠 quota exceed / STOP_CODES。
MSG = re.compile(r'quota(?:\s+exceed)?|limit(?:ed|s)?\b|(?<!tries )(?<!retry )exceed(?:ed|s)?(?:\s+error)?|permission|denied|not\s*(?:open|enabled|authori[sz]ed)|unauthori[sz]ed|forbidden|arrear|'
                 r'额度|权限|未开通|没有开通|欠费|余额不足|超限|超出|上限|无权', re.I)
CODE_KEYS = ('error_code', 'err_code', 'errno', 'err_no', 'code', 'ret_code')
MSG_KEYS = ('error_msg', 'err_msg', 'errmsg', 'msg', 'message', 'error', 'ret_msg')

def code_stop(code):
    if code is None or isinstance(code, bool): return False
    c = str(code).strip()
    if c in ('', '0', 'None', 'success', 'ok'): return False
    if not re.fullmatch(r'-?\d+', c): return False
    return c in STOP_CODES or c.startswith('2820') or (c.startswith('2161') and c not in STOP_EXCEPT)

def is_network(m):
    return isinstance(m, str) and bool(NET.search(m))

def msg_stop(m):
    if not isinstance(m, str) or is_network(m):
        return False
    return bool(MSG.search(m)) and not re.fullmatch(r'\s*(success|ok)\s*', m, re.I)

def find_stop(obj, depth=0, path=''):
    """返回 (路径, 错误码, 消息) 或 None。只看错误码/错误消息字段，不看正文识别结果（避免正文里的“上限”等字误判）。"""
    if depth > 6: return None
    if isinstance(obj, dict):
        code = next((obj[k] for k in CODE_KEYS if k in obj and not isinstance(obj[k], (dict, list))), None)
        msg = next((obj[k] for k in MSG_KEYS if k in obj and isinstance(obj[k], str)), None)
        has_err = code_stop(code) or (msg is not None and msg_stop(msg) and (code not in (None, 0, '0') or any(k in obj for k in ('error_msg', 'err_msg', 'error'))))
        if has_err: return (path or '/', code, msg)
        for k, v in obj.items():
            if k in ('words_result', 'parse_result', 'markdown', 'pages', 'layouts'): continue   # 识别正文
            r = find_stop(v, depth + 1, f'{path}/{k}')
            if r: return r
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:2000]):
            r = find_stop(v, depth + 1, f'{path}[{i}]')
            if r: return r
    return None

def exc_stop(e):
    s = f'{type(e).__name__}: {e}'
    if is_network(s):
        return False
    m = re.search(r'(?:error_code|code)\D{0,5}(\d{1,6})', s)
    return (m and code_stop(m.group(1))) or msg_stop(s)

def scan_dir(d):
    """步骤结束后扫描该步输出目录里的 json 返回（raw、cost、call_summary 等）。"""
    d = Path(d)
    if not d.exists(): return None
    for f in sorted(d.rglob('*.json')):
        try: o = json.loads(f.read_text(encoding='utf-8'))
        except Exception: continue
        r = find_stop(o)
        if r: return (str(f),) + r
    return None
