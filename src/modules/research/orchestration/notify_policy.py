"""本地定制：盘中通知门槛与节流判别（仅买卖动作推送，按动作集合节流）。

上游原生行为：推送门槛为任一建议 should_alert（含 attention/review，观望类也
可能触发）；盘中节流判别符为空串，整个监控列表 30 分钟内最多一条推送——其他
股票新触发或同股动作翻转都会被压住至多 30 分钟。

本模块恢复「每股票每动作」语义：仅 buy/add/reduce/sell 允许推送；判别符取本次
可执行动作集合的哈希——集合不变 30 分钟内抑制，集合变化（新股触发/动作翻转）
立即推送，观望类变化不影响判别符。
"""
from __future__ import annotations

import hashlib
import json

NOTIFY_ACTIONS = {"buy", "add", "reduce", "sell"}


def actionable_pairs(raw_data: dict | None) -> list[str]:
    """本次建议中的可执行动作集合，元素形如 'CN:600519:sell'，已排序去重序。"""
    suggestions = (raw_data or {}).get("suggestions") or {}
    return sorted(
        f"{key}:{suggestion.get('action')}"
        for key, suggestion in suggestions.items()
        if isinstance(suggestion, dict) and suggestion.get("action") in NOTIFY_ACTIONS
    )


def intraday_notify_allowed(raw_data: dict | None) -> bool:
    """盘中推送门槛：存在买/加/减/卖动作才允许通知。"""
    return bool(actionable_pairs(raw_data))


def intraday_discriminator(raw_data: dict | None) -> str:
    """盘中节流判别符：动作集合哈希；无可执行动作时为空串（此时也不会推送）。"""
    pairs = actionable_pairs(raw_data)
    if not pairs:
        return ""
    return hashlib.sha256(json.dumps(pairs, ensure_ascii=False).encode()).hexdigest()[:16]
