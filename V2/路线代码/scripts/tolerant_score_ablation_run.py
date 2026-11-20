#!/usr/bin/env python3
"""用已有预测，量化主口径三项放宽各自贡献的错字数。

只读 pred_pages.json、scores.json、blind-ref.json、blind-ref.txt、manifest.json。
不改任何已有文件，不重新识别，不调用外部 API。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import synth18b_score as SB  # noqa: E402
import tolerant_score as T0  # noqa: E402
import tolerant_score_ablation as T  # noqa: E402

OUT = Path("/workspace/pdf-test/baidu/output/ablation_tolerant")

# 每套集：数据集目录、预测目录、主版本键、额外版本键
SETS = [
    {
        "name": "synth18c",
        "sdir": Path("/workspace/pdf-test/synth18c"),
        "odir": Path("/workspace/pdf-test/baidu/output/synth18c"),
        "main": "F1+deepseek",
        "extra": [],
    },
    {
        "name": "synth18d",
        "sdir": Path("/workspace/pdf-test/synth18d"),
        "odir": Path("/workspace/pdf-test/baidu/output/synth18d"),
        "main": "参考：方案E（本轮之前冻结）",
        "extra": ["推荐组合（含纠错）"],
    },
    {
        "name": "synth18e",
        "sdir": Path("/workspace/pdf-test/synth18e"),
        "odir": Path("/workspace/pdf-test/baidu/output/synth18e"),
        "main": "方案E",
        "extra": ["推荐组合（含纠错）"],
    },
]

# 口径：名字 -> 开关（None 表示直接取 scores.json / 用原函数核对）
MODES = [
    ("完整主口径", dict(allow_vertical=True, allow_header=True, allow_seal_leak=True)),
    ("关掉竖排", dict(allow_vertical=False, allow_header=True, allow_seal_leak=True)),
    ("关掉表头", dict(allow_vertical=True, allow_header=False, allow_seal_leak=True)),
    ("关掉章混入", dict(allow_vertical=True, allow_header=True, allow_seal_leak=False)),
    ("三项全关", dict(allow_vertical=False, allow_header=False, allow_seal_leak=False)),
]


def err(x):
    return int(x["D"]) + int(x["S"]) + int(x["I"])


def pack(overall):
    return {
        "readout": overall["readout"],
        "cer": overall["cer"],
        "D": overall["D"],
        "S": overall["S"],
        "I": overall["I"],
        "ref": overall["ref"],
        "errors": err(overall),
    }


def same_pack(a, b):
    keys = ("readout", "cer", "D", "S", "I", "ref")
    return all(a[k] == b[k] for k in keys)


def json_vs_txt_ref(bs, rj, refs_full):
    """比较 JSON 重建参照 与 严格口径所用 txt 参照 的逐页差异。"""
    refp = T.ref_pages_from_json(bs, rj, "in", allow_vertical=False, allow_header=False)
    diffs = []
    for p in sorted(refs_full):
        rs_txt, _ = bs.ref_chars(refs_full[p])
        rs_json, _ = bs.ref_chars(refp[p])
        seals_txt = [bs.norm(s) for s in refs_full[p]["seals"]]
        seals_json = [bs.norm(s) for s in refp[p]["seals"]]
        if rs_txt != rs_json or seals_txt != seals_json:
            diffs.append({
                "page": p,
                "txt_len": len(rs_txt),
                "json_len": len(rs_json),
                "body_equal": rs_txt == rs_json,
                "seals_txt": seals_txt,
                "seals_json": seals_json,
            })
    return diffs


def pred_flatten_diff(bs, pred):
    """严格口径把各 section 直接拼接再 norm；主口径先按行切开再拼接。看两者是否相同。"""
    diffs = []
    for p, pp in pred.items():
        strict = bs.norm("".join(s["text"] for s in pp.get("sections") or []))
        lines = [bs.norm(x) for s in pp.get("sections") or [] for x in (s["text"] or "").split("\n")]
        lines = [x for x in lines if x]
        tol = "".join(lines)  # 已是 norm 后的行
        if strict != tol:
            diffs.append({"page": p, "strict_len": len(strict), "tol_len": len(tol)})
    return diffs


def per_page_errors(sc):
    out = {}
    for p, v in sc["per_page"].items():
        o = v["overall"]
        out[int(p)] = {
            "D": o["deletions"], "S": o["substitutions"], "I": o["insertions"],
            "errors": o["deletions"] + o["substitutions"] + o["insertions"],
            "ref": o["ref_chars"],
            "readout": round(o["readout"] * 100, 2) if o.get("readout") is not None else None,
            "cer": round(o["cer"] * 100, 2) if o.get("cer") is not None else None,
        }
    return out


def score_one(bs, pr, rj, mp, flags):
    sc = T.score_tolerant(bs, pr, rj, mp, "in", **flags)
    return pack(T.summ(sc)["overall"]), sc


def run_set(item):
    name, sdir, odir = item["name"], item["sdir"], item["odir"]
    pred_path = odir / "pred_pages.json"
    scores_path = odir / "scores.json"
    preds = json.loads(pred_path.read_text(encoding="utf-8"))
    official = json.loads(scores_path.read_text(encoding="utf-8"))
    bc, bs, hs, refs, rj, mp, rng = SB.load_bs(sdir)

    versions = [item["main"]] + list(item["extra"])
    out_versions = {}
    notes = []

    ref_diffs = json_vs_txt_ref(bs, rj, refs["full"])
    if ref_diffs:
        notes.append(f"JSON 参照与 txt 参照有 {len(ref_diffs)} 页不一致：页 {[d['page'] for d in ref_diffs]}")
    else:
        notes.append("三项全关时，JSON 重建参照与严格口径所用 txt 参照（正文+印章）完全一致。")

    for ver in versions:
        if ver not in preds:
            notes.append(f"预测里没有版本「{ver}」，跳过。")
            out_versions[ver] = {"missing": True}
            continue
        pr = preds[ver]
        off = official["results"][ver]
        off_strict = pack(off["strict"])
        off_tol = pack(off["tol_in"])

        # 与原函数核对完整主口径
        orig_tol = pack(T0.summ(T0.score_tolerant(bs, pr, rj, mp, "in"))["overall"])
        computed = {}
        raw_sc = {}
        for mode_name, flags in MODES:
            packed, sc = score_one(bs, pr, rj, mp, flags)
            computed[mode_name] = packed
            raw_sc[mode_name] = sc

        match_official = same_pack(computed["完整主口径"], off_tol)
        match_orig_fn = same_pack(computed["完整主口径"], orig_tol)
        if not match_official:
            notes.append(f"{ver}：完整主口径计算结果与 scores.json 的 tol_in 不一致：算得 {computed['完整主口径']}，官方 {off_tol}")
        else:
            notes.append(f"{ver}：完整主口径与 scores.json 的 tol_in 一致（错字 {off_tol['errors']}）。")
        if not match_orig_fn:
            notes.append(f"{ver}：完整主口径与原 tolerant_score.score_tolerant 不一致。")

        flat_diffs = pred_flatten_diff(bs, pr)
        if flat_diffs:
            notes.append(f"{ver}：有 {len(flat_diffs)} 页预测正文在「按 section 拼接」和「按行切开再拼接」下不同。")

        # 严格口径逐页（直接用 score_system，应与 scores.json 一致）
        st = bs.score_system(pr, refs["full"], mp, None)
        st_pack = bc.summ(st)["overall"]
        st_pack = pack({
            "readout": st_pack["readout"], "cer": st_pack["cer"],
            "D": st_pack["D"], "S": st_pack["S"], "I": st_pack["I"], "ref": st_pack["ref"],
        })
        if not same_pack(st_pack, off_strict):
            notes.append(f"{ver}：重算严格口径与 scores.json 不一致：算得 {st_pack}，官方 {off_strict}")

        full_err = computed["完整主口径"]["errors"]
        contrib = {
            "页边竖排": computed["关掉竖排"]["errors"] - full_err,
            "发票表头": computed["关掉表头"]["errors"] - full_err,
            "印章混入": computed["关掉章混入"]["errors"] - full_err,
        }
        all_off = computed["三项全关"]["errors"]
        residual = all_off - off_strict["errors"]
        additive = sum(contrib.values())
        interaction = (all_off - full_err) - additive

        # 逐页：三项全关 vs 严格，找出残余差距来自哪些页
        pp_alloff = per_page_errors(raw_sc["三项全关"])
        pp_strict = per_page_errors(st)
        page_resid = []
        for p in sorted(set(pp_alloff) | set(pp_strict)):
            a = pp_alloff.get(p, {}).get("errors", 0)
            b = pp_strict.get(p, {}).get("errors", 0)
            if a != b:
                page_resid.append({
                    "page": p,
                    "三项全关": pp_alloff.get(p),
                    "严格": pp_strict.get(p),
                    "差额": a - b,
                })

        # 单元/挪章明细（完整主口径），便于解释贡献
        unit_cost = {"margin_vertical": 0, "invoice_header": 0}
        for u in raw_sc["完整主口径"]["units"]:
            unit_cost[u["kind"]] = unit_cost.get(u["kind"], 0) + u["deletions"] + u["substitutions"] + u["insertions"]
        seal_moved_chars = sum(len(x["moved_to_seal"]) for x in raw_sc["完整主口径"]["seal_moves"])
        seal_moves = len(raw_sc["完整主口径"]["seal_moves"])

        # 关掉竖排后，竖排单元不再单独计分；看差异页
        def page_delta(mode):
            pp = per_page_errors(raw_sc[mode])
            pp0 = per_page_errors(raw_sc["完整主口径"])
            rows = []
            for p in sorted(set(pp) | set(pp0)):
                a, b = pp.get(p, {}).get("errors", 0), pp0.get(p, {}).get("errors", 0)
                if a != b:
                    rows.append({"page": p, "完整主口径": b, mode: a, "多出": a - b})
            return rows

        out_versions[ver] = {
            "official_strict": off_strict,
            "official_tol_in": off_tol,
            "orig_fn_tol_in": orig_tol,
            "match_official_tol_in": match_official,
            "match_orig_fn": match_orig_fn,
            "modes": computed,
            "contrib": contrib,
            "residual_all_off_minus_strict": residual,
            "contrib_sum": additive,
            "interaction_vs_sum": interaction,
            "unit_cost_in_full": unit_cost,
            "seal_moves": seal_moves,
            "seal_moved_chars": seal_moved_chars,
            "page_delta": {m: page_delta(m) for m, _ in MODES if m != "完整主口径"},
            "page_residual_all_off_vs_strict": page_resid,
            "pred_flatten_diffs": flat_diffs,
        }

    return {
        "name": name,
        "main": item["main"],
        "ref_json_vs_txt": ref_diffs,
        "notes": notes,
        "versions": out_versions,
    }


def fmt_score(x):
    if not x or x.get("missing"):
        return "—"
    return f"{x['readout']:.2f}% ／ {x['cer']:.2f}%（{x['D']}+{x['S']}+{x['I']}={x['errors']}）"


def fmt_err(x):
    if not x or x.get("missing"):
        return "—"
    if isinstance(x, int):
        return str(x)
    return str(x["errors"])


def report(results):
    L = ["# 主口径三项放宽的字数贡献（口径消融）", "",
         "只用已有预测计分，不重新识别、不调用外部 API、不改原文件。",
         "错字总数 = 漏字 + 错字 + 多字（D+S+I）。贡献字数 = 关掉该项之后比完整主口径多出的错字数。",
         "签名在两种口径下都计入。", ""]

    L += ["## 一、总表（错字总数）", "",
          "| 测试集 / 版本 | 严格口径 | 完整主口径 | 关掉竖排 | 关掉表头 | 关掉章混入 | 三项全关 | 竖排贡献 | 表头贡献 | 章混入贡献 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        for ver, v in r["versions"].items():
            label = f"{r['name']} · {ver}"
            if v.get("missing"):
                L.append(f"| {label} | （预测中无此版本） |  |  |  |  |  |  |  |  |")
                continue
            c = v["contrib"]
            L.append(
                f"| {label} | {v['official_strict']['errors']} | {v['modes']['完整主口径']['errors']} | "
                f"{v['modes']['关掉竖排']['errors']} | {v['modes']['关掉表头']['errors']} | "
                f"{v['modes']['关掉章混入']['errors']} | {v['modes']['三项全关']['errors']} | "
                f"{c['页边竖排']} | {c['发票表头']} | {c['印章混入']} |"
            )
    L.append("")

    L += ["## 二、各口径读出率 / 错字率（漏+错+多）", ""]
    for r in results:
        L.append(f"### {r['name']}")
        L.append("")
        L.append("| 版本 | 口径 | 读出率 | 错字率 | 漏字 | 错字 | 多字 | 错字总数 | 参照字数 |")
        L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
        for ver, v in r["versions"].items():
            if v.get("missing"):
                L.append(f"| {ver} | （预测中无此版本） |  |  |  |  |  |  |  |")
                continue
            L.append(
                f"| {ver} | 严格口径（scores.json） | {v['official_strict']['readout']:.2f}% | "
                f"{v['official_strict']['cer']:.2f}% | {v['official_strict']['D']} | {v['official_strict']['S']} | "
                f"{v['official_strict']['I']} | {v['official_strict']['errors']} | {v['official_strict']['ref']} |"
            )
            for mode, _ in MODES:
                x = v["modes"][mode]
                L.append(
                    f"| {ver} | {mode} | {x['readout']:.2f}% | {x['cer']:.2f}% | {x['D']} | {x['S']} | {x['I']} | {x['errors']} | {x['ref']} |"
                )
        L.append("")

    L += ["## 三、贡献与残余差距", ""]
    for r in results:
        L.append(f"### {r['name']}")
        L.append("")
        for n in r["notes"]:
            L.append(f"- {n}")
        if r["ref_json_vs_txt"]:
            L.append(f"- JSON 与 txt 参照差异页详情：{json.dumps(r['ref_json_vs_txt'], ensure_ascii=False)}")
        for ver, v in r["versions"].items():
            if v.get("missing"):
                continue
            L.append(f"- **{ver}**")
            L.append(f"  - 三项各自贡献（关掉后多出的错字）：竖排 {v['contrib']['页边竖排']} 字，表头 {v['contrib']['发票表头']} 字，章混入 {v['contrib']['印章混入']} 字，合计 {v['contrib_sum']} 字。")
            L.append(f"  - 三项全关比完整主口径多出 {v['modes']['三项全关']['errors'] - v['modes']['完整主口径']['errors']} 字；与三项贡献之和相差 {v['interaction_vs_sum']} 字（正数表示开关之间有叠加/交互，不能简单相加）。")
            L.append(f"  - 三项全关（{v['modes']['三项全关']['errors']} 字）与严格口径（{v['official_strict']['errors']} 字）还差 {v['residual_all_off_minus_strict']} 字。")
            L.append(f"  - 完整主口径下，竖排单元自身计分代价 {v['unit_cost_in_full'].get('margin_vertical', 0)} 字，表头单元自身计分代价 {v['unit_cost_in_full'].get('invoice_header', 0)} 字；抽出并入印章的片段 {v['seal_moves']} 处、共 {v['seal_moved_chars']} 字（这些字从正文挪到印章栏，印章不计总分）。")
            if v["page_residual_all_off_vs_strict"]:
                L.append("  - 三项全关与严格口径逐页差额（只列不同的页）：")
                for d in v["page_residual_all_off_vs_strict"]:
                    L.append(f"    - 第 {d['page']} 页：三项全关 {d['三项全关']['errors']} 字（D{d['三项全关']['D']}/S{d['三项全关']['S']}/I{d['三项全关']['I']}），严格 {d['严格']['errors']} 字（D{d['严格']['D']}/S{d['严格']['S']}/I{d['严格']['I']}），差额 {d['差额']}")
            else:
                L.append("  - 三项全关与严格口径逐页错字数完全相同。")
            for mode in ("关掉竖排", "关掉表头", "关掉章混入"):
                rows = v["page_delta"][mode]
                if not rows:
                    L.append(f"  - {mode}：没有页面的错字数发生变化（该项放宽在本版本上贡献为 0）。")
                    continue
                L.append(f"  - {mode} 发生变化的页（完整主口径 → 关掉后，多出的错字）：")
                for d in rows:
                    L.append(f"    - 第 {d['page']} 页：{d['完整主口径']} → {d[mode]}，多出 {d['多出']}")
        L.append("")

    L += ["## 四、脚本与命令", "",
          "- 计分副本（含三项开关，原文件未改）：`/workspace/pdf-test/baidu/scripts/tolerant_score_ablation.py`",
          "- 消融入口：`/workspace/pdf-test/baidu/scripts/tolerant_score_ablation_run.py`",
          "- 运行命令：`/workspace/pdf-test/baidu/.venv/bin/python /workspace/pdf-test/baidu/scripts/tolerant_score_ablation_run.py`（系统 python 缺 yaml，需用项目虚拟环境）",
          "- 本报告与数字还写入：`/workspace/pdf-test/baidu/output/ablation_tolerant/报告_中文.md` 和 `results.json`。",
          ""]
    return "\n".join(L) + "\n"


def main():
    results = [run_set(item) for item in SETS]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    txt = report(results)
    (OUT / "报告_中文.md").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
