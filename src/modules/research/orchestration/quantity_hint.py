"""本地定制：会话报告的数量参考（确定性计算，不经模型）。

投研图刻意不接触账户资金与真实持仓（engine 将持仓二值化、建议 quantity 恒为
null），导致买/加/减仓通知没有具体数量。本模块按账户快照、单票基准仓位比例与
建议置信度估算数量，追加到报告「操作建议」之后：A 股与 ETF 以整百为基准，
港股提示按券商手数取整，美股按整股；800xxx 统计指标不可交易，不出数量。

单票基准仓位比例默认 10%，可在 Agent 配置的 ``position_target_pct`` 中覆盖。
每次调整比例为基准仓位比例乘建议置信度，基准仓位不是持仓上限。
"""
from __future__ import annotations

import math

DEFAULT_BASE_PCT = 0.10
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
    return DEFAULT_BASE_PCT


def _normalize_confidence(value) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return min(1.0, max(0.0, float(value)))
    return 1.0


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
        confidence = _normalize_confidence(suggestion.get("confidence"))
        base_limit = _floor_to_lot(equity * pct / price, lot)
        adjustment = _floor_to_lot(equity * pct * confidence / price, lot)
        if confidence > 0 and adjustment < lot and base_limit >= lot:
            adjustment = lot
        cap = _floor_to_lot(cash * CASH_USE_RATIO / price, lot)

        def money(shares):
            return f"{shares * price:,.0f}"

        if action in {"buy", "add"} and position <= 0:
            n = min(adjustment, cap)
            if n < lot:
                hints[key] = tr(
                    f"本次参考数量或可用资金不足一手（{lot} 股），暂不建议开仓。",
                    f"Reference size or available funds below one lot ({lot}); no entry sizing.")
            else:
                hints[key] = tr(
                    f"建议买入 {n} 股（约 {money(n)}，本次约占总资产 {n * price / equity:.0%}）{lot_note}",
                    f"Buy ~{n} shares (≈{money(n)}, {n * price / equity:.0%} of equity for this adjustment){lot_note}")
        elif action in {"buy", "add"}:
            n = min(adjustment, cap)
            if n < lot:
                hints[key] = tr(
                    f"本次参考数量或可用资金不足一手（{lot} 股），暂无法加仓。",
                    f"Reference size or available funds below one lot ({lot}); top-up unavailable.")
            else:
                hints[key] = tr(
                    f"建议加仓至 {position + n} 股（当前 {position} 股，再买入 {n} 股，约 {money(n)}）{lot_note}",
                    f"Top up to {position + n} shares (current {position}, buy {n} more, ≈{money(n)}){lot_note}")
        elif action == "reduce":
            if position <= 0:
                hints[key] = tr("当前未持有该股票，减仓信号不适用。", "No position held; reduce signal not applicable.")
            else:
                max_reduce = _floor_to_lot(position - lot, lot)
                n = min(adjustment, max_reduce)
                if n < lot:
                    hints[key] = tr(
                        f"当前持仓 {position} 股；本次参考数量不足一手，或减仓后无法保留一手（{lot} 股），暂无法减仓。",
                        f"Current {position} shares; the reference size is below one lot or must retain one lot ({lot}); trim unavailable.")
                else:
                    hints[key] = tr(
                        f"建议减仓至 {position - n} 股（当前 {position} 股，卖出 {n} 股）{lot_note}",
                        f"Trim to {position - n} shares (current {position}, sell {n}){lot_note}")
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
    header = (f"## 数量参考（单票基准仓位 {pct:.0%} × 建议置信度，按账户快照估算，金额为当地货币，仅供参考）" if not en
              else f"## Position sizing (single-asset base weight {pct:.0%} × recommendation confidence, estimated from the account snapshot in local currency, for reference only)")
    lines = [f"- **{names.get(key, key)}**：{text}" for key, text in hints.items()]
    return "\n".join([header, *lines])
