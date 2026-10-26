"""数量参考（本地定制）：按基准仓位与置信度估算买/加/减/卖数量，A 股整百取整。"""
from types import SimpleNamespace

from src.modules.research.orchestration.quantity_hint import (
    build_quantity_hints,
    format_quantity_section,
)


def _sec(key, name="测试股", price=10.0, held=0):
    return SimpleNamespace(key=key, name=name, price=price, held_quantity=held)


def _portfolio(cash=0.0, positions=()):
    return {"accounts": [{"available_funds": cash, "positions": [
        {"quantity": q, "cost_price": c} for q, c in positions]}]}


class TestBuy:
    def test_buy_rounds_to_lot_and_caps_by_cash(self):
        # equity = 8141.17 + 700*1.439 + 300*3.37 = 10159.47；基准 10% = 1015.95 → 100 股
        hints = build_quantity_hints(
            {"CN:600519": {"action": "buy"}},
            securities=[_sec("CN:600519", "贵州茅台")],
            portfolio=_portfolio(8141.17, [(700, 1.439), (300, 3.37)]),
        )
        assert hints["CN:600519"] == "建议买入 100 股（约 1,000，本次约占总资产 10%）"

    def test_buy_capped_by_available_cash(self):
        # equity = 10000，基准 1000 → 100 股；现金 550 → 可买 50 股→整百 0
        hints = build_quantity_hints(
            {"CN:600519": {"action": "buy"}},
            securities=[_sec("CN:600519")],
            portfolio=_portfolio(550.0, [(1000, 9.45)]),
        )
        assert "不足一手" in hints["CN:600519"]

    def test_buy_below_one_lot_is_rejected(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "buy"}},
            securities=[_sec("CN:600519")],
            portfolio=_portfolio(50.0),
        )
        assert hints["CN:600519"] == "本次参考数量或可用资金不足一手（100 股），暂不建议开仓。"

    def test_buy_scales_base_by_confidence(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "buy", "confidence": 0.8}},
            securities=[_sec("CN:600519")],
            portfolio=_portfolio(100000.0),
        )
        assert hints["CN:600519"] == "建议买入 800 股（约 8,000，本次约占总资产 8%）"

    def test_small_positive_buy_uses_one_lot_within_base_limit(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "buy", "confidence": 0.01}},
            securities=[_sec("CN:600519")],
            portfolio=_portfolio(100000.0),
        )
        assert hints["CN:600519"] == "建议买入 100 股（约 1,000，本次约占总资产 1%）"

    def test_target_pct_override(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "buy"}},
            securities=[_sec("CN:600519")],
            portfolio=_portfolio(100000.0),
            target_pct=0.5,
        )
        assert hints["CN:600519"] == "建议买入 5000 股（约 50,000，本次约占总资产 50%）"


class TestAdd:
    def test_add_scales_base_by_confidence(self):
        # equity = 107000；107000 × 10% × 80% ÷ 10 = 856 股→整百 800；已持 700 → 加至 1500
        hints = build_quantity_hints(
            {"CN:600519": {"action": "add", "confidence": 0.8}},
            securities=[_sec("CN:600519", held=700)],
            portfolio=_portfolio(100000.0, [(700, 10.0)]),
        )
        assert hints["CN:600519"] == "建议加仓至 1500 股（当前 700 股，再买入 800 股，约 8,000）"

    def test_add_at_base_still_adds(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "add"}},
            securities=[_sec("CN:600519", held=1000)],
            portfolio=_portfolio(90000.0, [(1000, 10.0)]),
        )
        assert hints["CN:600519"] == "建议加仓至 2000 股（当前 1000 股，再买入 1000 股，约 10,000）"

    def test_add_above_base_still_adds(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "add"}},
            securities=[_sec("CN:600519", held=2000)],
            portfolio=_portfolio(100000.0, [(2000, 10.0)]),
        )
        assert hints["CN:600519"] == "建议加仓至 3200 股（当前 2000 股，再买入 1200 股，约 12,000）"

    def test_buy_on_held_stock_behaves_like_add(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "buy", "confidence": 0.8}},
            securities=[_sec("CN:600519", held=700)],
            portfolio=_portfolio(100000.0, [(700, 10.0)]),
        )
        assert "加仓至 1500 股" in hints["CN:600519"]


class TestReduceSell:
    def test_reduce_scales_base_by_confidence(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "reduce", "confidence": 0.8}},
            securities=[_sec("CN:600519", held=2000)],
            portfolio=_portfolio(80000.0, [(2000, 10.0)]),
        )
        assert hints["CN:600519"] == "建议减仓至 1200 股（当前 2000 股，卖出 800 股）"

    def test_reduce_below_base_still_reduces_and_keeps_one_lot(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "reduce"}},
            securities=[_sec("CN:600519", held=500)],
            portfolio=_portfolio(100000.0, [(500, 10.0)]),
        )
        assert hints["CN:600519"] == "建议减仓至 100 股（当前 500 股，卖出 400 股）"

    def test_small_positive_reduce_uses_one_lot(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "reduce", "confidence": 0.01}},
            securities=[_sec("CN:600519", held=500)],
            portfolio=_portfolio(100000.0, [(500, 10.0)]),
        )
        assert hints["CN:600519"] == "建议减仓至 400 股（当前 500 股，卖出 100 股）"

    def test_reduce_cannot_sell_the_last_lot(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "reduce"}},
            securities=[_sec("CN:600519", held=100)],
            portfolio=_portfolio(100000.0, [(100, 10.0)]),
        )
        assert "保留一手" in hints["CN:600519"]
        assert "暂无法减仓" in hints["CN:600519"]

    def test_sell_liquidates(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "sell"}},
            securities=[_sec("CN:600519", held=1200)],
            portfolio=_portfolio(0.0, [(1200, 10.0)]),
        )
        assert hints["CN:600519"] == "建议清仓，卖出全部 1200 股。"

    def test_sell_without_position(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "sell"}},
            securities=[_sec("CN:600519", held=0)],
            portfolio=_portfolio(100000.0),
        )
        assert "未持有" in hints["CN:600519"]


class TestGuards:
    def test_cn_statistical_index_is_not_tradable(self):
        hints = build_quantity_hints(
            {"CN:800005": {"action": "buy"}},
            securities=[_sec("CN:800005", "A股平均股价", price=20.0)],
            portfolio=_portfolio(100000.0),
        )
        assert hints["CN:800005"] == "统计指标，不可交易，无数量建议。"

    def test_hk_has_no_hundred_lot_rounding(self):
        hints = build_quantity_hints(
            {"HK:00700": {"action": "buy"}},
            securities=[_sec("HK:00700", "腾讯控股", price=300.0)],
            portfolio=_portfolio(100000.0),
        )
        assert hints["HK:00700"] == "建议买入 33 股（约 9,900，本次约占总资产 10%）（港股请按券商手数取整）"

    def test_missing_quote(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "buy"}},
            securities=[_sec("CN:600519", price=None)],
            portfolio=_portfolio(100000.0),
        )
        assert hints["CN:600519"] == "暂无报价，无法估算数量。"

    def test_no_account(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "buy"}},
            securities=[_sec("CN:600519")],
            portfolio={},
        )
        assert hints["CN:600519"] == "未配置账户与资金，无法计算数量参考。"

    def test_non_trade_actions_produce_no_hint(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "watch"}, "CN:000001": {"action": "hold"}},
            securities=[_sec("CN:600519"), _sec("CN:000001")],
            portfolio=_portfolio(100000.0),
        )
        assert hints == {}


class TestSection:
    def test_section_lists_each_hint(self):
        hints = {"CN:600519": "建议买入 100 股（约 1,000，本次约占总资产 10%）"}
        section = format_quantity_section(hints, securities=[_sec("CN:600519", "贵州茅台")])
        assert section.startswith("## 数量参考（单票基准仓位 10% × 建议置信度")
        assert "**贵州茅台**：建议买入 100 股" in section

    def test_empty_hints_render_empty(self):
        assert format_quantity_section({}, securities=[]) == ""
