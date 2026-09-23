import marketdata.vendors.eastmoney as ev
from marketdata.symbol import Symbol
from marketdata.types import Quote


def _fake_row(code: str = "600519", name: str = "贵州茅台") -> dict:
    """构造真实形态的 ulist.np/get 行(fltt=2 预格式化,价格/百分比直接是 float)。

    current=1700.00 prev_close=1680.00 open=1685.00 high=1710.00 low=1670.00
    change_amount=20.00 change_pct=1.19(%)
    """
    return {
        "f2": 1700.0,         # 最新价
        "f3": 1.19,           # 涨跌幅(%)
        "f4": 20.0,           # 涨跌额
        "f5": 12345,          # 成交量(手)
        "f6": 6789000000.0,   # 成交额(元)
        "f8": 0.5,            # 换手率(%)
        "f9": 17.63,          # 市盈率(动)
        "f10": 1.2,           # 量比
        "f12": code,          # 代码
        "f13": 1,             # 市场号
        "f14": name,          # 名称
        "f15": 1710.0,        # 最高
        "f16": 1670.0,        # 最低
        "f17": 1685.0,        # 今开
        "f18": 1680.0,        # 昨收
        "f20": 2100050000000, # 总市值(元)→ /1e8 = 21000.5(亿)
        "f21": 2100050000000, # 流通市值(元)→ /1e8 = 21000.5(亿)
    }


def _payload(*rows: dict) -> dict:
    return {"rc": 0, "data": {"total": len(rows), "diff": list(rows)}}


def test_eastmoney_parses_fltt2_row_directly(monkeypatch):
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: _payload(_fake_row()))
    v = ev.EastmoneyQuoteVendor()
    out = v.fetch([Symbol.parse("600519", market="CN")], {})
    assert len(out) == 1
    q = out[0]
    assert isinstance(q, Quote)
    assert q.symbol == "600519" and q.name == "贵州茅台" and q.market == "CN"
    assert q.current_price == 1700.0
    assert q.prev_close == 1680.0
    assert q.open_price == 1685.0
    assert q.high_price == 1710.0
    assert q.low_price == 1670.0
    assert q.change_amount == 20.0
    assert q.change_pct == 1.19
    assert q.turnover_rate == 0.5
    assert q.volume_ratio == 1.2
    assert q.pe_ratio == 17.63
    assert q.volume == 12345.0
    assert q.turnover == 6789000000.0
    assert q.total_market_value == 21000.5
    assert q.circulating_market_value == 21000.5


def test_eastmoney_batch_multiple_symbols_single_call(monkeypatch):
    calls: list[dict] = []

    def fake_market_get(url, *, params=None, **kwargs):
        calls.append(params or {})
        secids = (params or {}).get("secids", "")
        rows = []
        if "1.600519" in secids:
            rows.append(_fake_row("600519", "贵州茅台"))
        if "0.000001" in secids:
            rows.append(_fake_row("000001", "平安银行"))
        return _payload(*rows)

    monkeypatch.setattr(ev, "market_get", fake_market_get)
    v = ev.EastmoneyQuoteVendor()
    symbols = [Symbol.parse("600519", market="CN"), Symbol.parse("000001", market="CN")]
    out = v.fetch(symbols, {})
    assert len(calls) == 1  # 批量接口一次请求取全部
    assert calls[0]["secids"] == "1.600519,0.000001"
    assert calls[0]["fltt"] == "2"
    assert len(out) == 2
    codes = {q.symbol for q in out}
    assert codes == {"600519", "000001"}


def test_eastmoney_chunking_over_batch_size(monkeypatch):
    calls: list[dict] = []

    def fake_market_get(url, *, params=None, **kwargs):
        calls.append(params or {})
        rows = [_fake_row(secid.split(".")[1]) for secid in (params or {}).get("secids", "").split(",")]
        return _payload(*rows)

    monkeypatch.setattr(ev, "market_get", fake_market_get)
    symbols = [Symbol.parse(f"6000{i:02d}", market="CN") for i in range(ev._BATCH_SIZE + 1)]
    out = ev.EastmoneyQuoteVendor().fetch(symbols, {})
    assert len(calls) == 2
    assert len(out) == ev._BATCH_SIZE + 1


def test_eastmoney_dash_fields_become_none(monkeypatch):
    row = _fake_row()
    row["f9"] = "-"   # 停牌/亏损股 PE 给 "-"
    row["f8"] = "-"
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: _payload(row))
    out = ev.EastmoneyQuoteVendor().fetch([Symbol.parse("600519", market="CN")], {})
    assert len(out) == 1
    assert out[0].pe_ratio is None
    assert out[0].turnover_rate is None


def test_eastmoney_dash_price_row_skipped(monkeypatch):
    row = _fake_row()
    row["f2"] = "-"   # 停牌无最新价
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: _payload(row))
    out = ev.EastmoneyQuoteVendor().fetch([Symbol.parse("600519", market="CN")], {})
    assert out == []


def test_eastmoney_diff_dict_single_item(monkeypatch):
    # 防御:个别情况下 diff 是单个 dict 而非 list
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: {"rc": 0, "data": {"total": 1, "diff": _fake_row()}})
    out = ev.EastmoneyQuoteVendor().fetch([Symbol.parse("600519", market="CN")], {})
    assert len(out) == 1
    assert out[0].symbol == "600519"


def test_eastmoney_empty_response_returns_empty(monkeypatch):
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: None)
    out = ev.EastmoneyQuoteVendor().fetch([Symbol.parse("600519", market="CN")], {})
    assert out == []


def test_eastmoney_no_symbols_returns_empty():
    assert ev.EastmoneyQuoteVendor().fetch([], {}) == []


def test_eastmoney_unsupported_market_skipped(monkeypatch):
    # 本 vendor 只做 CN;HK/US symbol 应被跳过,不发请求
    calls = {"n": 0}

    def fake_market_get(*a, **k):
        calls["n"] += 1
        return _payload(_fake_row())

    monkeypatch.setattr(ev, "market_get", fake_market_get)
    out = ev.EastmoneyQuoteVendor().fetch([Symbol.parse("00700", market="HK")], {})
    assert out == []
    assert calls["n"] == 0


def test_eastmoney_missing_data_key_returns_empty(monkeypatch):
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: {"rc": 0, "data": None})
    out = ev.EastmoneyQuoteVendor().fetch([Symbol.parse("600519", market="CN")], {})
    assert out == []
