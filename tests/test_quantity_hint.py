"""数量参考（本地定制）：按目标仓位估算买/加/减/卖数量，A 股整百取整。"""
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
        # equity = 7141.17 + 700*1.439 + 300*3.37 = 9159.47；目标 15% = 1373.92 → 100 股
        hints = build_quantity_hints(
            {"CN:600519": {"action": "buy"}},
            securities=[_sec("CN:600519", "贵州茅台")],
            portfolio=_portfolio(7141.17, [(700, 1.439), (300, 3.37)]),
        )
        assert hints["CN:600519"] == "建议买入 100 股（约 1,000，约占总资产 11%）"

    def test_buy_capped_by_available_cash(self):
        # equity = 10000，目标 1500 → 150 股→整百 100；现金 550 → 可买 50 股→整百 0
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
        assert hints["CN:600519"] == "目标仓位或可用资金不足一手（100 股），暂不建议开仓。"

    def test_target_pct_override(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "buy"}},
            securities=[_sec("CN:600519")],
            portfolio=_portfolio(100000.0),
            target_pct=0.5,
        )
        assert hints["CN:600519"] == "建议买入 5000 股（约 50,000，约占总资产 50%）"


class TestAdd:
    def test_add_tops_up_to_target(self):
        # equity = 100000 + 700*10 = 107000；目标 16050 → 1600 股；已持 700 → 再买入 900
        hints = build_quantity_hints(
            {"CN:600519": {"action": "add"}},
            securities=[_sec("CN:600519", held=700)],
            portfolio=_portfolio(100000.0, [(700, 10.0)]),
        )
        assert hints["CN:600519"] == "建议加仓至 1600 股（再买入 900 股，约 9,000）"

    def test_add_at_target_is_noop(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "add"}},
            securities=[_sec("CN:600519", held=2000)],
            portfolio=_portfolio(0.0, [(2000, 10.0)]),
        )
        assert hints["CN:600519"] == "当前持仓 2000 股已达目标仓位（约 300 股），无需加仓。"

    def test_buy_on_held_stock_behaves_like_add(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "buy"}},
            securities=[_sec("CN:600519", held=700)],
            portfolio=_portfolio(100000.0, [(700, 10.0)]),
        )
        assert "加仓至 1600 股" in hints["CN:600519"]


class TestReduceSell:
    def test_reduce_to_target(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "reduce"}},
            securities=[_sec("CN:600519", held=2000)],
            portfolio=_portfolio(0.0, [(2000, 10.0)]),
        )
        assert hints["CN:600519"] == "建议减仓至 300 股（卖出 1700 股）"

    def test_reduce_below_target_is_informational(self):
        hints = build_quantity_hints(
            {"CN:600519": {"action": "reduce"}},
            securities=[_sec("CN:600519", held=100)],
            portfolio=_portfolio(100000.0, [(100, 10.0)]),
        )
        assert "未超目标仓位" in hints["CN:600519"]

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
        assert hints["HK:00700"] == "建议买入 50 股（约 15,000，约占总资产 15%）（港股请按券商手数取整）"

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
        hints = {"CN:600519": "建议买入 100 股（约 1,000，约占总资产 11%）"}
        section = format_quantity_section(hints, securities=[_sec("CN:600519", "贵州茅台")])
        assert section.startswith("## 数量参考（按目标仓位 15%")
        assert "**贵州茅台**：建议买入 100 股" in section

    def test_empty_hints_render_empty(self):
        assert format_quantity_section({}, securities=[]) == ""
