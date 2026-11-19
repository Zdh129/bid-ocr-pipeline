"""按官方单价估算费用，并执行软/硬上限。密钥与请求体不进入本模块日志。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class CostCapExceeded(RuntimeError):
    """硬上限已到，必须停止后续计费调用。"""


@dataclass
class CostTracker:
    cny_per_usd: float = 7.2
    soft_cap_usd: float = 5.0
    hard_cap_usd: float = 10.0
    prices_cny: dict[str, float] = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)
    total_cny: float = 0.0
    soft_warned: bool = False

    @classmethod
    def from_config(cls, cfg: dict) -> "CostTracker":
        cost = cfg.get("cost") or {}
        return cls(
            cny_per_usd=float(cost.get("cny_per_usd") or 7.2),
            soft_cap_usd=float(cost.get("soft_cap_usd") or 5.0),
            hard_cap_usd=float(cost.get("hard_cap_usd") or 10.0),
            prices_cny=dict(cost.get("prices_cny") or {}),
        )

    def usd(self, cny: float | None = None) -> float:
        value = self.total_cny if cny is None else cny
        return value / self.cny_per_usd if self.cny_per_usd else 0.0

    def price_of(self, api: str) -> float:
        return float(self.prices_cny.get(api) or 0.0)

    def can_charge(self, api: str, units: float = 1.0) -> tuple[bool, str]:
        add = self.price_of(api) * units
        future_usd = self.usd(self.total_cny + add)
        if future_usd > self.hard_cap_usd + 1e-12:
            return False, "hard_cap"
        return True, "ok"

    def add(
        self,
        *,
        api: str,
        units: float = 1.0,
        success: bool,
        elapsed_sec: float,
        page: int | None = None,
        filename: str | None = None,
        dry_run: bool = False,
        extra: dict | None = None,
    ) -> dict:
        """成功调用才计费。dry_run 只记账 0 元。"""
        cny = 0.0 if dry_run or not success else self.price_of(api) * units
        future_usd = self.usd(self.total_cny + cny)
        if cny and future_usd > self.hard_cap_usd + 1e-12:
            raise CostCapExceeded(
                f"累计费用将超过硬上限 ${self.hard_cap_usd:.2f}，已停止后续调用"
            )
        self.total_cny += cny
        rec: dict[str, Any] = {
            "api": api,
            "units": units,
            "success": success,
            "dry_run": dry_run,
            "cny": round(cny, 6),
            "usd": round(self.usd(cny), 6),
            "elapsed_sec": round(elapsed_sec, 4),
            "page": page,
            "filename": filename,
            "cumulative_cny": round(self.total_cny, 6),
            "cumulative_usd": round(self.usd(), 6),
        }
        if extra:
            rec["extra"] = extra
        rec["soft_cap_hit"] = self.usd() >= self.soft_cap_usd
        rec["hard_cap_hit"] = self.usd() >= self.hard_cap_usd
        if rec["soft_cap_hit"] and not self.soft_warned:
            self.soft_warned = True
            rec["soft_cap_warning"] = True
        self.calls.append(rec)
        return rec

    def summary(self) -> dict:
        by_api: dict[str, dict] = {}
        for c in self.calls:
            slot = by_api.setdefault(
                c["api"],
                {"calls": 0, "success": 0, "cny": 0.0, "elapsed_sec": 0.0},
            )
            slot["calls"] += 1
            slot["success"] += int(bool(c["success"]))
            slot["cny"] += c["cny"]
            slot["elapsed_sec"] += c["elapsed_sec"]
        for slot in by_api.values():
            slot["cny"] = round(slot["cny"], 6)
            slot["usd"] = round(self.usd(slot["cny"]), 6)
            slot["elapsed_sec"] = round(slot["elapsed_sec"], 4)
        return {
            "total_cny": round(self.total_cny, 6),
            "total_usd": round(self.usd(), 6),
            "cny_per_usd_estimate": self.cny_per_usd,
            "soft_cap_usd": self.soft_cap_usd,
            "hard_cap_usd": self.hard_cap_usd,
            "soft_cap_hit": self.usd() >= self.soft_cap_usd,
            "hard_cap_hit": self.usd() >= self.hard_cap_usd,
            "n_calls": len(self.calls),
            "by_api": by_api,
            "calls": self.calls,
        }
