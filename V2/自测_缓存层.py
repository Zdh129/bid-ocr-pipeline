"""缓存层自测（离线，假调用函数，不联网、不碰 key）：新键命中、旧缓存命中、未命中过闸门、闸门超限停止、额度/权限错误码立即停止并停用接口、缓存被改时报错。"""
import json, sys, tempfile
from pathlib import Path
sys.dont_write_bytecode = True
H = Path(__file__).resolve().parent; sys.path.insert(0, str(H / '路线代码/rules/v2'))
import baidu_cache as BC
res = {}
with tempfile.TemporaryDirectory() as td:
    td = Path(td); img = td / 'a.png'; img.write_bytes(b'fake-image-1'); img2 = td / 'b.png'; img2.write_bytes(b'fake-image-2')
    calls = []
    def ok_fn(raw, ep, prm): calls.append(ep); return {'words_result': [{'words': 'x'}], 'log_id': 1}
    def quota_fn(raw, ep, prm): calls.append(ep); return {'error_code': 17, 'error_msg': 'Open api daily request limit reached'}
    gate = BC.UnitGate(td / 'led.json', 2); cache = BC.KeyedCache(td / 'c')
    c = BC.CachedCaller(cache, gate, None, ok_fn)
    r1 = c.recognize(img, 'accurate', {'probability': 'true'}); r2 = c.recognize(img, 'accurate', {'probability': 'true'})
    res['首次真实调用、第二次新键命中'] = r1['来源'] == '真实调用' and r2['来源'] == '新键缓存' and len(calls) == 1
    r3 = c.recognize(img, 'accurate', {'probability': 'false'})
    res['参数不同算不同键'] = r3['来源'] == '真实调用' and len(calls) == 2
    try: c.recognize(img2, 'accurate', {}); res['闸门超限停止'] = False
    except BC.GateStop: res['闸门超限停止'] = len(calls) == 2 and json.loads((td / 'led.json').read_text())['已用实际单位'] == 2
    gate2 = BC.UnitGate(td / 'led2.json', 10); c2 = BC.CachedCaller(BC.KeyedCache(td / 'c2'), gate2, None, quota_fn)
    try: c2.recognize(img, 'table', {}); res['额度错误码立即停止'] = False
    except BC.StopCalls: res['额度错误码立即停止'] = gate2.disabled() == {'table': '17'}
    n = len(calls)
    try: c2.recognize(img2, 'table', {}); res['停用后不再调用'] = False
    except BC.StopCalls: res['停用后不再调用'] = len(calls) == n
    f = cache.path(BC.sha_file(img), 'accurate', {'probability': 'true'}); d = json.loads(f.read_text()); d['payload']['words_result'][0]['words'] = 'y'; f.write_text(json.dumps(d))
    try: c.recognize(img, 'accurate', {'probability': 'true'}); res['载荷被改时报错'] = False
    except BC.CacheMismatch: res['载荷被改时报错'] = True
    d['image_sha256'] = '0' * 64; f.write_text(json.dumps(d))
    try: c.recognize(img, 'accurate', {'probability': 'true'}); res['身份不符时报错'] = False
    except BC.CacheMismatch: res['身份不符时报错'] = True
    c3 = BC.CachedCaller(BC.KeyedCache(td / 'c3'), BC.UnitGate(td / 'led3.json', 5), None, None)
    try: c3.recognize(img2, 'accurate', {}); res['不允许调用时未命中即停'] = False
    except BC.StopCalls: res['不允许调用时未命中即停'] = True
print(json.dumps(res, ensure_ascii=False, indent=1)); json.dump(res, open(H / '日志/自测_缓存层.json', 'w'), ensure_ascii=False, indent=1)
assert all(res.values())
