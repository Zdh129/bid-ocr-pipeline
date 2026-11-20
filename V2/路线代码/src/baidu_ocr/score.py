"""计分器：以 ChatGPT 网页版识别结果为满分参照。

口径：NFKC 规范化、忽略空白、保留标点；参照里的【?】从参照和计分中排除。
读出率 = 对齐后正确匹配的参照字符数 / 参照字符数。
CER = (删除 + 替换 + 插入) / 参照字符数。删除即漏字，单独统计。
"""

from __future__ import annotations

import unicodedata
from typing import Iterable

UNCERTAIN_TOKEN = "【?】"


def normalize(text: str, uncertain_token: str = UNCERTAIN_TOKEN) -> str:
    s = unicodedata.normalize("NFKC", text or "")
    if uncertain_token:
        s = s.replace(uncertain_token, "")
    # 忽略所有空白（含全角空格、换行、制表），保留标点
    return "".join(ch for ch in s if not ch.isspace())


def levenshtein_alignment(pred: str, ref: str) -> dict:
    """带回溯的 Levenshtein 对齐，统计 match / substitution / deletion / insertion。

    deletion：参照有、预测无（漏字）。
    insertion：预测有、参照无。
    substitution：同一位置字符不同。
    """
    n, m = len(pred), len(ref)
    # dp[i][j] = cost; 用 1 维滚 + 指针矩阵
    prev = list(range(m + 1))
    ptr = [[0] * (m + 1) for _ in range(n + 1)]
    # ptr: 0=match/sub from diag, 1=delete (from left, consume ref), 2=insert (from up, consume pred)
    for j in range(1, m + 1):
        ptr[0][j] = 1  # deletions from empty pred
    for i in range(1, n + 1):
        cur = [i]
        ptr[i][0] = 2
        ca = pred[i - 1]
        for j in range(1, m + 1):
            cb = ref[j - 1]
            sub_cost = prev[j - 1] + (0 if ca == cb else 1)
            delete_cost = cur[j - 1] + 1  # skip ref char
            insert_cost = prev[j] + 1  # skip pred char
            best = sub_cost
            op = 0
            if delete_cost < best or (delete_cost == best and op != 1 and delete_cost < sub_cost):
                # prefer match when tied with equal cost; when costs equal prefer diag
                pass
            # Choose min; tie-break: match/sub, then delete, then insert (stable, deterministic)
            candidates = [(sub_cost, 0), (delete_cost, 1), (insert_cost, 2)]
            best, op = min(candidates, key=lambda x: (x[0], x[1]))
            cur.append(best)
            ptr[i][j] = op
        prev = cur
    distance = prev[m]

    # backtrace from n,m
    i, j = n, m
    matches = substitutions = deletions = insertions = 0
    ops: list[tuple[str, str | None, str | None]] = []
    while i > 0 or j > 0:
        if i == 0:
            deletions += 1
            ops.append(("delete", None, ref[j - 1]))
            j -= 1
            continue
        if j == 0:
            insertions += 1
            ops.append(("insert", pred[i - 1], None))
            i -= 1
            continue
        op = ptr[i][j]
        if op == 0:
            if pred[i - 1] == ref[j - 1]:
                matches += 1
                ops.append(("match", pred[i - 1], ref[j - 1]))
            else:
                substitutions += 1
                ops.append(("substitute", pred[i - 1], ref[j - 1]))
            i -= 1
            j -= 1
        elif op == 1:
            deletions += 1
            ops.append(("delete", None, ref[j - 1]))
            j -= 1
        else:
            insertions += 1
            ops.append(("insert", pred[i - 1], None))
            i -= 1
    ops.reverse()
    return {
        "edit_distance": distance,
        "matches": matches,
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "pred_chars": n,
        "ref_chars": m,
        "ops": ops,
    }


def score_text(
    pred: str,
    ref: str,
    *,
    uncertain_token: str = UNCERTAIN_TOKEN,
    section: str | None = None,
) -> dict:
    p = normalize(pred, uncertain_token)
    r = normalize(ref, uncertain_token)
    aln = levenshtein_alignment(p, r)
    ref_n = aln["ref_chars"]
    if ref_n == 0:
        readout = 1.0 if aln["pred_chars"] == 0 else 0.0
        cer = 0.0 if aln["pred_chars"] == 0 else 1.0
        note = "empty_reference_after_normalize"
    else:
        readout = aln["matches"] / ref_n
        cer = (aln["deletions"] + aln["substitutions"] + aln["insertions"]) / ref_n
        note = None
    out = {
        "section": section,
        "ref_chars": ref_n,
        "pred_chars": aln["pred_chars"],
        "matches": aln["matches"],
        "deletions": aln["deletions"],  # 漏字
        "substitutions": aln["substitutions"],
        "insertions": aln["insertions"],
        "edit_distance": aln["edit_distance"],
        "readout": round(readout, 6),
        "cer": round(cer, 6),
        "char_accuracy": round(max(0.0, 1.0 - cer), 6),
    }
    if note:
        out["note"] = note
    return out


def score_by_sections(
    pred_sections: dict[str, str],
    ref_sections: dict[str, str],
    *,
    uncertain_token: str = UNCERTAIN_TOKEN,
) -> dict:
    """正文/表格/印章分开计分（仅当参照能提供对应分区）。另给 overall。"""
    # overall：有参照的分区拼接；没有分区则用 overall 键
    results = {}
    overall_pred_parts = []
    overall_ref_parts = []
    for key in ("body", "table", "seal", "overall"):
        if key == "overall":
            continue
        if key in ref_sections and ref_sections[key] is not None:
            results[key] = score_text(
                pred_sections.get(key, ""),
                ref_sections[key],
                uncertain_token=uncertain_token,
                section=key,
            )
            overall_pred_parts.append(pred_sections.get(key, ""))
            overall_ref_parts.append(ref_sections[key])
    if "overall" in ref_sections and ref_sections["overall"] is not None:
        results["overall"] = score_text(
            pred_sections.get("overall", ""),
            ref_sections["overall"],
            uncertain_token=uncertain_token,
            section="overall",
        )
    elif overall_ref_parts:
        results["overall"] = score_text(
            "\n".join(overall_pred_parts),
            "\n".join(overall_ref_parts),
            uncertain_token=uncertain_token,
            section="overall",
        )
    else:
        results["overall"] = score_text(
            pred_sections.get("overall", "") or pred_sections.get("body", ""),
            "",
            uncertain_token=uncertain_token,
            section="overall",
        )
        results["overall"]["note"] = "no_chatgpt_reference"
    return results


def extract_pred_sections(document: dict) -> dict[str, str]:
    body, table, seal = [], [], []
    for b in document.get("blocks") or []:
        typ = b.get("type")
        if typ == "table":
            cells = ((b.get("table") or {}).get("cells")) or []
            table.append("\n".join(c.get("text") or "" for c in cells) or (b.get("text") or ""))
        elif typ == "seal":
            seal.append(b.get("text") or "")
        elif typ in {"header", "footer", "page_number"}:
            body.append(b.get("text") or "")
        else:
            body.append(b.get("text") or "")
    return {
        "body": "\n".join(body),
        "table": "\n".join(table),
        "seal": "\n".join(seal),
        "overall": "\n".join(body + table + seal),
    }


def load_reference(path) -> dict:
    """加载 ChatGPT 网页参照。支持：
    - 纯文本（整体参照）
    - JSON：{pages:[{page, text, body, table, seal}]} 或 {text: ...}
    不接受文字层或其他 OCR 结果冒充。调用方须先做 SHA256 校验。
    """
    from pathlib import Path
    import json

    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(raw)
        return data
    return {"text": raw, "pages": []}
