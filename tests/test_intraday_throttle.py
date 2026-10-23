"""盘中通知门槛与节流判别（本地定制，仅买卖动作推送 + 动作集合节流）。"""
from src.modules.research.orchestration.notify_policy import (
    NOTIFY_ACTIONS,
    actionable_pairs,
    intraday_discriminator,
    intraday_notify_allowed,
)


def _raw(suggestions):
    return {"suggestions": suggestions}


class TestActionablePairs:
    def test_only_trade_actions_are_actionable(self):
        raw = _raw({
            "CN:600519": {"action": "sell"},
            "CN:000001": {"action": "watch"},
            "CN:511880": {"action": "buy"},
            "HK:00700": {"action": "hold"},
        })
        assert actionable_pairs(raw) == ["CN:511880:buy", "CN:600519:sell"]

    def test_watch_hold_avoid_reduce_noise(self):
        for action in ("watch", "hold", "avoid", "alert", "review"):
            assert actionable_pairs(_raw({"CN:600519": {"action": action}})) == []

    def test_malformed_entries_ignored(self):
        raw = _raw({"CN:600519": None, "CN:000001": {"attention_required": True}})
        assert actionable_pairs(raw) == []

    def test_empty_raw_data(self):
        assert actionable_pairs(None) == []
        assert actionable_pairs({}) == []


class TestNotifyGate:
    def test_trade_action_allows_notify(self):
        for action in sorted(NOTIFY_ACTIONS):
            assert intraday_notify_allowed(_raw({"CN:600519": {"action": action}}))

    def test_watch_only_blocks_notify(self):
        raw = _raw({
            "CN:600519": {"action": "watch", "should_alert": True, "attention_required": True},
            "CN:000001": {"action": "hold", "review_required": True},
        })
        assert not intraday_notify_allowed(raw)


class TestDiscriminator:
    def test_same_action_set_shares_key(self):
        a = _raw({"CN:600519": {"action": "sell"}})
        b = _raw({"CN:600519": {"action": "sell"}})
        assert intraday_discriminator(a) == intraday_discriminator(b) != ""

    def test_action_flip_changes_key(self):
        before = _raw({"CN:600519": {"action": "sell"}})
        after = _raw({"CN:600519": {"action": "buy"}})
        assert intraday_discriminator(before) != intraday_discriminator(after)

    def test_new_symbol_changes_key(self):
        before = _raw({"CN:600519": {"action": "sell"}})
        after = _raw({"CN:600519": {"action": "sell"}, "CN:000001": {"action": "buy"}})
        assert intraday_discriminator(before) != intraday_discriminator(after)

    def test_watch_changes_do_not_change_key(self):
        before = _raw({"CN:600519": {"action": "sell"}, "CN:000001": {"action": "watch"}})
        after = _raw({"CN:600519": {"action": "sell"}, "CN:000001": {"action": "hold"}})
        assert intraday_discriminator(before) == intraday_discriminator(after)

    def test_no_actionable_means_empty_key(self):
        assert intraday_discriminator(_raw({"CN:600519": {"action": "watch"}})) == ""
        assert intraday_discriminator(None) == ""
