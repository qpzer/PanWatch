"""盘中监测通知节流:节流键含动作(symbol:action)。

回归场景(2026-10-09 线上):AI 建议从"持有"升级为"减仓/卖出"时,旧节流键只看股票,
30 分钟窗口内的升级信号被吞,用户收不到卖出通知。
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.modules.automation.base import AnalysisResult
from src.modules.automation.intraday_monitor import IntradayMonitorAgent
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import NotifyThrottle

_TEST_AGENT = "test_intraday_throttle"


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def agent():
    a = IntradayMonitorAgent(throttle_minutes=30)
    a.name = _TEST_AGENT  # 实例级隔离,不碰真实 intraday_monitor 节流记录
    yield a
    db = SessionLocal()
    try:
        db.query(NotifyThrottle).filter(
            NotifyThrottle.agent_name == _TEST_AGENT
        ).delete()
        db.commit()
    finally:
        db.close()


class TestThrottleKey:
    def test_symbol_with_action(self):
        key = IntradayMonitorAgent._throttle_key("516310", {"action": "reduce"})
        assert key == "516310:reduce"

    def test_missing_suggestion_or_action_falls_back_to_symbol(self):
        assert IntradayMonitorAgent._throttle_key("516310", None) == "516310"
        assert IntradayMonitorAgent._throttle_key("516310", {}) == "516310"
        assert IntradayMonitorAgent._throttle_key("516310", {"action": ""}) == "516310"
        assert IntradayMonitorAgent._throttle_key("516310", {"action": "  "}) == "516310"


class TestActionKeyedThrottle:
    def test_same_action_throttled_within_window(self, agent):
        agent._update_throttle("600519:reduce")
        assert agent._check_throttle("600519:reduce") is False

    def test_action_change_bypasses_window(self, agent):
        """回归:持有通知刚发出,减仓升级信号必须立即放行。"""
        agent._update_throttle("600519:hold")
        assert agent._check_throttle("600519:reduce") is True
        assert agent._check_throttle("600519:sell") is True

    def test_same_action_allowed_after_window(self, agent):
        db = SessionLocal()
        try:
            db.add(
                NotifyThrottle(
                    agent_name=_TEST_AGENT,
                    stock_symbol="600519:reduce",
                    last_notify_at=_utcnow_naive() - timedelta(minutes=31),
                    notify_count=1,
                )
            )
            db.commit()
        finally:
            db.close()
        assert agent._check_throttle("600519:reduce") is True

    def test_bare_symbol_legacy_record_still_throttles(self, agent):
        """旧格式(无动作)记录对无动作键保持节流,行为兼容。"""
        agent._update_throttle("600519")
        assert agent._check_throttle("600519") is False
        assert agent._check_throttle("600519:hold") is True

    def test_notify_count_increments_per_action_key(self, agent):
        agent._update_throttle("600519:reduce")
        agent._update_throttle("600519:reduce")
        db = SessionLocal()
        try:
            rec = (
                db.query(NotifyThrottle)
                .filter(
                    NotifyThrottle.agent_name == _TEST_AGENT,
                    NotifyThrottle.stock_symbol == "600519:reduce",
                )
                .one()
            )
            assert rec.notify_count == 2
        finally:
            db.close()


def _make_result(symbol: str, action: str, should_alert: bool = True) -> AnalysisResult:
    return AnalysisResult(
        agent_name=_TEST_AGENT,
        title="t",
        content="c",
        raw_data={
            "stock": {"symbol": symbol, "name": symbol},
            "suggestion": {"action": action},
            "should_alert": should_alert,
        },
    )


class TestTradeActionGate:
    """仅买卖动作(buy/add/reduce/sell)推送通知。"""

    def test_watch_with_attention_not_notified(self, agent):
        """回归(2026-10-09 线上):观望+attention_required 被 normalize 升级为
        should_alert=True 后照样推送,是通知噪音的最大来源。"""
        result = _make_result("600519", "watch", should_alert=True)
        assert asyncio.run(agent.should_notify(result)) is False

    def test_hold_not_notified(self, agent):
        result = _make_result("600519", "hold", should_alert=True)
        assert asyncio.run(agent.should_notify(result)) is False

    def test_missing_action_not_notified(self, agent):
        result = _make_result("600519", "", should_alert=True)
        assert asyncio.run(agent.should_notify(result)) is False

    def test_trade_action_passes_gate(self, agent):
        result = _make_result("600519", "buy", should_alert=True)
        assert asyncio.run(agent.should_notify(result)) is True

    def test_bypass_throttle_still_gated_on_action(self):
        """bypass 只跳过节流,动作门槛仍然生效:观望手动测试也不推送。"""
        agent = IntradayMonitorAgent(bypass_throttle=True)
        agent.name = _TEST_AGENT  # bypass 路径不写库,无需清理
        assert asyncio.run(agent.should_notify(_make_result("600519", "watch"))) is False
        assert asyncio.run(agent.should_notify(_make_result("600519", "buy"))) is True
