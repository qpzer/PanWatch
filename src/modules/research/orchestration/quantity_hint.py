"""本地定制：会话报告的数量参考（确定性计算，不经模型）。

投研图刻意不接触账户资金与真实持仓（engine 将持仓二值化、建议 quantity 恒为
null），导致买/加/减仓通知没有具体数量。本模块按账户快照与目标仓位比例估算
数量，追加到报告「操作建议」之后：A 股与 ETF 以整百为基准，港股提示按券商
手数取整，美股按整股；800xxx 统计指标不可交易，不出数量。

目标仓位比例默认 15%，可在 Agent 配置的 ``position_target_pct`` 中覆盖。
"""
from __future__ import annotations

import math

DEFAULT_TARGET_PCT = 0.15
CASH_USE_RATIO = 0.95
CN_LOT = 100

_ACTIONS = {"buy", "add", "reduce", "sell"}


def _is_cn_statistical_index(symbol: str) -> bool:
    # 800xxx 统计指标（如 800005 A股平均股价）不可交易
    return len(symbol) == 6 and symbol.startswith("800")


def _floor_to_lot(shares: float, lot: int) -> int:
    if not math.isfinite(shares) or shares <= 0:
        return 0
    return int(math.floor(shares / lot) * lot)


def _normalize_pct(target_pct) -> float:
    if isinstance(target_pct, (int, float)) and not isinstance(target_pct, bool) and 0 < target_pct <= 1:
        return float(target_pct)
    return DEFAULT_TARGET_PCT


def build_quantity_hints(suggestions, *, securities, portfolio, target_pct=None, language="zh-CN") -> dict:
    """按可执行动作逐股生成数量提示，返回 {stock_key: 文本}（保持传入顺序）。

    suggestions: present() 归一化后的 {stock_key: suggestion}
    securities: prepared.securities（Security，含 held_quantity/price/name）
    portfolio: prepared.portfolio（to_jsonable 后的账户快照）
    """
    en = language == "en-US"

    def tr(zh, english):
        return english if en else zh

    pct = _normalize_pct(target_pct)
    securities = list(securities or [])
    held = {s.key: int(s.held_quantity or 0) for s in securities}
    prices = {s.key: s.price for s in securities}
    accounts = (portfolio or {}).get("accounts") or []
    cash = sum(float(a.get("available_funds") or 0) for a in accounts)
    cost_total = sum(
        float(p.get("quantity") or 0) * float(p.get("cost_price") or 0)
        for a in accounts
        for p in (a.get("positions") or [])
    )
    equity = cash + cost_total

    hints = {}
    for key, suggestion in (suggestions or {}).items():
        if not isinstance(suggestion, dict):
            continue
        action = suggestion.get("action")
        if action not in _ACTIONS:
            continue
        market, _, symbol = key.partition(":")
        price = prices.get(key)
        position = held.get(key, 0)
        lot = CN_LOT if market == "CN" else 1
        lot_note = tr("（港股请按券商手数取整）", " (round to board lots)") if market == "HK" else ""
        if market == "CN" and _is_cn_statistical_index(symbol):
            hints[key] = tr("统计指标，不可交易，无数量建议。", "Statistical index, not tradable; no sizing.")
            continue
        if not price or price <= 0:
            hints[key] = tr("暂无报价，无法估算数量。", "No quote available; sizing unavailable.")
            continue
        if equity <= 0:
            hints[key] = tr("未配置账户与资金，无法计算数量参考。", "No account funds configured; sizing unavailable.")
            continue
        target = _floor_to_lot(equity * pct / price, lot)
        cap = _floor_to_lot(cash * CASH_USE_RATIO / price, lot)

        def money(shares):
            return f"{shares * price:,.0f}"

        if action in {"buy", "add"} and position <= 0:
            # 新建仓：买到目标仓位，且不超可用资金
            n = min(target, cap)
            if n < lot:
                hints[key] = tr(
                    f"目标仓位或可用资金不足一手（{lot} 股），暂不建议开仓。",
                    f"Target position or funds below one lot ({lot}); no entry sizing.")
            else:
                hints[key] = tr(
                    f"建议买入 {n} 股（约 {money(n)}，约占总资产 {n * price / equity:.0%}）{lot_note}",
                    f"Buy ~{n} shares (≈{money(n)}, {n * price / equity:.0%} of equity){lot_note}")
        elif action in {"buy", "add"}:
            # 已持仓的买入/加仓：补到目标仓位
            if position >= target:
                hints[key] = tr(
                    f"当前持仓 {position} 股已达目标仓位（约 {target} 股），无需加仓。",
                    f"Current {position} shares already meet the target (~{target}); no top-up needed.")
                continue
            extra = min(_floor_to_lot(target - position, lot), cap)
            if extra < lot:
                hints[key] = tr(
                    f"可用资金不足一手（{lot} 股），暂无法加仓。",
                    f"Available funds below one lot ({lot}); top-up unavailable.")
            else:
                hints[key] = tr(
                    f"建议加仓至 {position + extra} 股（再买入 {extra} 股，约 {money(extra)}）{lot_note}",
                    f"Top up to {position + extra} shares (buy {extra} more, ≈{money(extra)}){lot_note}")
        elif action == "reduce":
            if position <= 0:
                hints[key] = tr("当前未持有该股票，减仓信号不适用。", "No position held; reduce signal not applicable.")
            elif position > target:
                hints[key] = tr(
                    f"建议减仓至 {target} 股（卖出 {position - target} 股）{lot_note}",
                    f"Trim to {target} shares (sell {position - target}){lot_note}")
            else:
                hints[key] = tr(
                    f"当前持仓 {position} 股未超目标仓位（约 {target} 股），如执行减仓可自行把握。",
                    f"Current {position} shares below target (~{target}); trim at your discretion.")
        else:  # sell
            if position > 0:
                hints[key] = tr(f"建议清仓，卖出全部 {position} 股。{lot_note}",
                                f"Close the position: sell all {position} shares.{lot_note}")
            else:
                hints[key] = tr("当前未持有该股票，卖出信号不适用。", "No position held; sell signal not applicable.")
    return hints


def format_quantity_section(hints, *, securities, target_pct=None, language="zh-CN") -> str:
    """把数量提示渲染为追加在「操作建议」之后的 Markdown 小节。"""
    if not hints:
        return ""
    en = language == "en-US"
    pct = _normalize_pct(target_pct)
    names = {s.key: (s.name or s.key) for s in securities or []}
    header = (f"## 数量参考（按目标仓位 {pct:.0%} 与账户快照估算，金额为当地货币，仅供参考）" if not en
              else f"## Position sizing (estimated at {pct:.0%} target weight from the account snapshot, local currency, for reference only)")
    lines = [f"- **{names.get(key, key)}**：{text}" for key, text in hints.items()]
    return "\n".join([header, *lines])
