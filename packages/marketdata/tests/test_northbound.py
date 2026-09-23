"""北向资金(同花顺 hexin)vendor + client 方法测试。

离线 monkeypatch marketdata.vendors.northbound.market_get,不实抓。
真实结构已实抓校准(2026-09-23):扁平 {"time":[...],"hgt":[...],"sgt":[...]},
并行数组、元素可为数字/数字字符串/null;包装结构({"data": ...})仅作防御保留。
"""
from __future__ import annotations

import marketdata.vendors.northbound as nb
from marketdata.client import MarketData
from marketdata.defaults import StaticConfigProvider
from marketdata.ports import SourceConfig
from marketdata.types import NorthboundItem


def _hexin_payload(hgt: list, sgt: list, *, date: str | None = None) -> dict:
    inner = {"hgt": hgt, "sgt": sgt}
    if date is not None:
        inner["date"] = date
    return {"data": inner}


def _flat_payload(times: list, hgt: list, sgt: list) -> dict:
    """真实形态:扁平并行数组,无 data 包裹。"""
    return {"time": times, "hgt": hgt, "sgt": sgt}


class TestUnwrapPayload:
    def test_flat_real_structure(self):
        flat = {"time": ["09:30"], "hgt": [1.0], "sgt": [0.5]}
        assert nb._unwrap_payload(flat) is flat

    def test_single_layer(self):
        assert nb._unwrap_payload({"data": {"hgt": [], "sgt": []}}) == {"hgt": [], "sgt": []}

    def test_double_layer(self):
        assert nb._unwrap_payload({"data": {"data": {"hgt": [], "sgt": []}}}) == {"hgt": [], "sgt": []}

    def test_not_a_dict_returns_empty(self):
        assert nb._unwrap_payload(None) == {}
        assert nb._unwrap_payload("not a dict") == {}
        assert nb._unwrap_payload({"no_data_key": 1}) == {}


class TestLastPoint:
    def test_list_pairs(self):
        series = [["09:30", 1.1], ["09:31", 1.5], ["09:32", 2.3]]
        t, v = nb._last_point(series)
        assert t == "09:32" and v == 2.3

    def test_dict_points(self):
        series = [{"time": "09:30", "value": 1.1}, {"time": "09:31", "value": 1.5}]
        t, v = nb._last_point(series)
        assert t == "09:31" and v == 1.5

    def test_empty_or_invalid_returns_none_none(self):
        assert nb._last_point([]) == (None, None)
        assert nb._last_point(None) == (None, None)
        assert nb._last_point("not a series") == (None, None)


class TestPickPoint:
    """扁平并行数组 + now 对齐(实抓结构);成对元素走 _last_point 老路径。"""

    def test_flat_without_now_takes_last_valid(self):
        t, v = nb._pick_point(["09:30", "09:31", "10:15"], [1.2, 3.4, 8.76], None)
        assert t == "10:15" and v == 8.76

    def test_flat_with_now_aligns_to_time_axis(self):
        # 数组预填到 15:00 的残留值,now=09:31 时应取 09:31 的点而非末值
        times = ["09:30", "09:31", "10:15", "15:00"]
        series = [1.2, 3.4, 8.76, -99.0]
        t, v = nb._pick_point(times, series, "09:31")
        assert (t, v) == ("09:31", 3.4)

    def test_flat_now_before_all_points_falls_back_to_last(self):
        t, v = nb._pick_point(["09:30", "09:31"], [1.2, 3.4], "08:00")
        assert (t, v) == ("09:31", 3.4)

    def test_flat_skips_none_and_empty_string_holes(self):
        t, v = nb._pick_point(["09:30", "09:31", "09:32"], [1.2, None, ""], None)
        assert (t, v) == ("09:30", 1.2)

    def test_flat_string_numbers_pass_through(self):
        t, v = nb._pick_point(["09:30"], ["0.03"], None)
        assert v == "0.03"  # 数值转换在 _to_float 做,取点原样返回

    def test_flat_all_empty_returns_none_none(self):
        assert nb._pick_point(["09:30"], [None], None) == (None, None)
        assert nb._pick_point(None, [1.2], None) == (None, 1.2)  # 无 time 轴也能取值

    def test_pair_series_delegates_to_last_point(self):
        t, v = nb._pick_point(None, [["09:30", 1.1], ["09:31", 1.5]], "23:59")
        assert (t, v) == ("09:31", 1.5)


class TestToFloatAndSgtValid:
    def test_to_float_handles_nan_and_none(self):
        assert nb._to_float(None) is None
        assert nb._to_float(float("nan")) is None
        assert nb._to_float("1.23") == 1.23
        assert nb._to_float("not a number") is None

    def test_sgt_valid_rejects_nan(self):
        assert nb._sgt_valid(float("nan")) is None

    def test_sgt_valid_rejects_extreme_magnitude(self):
        assert nb._sgt_valid(999999.0) is None

    def test_sgt_valid_rejects_real_world_dirty_value(self):
        # 实抓脏值:sgt 持续报 365~400 亿(2026-09-23)
        assert nb._sgt_valid(388.97) is None
        assert nb._sgt_valid(-365.5) is None

    def test_sgt_valid_accepts_normal_range(self):
        assert nb._sgt_valid(12.34) == 12.34
        assert nb._sgt_valid(-45.6) == -45.6


# ---------------------------------------------------------------------------
# HexinNorthboundVendor.fetch
# ---------------------------------------------------------------------------

class TestHexinNorthboundVendor:
    def test_takes_last_value_of_minute_series(self, monkeypatch):
        payload = _hexin_payload(
            hgt=[["09:30", 1.2], ["09:31", 3.4], ["10:15", 8.76]],
            sgt=[["09:30", 0.5], ["09:31", 1.1], ["10:15", 2.34]],
            date="2026-07-16",
        )
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: payload)

        out = nb.HexinNorthboundVendor().fetch([], {})
        assert len(out) == 1 and isinstance(out[0], NorthboundItem)
        item = out[0]
        assert item.date == "2026-07-16"
        assert item.hgt_net == 8.76
        assert item.sgt_net == 2.34
        assert item.total_net == 8.76 + 2.34
        assert item.time == "10:15"

    def test_flat_real_structure_without_now_takes_tail(self, monkeypatch):
        payload = _flat_payload(
            times=["09:30", "09:31", "10:15"],
            hgt=[1.2, 3.4, 8.76],
            sgt=[0.5, 1.1, 2.34],
        )
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: payload)

        out = nb.HexinNorthboundVendor().fetch([], {})
        assert len(out) == 1
        item = out[0]
        assert item.hgt_net == 8.76
        assert item.sgt_net == 2.34
        assert item.time == "10:15"

    def test_flat_with_now_picks_current_time_point(self, monkeypatch):
        # 盘中数组预填了到 15:00 的残留值,now 对齐后不应取到未来时点
        payload = _flat_payload(
            times=["09:30", "09:31", "10:15", "15:00"],
            hgt=[1.2, 3.4, 8.76, -88.0],
            sgt=[0.5, 1.1, 2.34, -66.0],
        )
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: payload)

        out = nb.HexinNorthboundVendor().fetch([], {"now": "09:35"})
        item = out[0]
        assert item.hgt_net == 3.4
        assert item.sgt_net == 1.1
        assert item.time == "09:31"

    def test_flat_string_values_and_null_holes(self, monkeypatch):
        payload = _flat_payload(
            times=["09:30", "09:31", "09:32"],
            hgt=["0", "0.03", None],
            sgt=[None, None, None],
        )
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: payload)

        out = nb.HexinNorthboundVendor().fetch([], {"now": "09:32"})
        item = out[0]
        assert item.hgt_net == 0.03
        assert item.sgt_net is None
        assert item.total_net is None

    def test_flat_sgt_dirty_magnitude_discarded(self, monkeypatch):
        # 实抓 sgt 脏值 365~400 亿:弃 sgt,保 hgt,不臆造合计
        payload = _flat_payload(
            times=["10:15"],
            hgt=[8.76],
            sgt=[388.97],
        )
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: payload)

        out = nb.HexinNorthboundVendor().fetch([], {})
        item = out[0]
        assert item.hgt_net == 8.76
        assert item.sgt_net is None
        assert item.total_net is None

    def test_sgt_nan_falls_back_to_none_and_total_none(self, monkeypatch):
        payload = _hexin_payload(
            hgt=[["09:30", 1.2], ["10:15", 8.76]],
            sgt=[["09:30", 0.5], ["10:15", float("nan")]],
            date="2026-07-16",
        )
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: payload)

        out = nb.HexinNorthboundVendor().fetch([], {})
        assert len(out) == 1
        item = out[0]
        assert item.hgt_net == 8.76
        assert item.sgt_net is None
        assert item.total_net is None  # sgt 缺失,不臆造合计

    def test_sgt_extreme_magnitude_treated_as_invalid(self, monkeypatch):
        payload = _hexin_payload(
            hgt=[["10:15", 8.76]],
            sgt=[["10:15", 123456789.0]],  # 明显超出"亿元"合理范围的脏值
            date="2026-07-16",
        )
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: payload)

        out = nb.HexinNorthboundVendor().fetch([], {})
        item = out[0]
        assert item.sgt_net is None
        assert item.total_net is None
        assert item.hgt_net == 8.76  # hgt 不受 sgt 异常污染

    def test_none_response_returns_empty(self, monkeypatch):
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: None)
        assert nb.HexinNorthboundVendor().fetch([], {}) == []

    def test_empty_dict_response_returns_empty(self, monkeypatch):
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: {})
        assert nb.HexinNorthboundVendor().fetch([], {}) == []

    def test_unexpected_structure_returns_empty(self, monkeypatch):
        # data 不是 dict,或没有 hgt/sgt 键 —— 防御性返回 []
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: {"data": "unexpected string"})
        assert nb.HexinNorthboundVendor().fetch([], {}) == []

        monkeypatch.setattr(nb, "market_get", lambda *a, **k: {"data": {"unrelated": 1}})
        assert nb.HexinNorthboundVendor().fetch([], {}) == []

    def test_both_series_empty_returns_empty(self, monkeypatch):
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: _hexin_payload(hgt=[], sgt=[]))
        assert nb.HexinNorthboundVendor().fetch([], {}) == []

    def test_date_falls_back_to_config_when_missing_in_response(self, monkeypatch):
        payload = _hexin_payload(hgt=[["10:15", 8.76]], sgt=[["10:15", 2.34]])  # 无 date 字段
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: payload)

        out = nb.HexinNorthboundVendor().fetch([], {"date": "2026-07-16"})
        assert out[0].date == "2026-07-16"

    def test_date_empty_string_when_unavailable_anywhere(self, monkeypatch):
        payload = _hexin_payload(hgt=[["10:15", 8.76]], sgt=[["10:15", 2.34]])
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: payload)

        out = nb.HexinNorthboundVendor().fetch([], {})
        assert out[0].date == ""

    def test_forwards_expected_request_shape(self, monkeypatch):
        captured = {}

        def fake_market_get(url, *, host_key=None, headers=None, **kwargs):
            captured["url"] = url
            captured["host_key"] = host_key
            captured["headers"] = headers
            return _hexin_payload(hgt=[["10:15", 1.0]], sgt=[["10:15", 1.0]])

        monkeypatch.setattr(nb, "market_get", fake_market_get)
        nb.HexinNorthboundVendor().fetch([], {})
        assert captured["url"] == "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
        assert captured["host_key"] == "data.hexin.cn"
        assert captured["headers"]["Host"] == "data.hexin.cn"
        assert captured["headers"]["Referer"] == "https://data.hexin.cn/"


# ---------------------------------------------------------------------------
# MarketData.northbound() —— 走单源 Engine 出数
# ---------------------------------------------------------------------------

class TestClientMethod:
    def test_northbound_via_single_source_engine(self, monkeypatch):
        payload = _hexin_payload(
            hgt=[["09:30", 1.2], ["10:15", 8.76]],
            sgt=[["09:30", 0.5], ["10:15", 2.34]],
            date="2026-07-16",
        )
        monkeypatch.setattr(nb, "market_get", lambda *a, **k: payload)

        md = MarketData(config=StaticConfigProvider({
            "northbound": [SourceConfig(vendor="ths", priority=1)],
        }))
        out = md.northbound()
        assert len(out) == 1 and isinstance(out[0], NorthboundItem)
        assert out[0].hgt_net == 8.76
        assert out[0].total_net == 8.76 + 2.34

    def test_northbound_no_sources_returns_empty(self):
        md = MarketData(config=StaticConfigProvider({}))
        assert md.northbound() == []
