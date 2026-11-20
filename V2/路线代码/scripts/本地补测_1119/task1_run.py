"""任务1 运行：①配置甲开发成绩四套全部已报对照（修正口径重算）；②扫一遍以前报过的其他「新引入错误」缓存。"""
import json
from common import ROOT, OUTD, SETS
import jia_eval as J
from newerr_fix import pairs_for

OUTD.mkdir(parents=True, exist_ok=True)
dev = {}
for n in SETS:
    d = ROOT / "output/配置甲/开发成绩_第二版" / n
    dev[n] = pairs_for(d, [J.B_NAME, J.E_NAME, J.R3N, J.R3L, J.NOSEAL])
others = {}
for rel in ["synth18d", "synth18e", "blind20_frozen", "blind20_frozen/完整计分", "f1_round2/synth18", "f1_round2/synth18c", "synth18b", "synth18", "synth18c"]:
    d = ROOT / "output" / rel
    diff = json.loads((d / "diff_vs_ref.json").read_text(encoding="utf-8"))
    rows0 = next((rs for rs in diff.values() if rs), [])
    if rows0 and "ref_start" not in rows0[0]:
        others[rel] = "旧格式（错误行无参照位置，旧口径本来就不是按位置算的），跳过"
        continue
    others[rel] = pairs_for(d, list(diff))
(OUTD / "任务1_新引入错误_修正口径.json").write_text(json.dumps({"dev": dev, "others": others}, ensure_ascii=False, indent=1), encoding="utf-8")
for n, rs in dev.items():
    for r in rs:
        print("DEV", n, r["var"], "vs", r["base"], "旧", r["old_n"], r["old_cost"], "补", r["add_n"], r["add_cost"])
for k, rs in others.items():
    if isinstance(rs, str):
        print("OTHER", k, rs); continue
    tot = len(rs); hit = [r for r in rs if r["add_n"]]
    print("OTHER", k, f"对照对数 {tot}，修正后有变化 {len(hit)}")
