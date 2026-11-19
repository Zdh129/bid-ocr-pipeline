"""本地补测_1119 公共部分：只读缓存，断网保护（任何 socket 连接直接报错），不改冻结文件。"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def _no_net(*a, **k):
    raise RuntimeError("本地补测_1119：禁止任何网络连接")


socket.socket.connect = _no_net
socket.socket.connect_ex = _no_net
socket.create_connection = _no_net

OUTD = ROOT / "output" / "本地补测_1119"
SETS = ["synth18", "synth18c", "synth18d", "synth18e"]


def ctx(name):
    """与 scripts/配置甲_开发成绩.py 的 one() 同样的方式建 f1r2.Ctx（只读缓存）。"""
    import importlib.util as u
    s = u.spec_from_file_location("jia_dev", ROOT / "scripts" / "配置甲_开发成绩.py")
    m = u.module_from_spec(s)
    s.loader.exec_module(m)
    import f1r2 as F
    fout, ent, llm_ok = m.SETS[name]
    F.OUT = fout
    if ent:
        F.DS[name] = ent
    d = F.DS[name]
    for k in ("b", "render"):
        assert Path(d[k]).exists(), (name, k)
    assert (fout / name / "seal_leak" / "results.json").exists(), (name, "seal_leak 缓存缺")
    return F.Ctx(name), d, llm_ok
