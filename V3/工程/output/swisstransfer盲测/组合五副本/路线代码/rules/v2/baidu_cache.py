"""百度结果缓存（候选路线第二版，工程层，不改识别文字）。
- 缓存键 ＝ 图片 sha256 ＋ 接口名 ＋ 参数哈希（参数按键排序后的 JSON 的 sha256）。
- 读取时校验：文件里记录的 图片sha256 / 接口 / 参数 必须与本次请求完全一致，且载荷 sha256 与记录一致，否则报 CacheMismatch，绝不静默用错缓存。
- 兼容现有缓存（只读，不搬动、不改写）：
  1) scheme_a 目录：raw/pNN_main_<api>.json ＋ call_summary.json（image_ref 渲染图、params 参数）→ 按新键建索引；读取时再核对渲染图 sha256 与旧文件 sha256 未变。
     （retry/reread 文件的输入是裁剪图，原记录里没有可追溯的图片，不建键，仍按原路径由路线读取。）
  2) 杨工式缓存（文件内含 image_sha256 / endpoint / options）：直接按新键识别。
- 闸门：每次真实调用前检查「已用实际单位 ＋ 本次单位 ≤ 上限」，先记账再调用，失败也计（偏保守）。
- 额度/权限类错误码立即停止（抛 StopCalls），并把该接口记为停用，不重试、不开通、不购买。
- 本模块不读环境变量、不接触 key；真实调用函数由调用方注入。日志只记接口、页、错误码。"""
from __future__ import annotations
import fcntl, hashlib, json
from pathlib import Path

STOP_CODES = {'3', '6', '17', '18', '19', '110', '111', '216102', '216110'}
STOP_2161_EXCEPT = {'216100', '216101', '216200', '216201', '216202', '216630', '216634'}


class CacheMismatch(RuntimeError): pass
class GateStop(SystemExit): pass
class StopCalls(SystemExit): pass


def sha_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def sha_file(p) -> str: return sha_bytes(Path(p).read_bytes())
def canon(params: dict) -> str: return json.dumps({str(k): str(v) for k, v in (params or {}).items()}, sort_keys=True, ensure_ascii=False)
def params_hash(params: dict) -> str: return sha_bytes(canon(params).encode())
def is_stop(code) -> bool:
    c = str(code) if code not in (None, 0, '0') else None
    return bool(c) and (c in STOP_CODES or (c.startswith('2161') and c not in STOP_2161_EXCEPT))


class KeyedCache:
    """新键缓存：<root>/<图片sha256>_<接口>_<参数哈希前16位>.json"""
    def __init__(self, root):
        self.root = Path(root)
    def path(self, img, ep, prm):
        return self.root / f'{img}_{ep}_{params_hash(prm)[:16]}.json'
    def get(self, img, ep, prm):
        f = self.path(img, ep, prm)
        if not f.exists(): return None
        s = json.loads(f.read_text())
        want = (img, ep, canon(prm))
        got = (s.get('image_sha256'), s.get('endpoint'), canon(s.get('params') or {}))
        if got != want: raise CacheMismatch(f'缓存身份不符：{f.name}')
        if sha_bytes(json.dumps(s.get('payload'), sort_keys=True, ensure_ascii=False).encode()) != s.get('payload_sha256'):
            raise CacheMismatch(f'缓存载荷被改：{f.name}')
        return s
    def put(self, img, ep, prm, payload, status='ok', extra=None):
        self.root.mkdir(parents=True, exist_ok=True)
        rec = {'version': 2, 'image_sha256': img, 'endpoint': ep, 'params': {str(k): str(v) for k, v in (prm or {}).items()},
               'params_sha256': params_hash(prm), 'status': status, 'payload': payload,
               'payload_sha256': sha_bytes(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()), **(extra or {})}
        self.path(img, ep, prm).write_text(json.dumps(rec, ensure_ascii=False, indent=1))
        return rec


class LegacyIndex:
    """旧缓存的只读索引：新键 → 旧文件路径＋旧文件 sha256＋图片路径。"""
    def __init__(self):
        self.idx = {}; self.skipped = []
    @staticmethod
    def key(img, ep, prm): return f'{img}|{ep}|{params_hash(prm)}'
    def add_scheme_a(self, sa_dir, render_dir=None):
        sa = Path(sa_dir); cs = json.loads((sa / 'call_summary.json').read_text()); prm = cs.get('params') or {}
        n = 0
        for pg in cs['pages']:
            api = pg['route']['api']; f = sa / 'raw' / f"p{pg['page']:02d}_main_{api}.json"
            img = Path(pg['image_ref'])
            if render_dir is not None and not img.exists(): img = Path(render_dir) / img.name
            if not f.exists() or not img.exists():
                self.skipped.append({'目录': str(sa), '页': pg['page'], '原因': '缺旧文件或渲染图'}); continue
            ish = sha_file(img)
            self.idx[self.key(ish, api, prm)] = {'旧文件': str(f), '旧文件sha256': sha_file(f), '图片': str(img), '图片sha256': ish,
                                                 '接口': api, '参数': {str(k): str(v) for k, v in prm.items()}}
            n += 1
        for f in sorted((sa / 'raw').glob('p*_*.json')):
            if '_main_' not in f.name: self.skipped.append({'目录': str(sa), '文件': f.name, '原因': '裁剪图输入，原记录无图片可追溯，不建键'})
        return n
    def add_yang_style(self, cache_dir):
        n = 0
        for f in sorted(Path(cache_dir).glob('*.json')):
            s = json.loads(f.read_text())
            if not {'image_sha256', 'endpoint', 'options'} <= set(s): continue
            self.idx[self.key(s['image_sha256'], s['endpoint'], s['options'])] = {'旧文件': str(f), '旧文件sha256': sha_file(f), '图片': None,
                                                                                  '图片sha256': s['image_sha256'], '接口': s['endpoint'], '参数': s['options'], '杨工式': True}
            n += 1
        return n
    def get(self, img, ep, prm):
        e = self.idx.get(self.key(img, ep, prm))
        if not e: return None
        if sha_file(e['旧文件']) != e['旧文件sha256']: raise CacheMismatch(f"旧缓存文件被改：{Path(e['旧文件']).name}")
        if e.get('图片') and sha_file(e['图片']) != img: raise CacheMismatch(f"旧缓存对应的渲染图变了：{Path(e['图片']).name}")
        d = json.loads(Path(e['旧文件']).read_text())
        if e.get('杨工式'):
            if (d.get('image_sha256'), d.get('endpoint'), canon(d.get('options') or {})) != (img, ep, canon(prm)): raise CacheMismatch('杨工式缓存身份不符')
            return {'status': d.get('status'), 'payload': d.get('payload'), '来源': '旧缓存(杨工式)'}
        return {'status': 'ok', 'payload': d, '来源': '旧缓存(scheme_a)'}
    def dump(self, path):
        Path(path).write_text(json.dumps({'条目数': len(self.idx), '条目': self.idx, '未建键': self.skipped}, ensure_ascii=False, indent=1))


class UnitGate:
    """单位闸门：先比后记，失败也计。"""
    def __init__(self, ledger, cap):
        self.led = Path(ledger); self.cap = int(cap)
    def _load(self):
        return json.loads(self.led.read_text()) if self.led.exists() else {'已用实际单位': 0, '明细': [], '停用接口': {}}
    def _save(self, d):
        self.led.parent.mkdir(parents=True, exist_ok=True); self.led.write_text(json.dumps(d, ensure_ascii=False, indent=1))
    def charge(self, units, what):
        self.led.parent.mkdir(parents=True, exist_ok=True)
        with open(str(self.led) + '.lock', 'w') as lk:
            fcntl.flock(lk, fcntl.LOCK_EX); d = self._load()
            if d['已用实际单位'] + units > self.cap:
                d.setdefault('拦截', []).append({'调用': what, '单位': units, '已用': d['已用实际单位']}); self._save(d)
                raise GateStop(f'闸门：已用 {d["已用实际单位"]} + 本次 {units} > 上限 {self.cap}，停止')
            d['已用实际单位'] += units; d['明细'].append({'调用': what, '单位': units}); self._save(d)
    def disabled(self): return self._load().get('停用接口', {})
    def disable(self, ep, code):
        with open(str(self.led) + '.lock', 'w') as lk:
            fcntl.flock(lk, fcntl.LOCK_EX); d = self._load(); d.setdefault('停用接口', {})[ep] = str(code); self._save(d)


class CachedCaller:
    """先查新键缓存，再查旧缓存索引；都没有才过闸门真实调用（call_fn 由调用方注入：call_fn(image_bytes, endpoint, params) -> dict 载荷）。"""
    def __init__(self, cache: KeyedCache, gate: UnitGate, legacy: LegacyIndex | None, call_fn=None):
        self.cache, self.gate, self.legacy, self.call_fn = cache, gate, legacy, call_fn
        self.stats = {'新键命中': 0, '旧缓存命中': 0, '真实调用': 0}
    def recognize(self, image_path, endpoint, params, units=1, what=''):
        raw = Path(image_path).read_bytes(); img = sha_bytes(raw)
        s = self.cache.get(img, endpoint, params)
        if s is not None: self.stats['新键命中'] += 1; return {**s, '来源': '新键缓存'}
        if self.legacy is not None:
            s = self.legacy.get(img, endpoint, params)
            if s is not None: self.stats['旧缓存命中'] += 1; return s
        if endpoint in self.gate.disabled(): raise StopCalls(f'接口已停用（错误码 {self.gate.disabled()[endpoint]}），不再调用')
        if self.call_fn is None: raise StopCalls('缓存未命中，且本次运行不允许真实调用')
        self.gate.charge(units, f'{endpoint}:{what or Path(image_path).name}')
        self.stats['真实调用'] += 1
        payload = self.call_fn(raw, endpoint, dict(params or {}))
        code = payload.get('error_code') if isinstance(payload, dict) else 'no_json'
        if is_stop(code):
            self.gate.disable(endpoint, code); self.cache.put(img, endpoint, params, payload, status='error')
            raise StopCalls(f'{endpoint} 返回额度/权限类错误码 {code}，立即停止')
        status = 'error' if code not in (None, 0, '0') else 'ok'
        return {**self.cache.put(img, endpoint, params, payload, status=status), '来源': '真实调用'}
