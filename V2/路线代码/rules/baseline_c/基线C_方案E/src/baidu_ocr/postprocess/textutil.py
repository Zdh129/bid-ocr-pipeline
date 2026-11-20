"""规范化与对齐工具（与 blind20 计分口径一致：NFKC、忽略空白）。"""
from __future__ import annotations

import unicodedata

UNC = "【?】"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").replace(unicodedata.normalize("NFKC", UNC), "")
    return "".join(ch for ch in s if not ch.isspace())


def norm_with_map(s: str) -> tuple[str, list[int]]:
    """逐字 NFKC 并去空白；返回 (规范串, 每个规范字对应的原串下标)。"""
    out, idx = [], []
    for i, ch in enumerate(s or ""):
        for c in unicodedata.normalize("NFKC", ch):
            if not c.isspace():
                out.append(c)
                idx.append(i)
    return "".join(out), idx


def insertions(base: str, other: str, free_ends: bool = False) -> tuple[dict[int, str], str, str]:
    """把 other 对齐到 base（编辑距离，带回溯），返回 other 相对 base 的插入 {base 位置: 插入串}。

    free_ends=True 时为半全局对齐：other 首尾多出的部分不计代价，作为 (lead, tail) 单独返回，
    不放进插入表（用于 other 是一段更长文本、只需截取与 base 对应窗口的情形）。
    位置 i 表示插在 base[i] 之前；i == len(base) 表示插在末尾。
    """
    n, m = len(base), len(other)
    INF = 10 ** 9
    # dp[i][j]: base[:i] 与 other[:j] 的最小代价
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for j in range(1, m + 1):
        dp[0][j] = 0 if free_ends else j
    for i in range(1, n + 1):
        dp[i][0] = i
        bi = base[i - 1]
        row, prow = dp[i], dp[i - 1]
        for j in range(1, m + 1):
            v = prow[j - 1] + (0 if bi == other[j - 1] else 1)
            d = prow[j] + 1
            if d < v:
                v = d
            ins = row[j - 1] + 1
            if ins < v:
                v = ins
            row[j] = v
    # 终点
    if free_ends:
        jend = min(range(m + 1), key=lambda j: (dp[n][j], -j)) if m else 0
    else:
        jend = m
    tail = other[jend:] if free_ends else ""
    ins_map: dict[int, list[str]] = {}
    i, j = n, jend
    while i > 0 or j > 0:
        if free_ends and i == 0:
            break
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + (0 if base[i - 1] == other[j - 1] else 1):
            i, j = i - 1, j - 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            i -= 1
        else:
            ins_map.setdefault(i, []).append(other[j - 1])
            j -= 1
    lead = other[:j] if free_ends else ""
    _ = INF
    return {k: "".join(reversed(v)) for k, v in ins_map.items()}, lead, tail
