"""东财 CN 报价 vendor(quote 第二源)。push2 ulist.np 批量接口,一次请求取全部标的。

背景:push2/push2delay 的 stock/get 单只查询路径被东财 WAF 按路径封禁(云服务器 IP
连接被秒断,2026-09-23 实抓确认),同域名 ulist.np/get 批量路径正常,故整体切换。
批量请求顺带消除逐只循环的节流等待。

字段映射对齐 akshare stock_zh_a_spot_em(同一 ulist.np 端点):fltt=2 预格式化模式下
价格/涨跌/百分比字段直接是 float(停牌等无数据给 "-"),成交额/市值单位为元。
"""

from __future__ import annotations

import logging

from marketdata.http import market_get
from marketdata.symbol import Market, Symbol
from marketdata.types import Quote
from marketdata.vendors.base import QuoteVendor

logger = logging.getLogger(__name__)

_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
_HOST = "push2.eastmoney.com"
_MIN_INTERVAL_S = 0.2
# f2 最新价 / f3 涨跌幅 / f4 涨跌额 / f5 成交量(手) / f6 成交额(元) /
# f8 换手率 / f9 市盈率(动) / f10 量比 / f12 代码 / f13 市场号 / f14 名称 /
# f15 最高 / f16 最低 / f17 今开 / f18 昨收 / f20 总市值(元) / f21 流通市值(元)
_FIELDS = "f2,f3,f4,f5,f6,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
# secids 逗号拼接进 URL,分批控制 URL 长度(实测 50 只远低于长度上限)
_BATCH_SIZE = 50


def _to_float(value) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_row(row: dict | None, market: str) -> Quote | None:
    if not isinstance(row, dict):
        return None
    price = _to_float(row.get("f2"))
    if price is None or price <= 0:
        return None

    total_mv = _to_float(row.get("f20"))
    circ_mv = _to_float(row.get("f21"))

    return Quote(
        symbol=str(row.get("f12") or ""),
        market=market,
        name=str(row.get("f14") or ""),
        current_price=price,
        prev_close=_to_float(row.get("f18")),
        open_price=_to_float(row.get("f17")),
        high_price=_to_float(row.get("f15")),
        low_price=_to_float(row.get("f16")),
        change_amount=_to_float(row.get("f4")),
        change_pct=_to_float(row.get("f3")),  # fltt=2 下已是百分数(0.14 = 0.14%)
        volume=_to_float(row.get("f5")),
        turnover=_to_float(row.get("f6")),
        turnover_rate=_to_float(row.get("f8")),
        volume_ratio=_to_float(row.get("f10")),
        pe_ratio=_to_float(row.get("f9")),
        circulating_market_value=(circ_mv / 1e8) if circ_mv is not None else None,
        total_market_value=(total_mv / 1e8) if total_mv is not None else None,
    )


def _fetch_batch(secids: list[str]) -> list[dict]:
    payload = market_get(
        _URL,
        host_key=_HOST,
        min_interval_s=_MIN_INTERVAL_S,
        params={
            "secids": ",".join(secids),
            "fields": _FIELDS,
            "fltt": "2",
            "invt": "2",
        },
        headers=_HEADERS,
        timeout=10,
        retries=2,
        parse="json",
        log_label="东财报价",
    )
    if not payload or not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    diff = data.get("diff")
    if isinstance(diff, dict):  # 单标的时防御:个别情况下 diff 不是 list
        return [diff]
    return diff if isinstance(diff, list) else []


class EastmoneyQuoteVendor(QuoteVendor):
    name = "eastmoney"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Quote]:
        if not symbols:
            return []
        cn = [s for s in symbols if s.market == Market.CN]
        if not cn:
            return []
        market = cn[0].market.value

        out: list[Quote] = []
        secids = [s.to_eastmoney_secid() for s in cn]
        for i in range(0, len(secids), _BATCH_SIZE):
            for row in _fetch_batch(secids[i : i + _BATCH_SIZE]):
                q = _parse_row(row, market)
                if q:
                    out.append(q)
        return out
