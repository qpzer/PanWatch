"""北向资金 vendor:同花顺(ths/hexin)当日分钟累计净买入,市场级(symbols 恒空)。

背景:东财 datacenter/push2 的北向资金接口(kamt)自 2024-08 起断供(返回 NaN/0),
不可用。改走同花顺 hexin 私有接口 `data.hexin.cn/market/hsgtApi/method/dayChart/`,
返回当日分钟级累计净买入序列,`hgt`(沪股通)/`sgt`(深股通),单位均为"亿元"。

真实响应结构(2026-09-23 实抓校准):扁平 JSON,无 data 包裹——
{"time": ["09:10", ..., "15:00"], "hgt": [0, 0.03, ...], "sgt": [...]}。
time 是全天时间轴(262 点),hgt/sgt 是并行数组,元素为数字或数字字符串,可有 null。
注意:数组会预填到 15:00 的残留/脏值(盘中尚未到的时点也有数),不能直接取末值,
须按宿主传入的 config["now"]("HH:MM",北京时间)对齐时间轴取点。

已知坑(SKILL 标注 + 实抓确认):`sgt`(深股通)数据不可靠,实抓返回 365~400 亿的
持续脏值(远超合理范围),必须容错——异常时 sgt_net=None,不参与 total_net 计算,
也不让异常值污染 hgt_net。绝不用无参 now()/time()/random 填充缺失的 date/time。
"""
from __future__ import annotations

from marketdata.http import market_get
from marketdata.symbol import Symbol
from marketdata.types import NorthboundItem
from marketdata.vendors.base import NorthboundVendor as _NorthboundVendorBase

_HEXIN_URL = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
_HEXIN_HOST = "data.hexin.cn"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

_HEADERS = {
    "Host": _HEXIN_HOST,
    "Referer": "https://data.hexin.cn/",
    "User-Agent": _UA,
}

# sgt(深股通)不可靠:实抓(2026-09-23)返回 365~400 亿的持续脏值;历史披露时代
# 深股通单日净买入极值约 ±130 亿,150 亿作为防御上限,超过一律视为异常丢弃。
_SGT_MAX_ABS = 150.0


def _to_float(value) -> float | None:
    """宽松转 float;None/无法转换/NaN 一律 None(NaN 用 f != f 判定,不额外 import math)。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _sgt_valid(value) -> float | None:
    """sgt 专用:在 _to_float 基础上再做量级容错(近期不可靠,可能 NaN/异常大)。"""
    f = _to_float(value)
    if f is None:
        return None
    if abs(f) > _SGT_MAX_ABS:
        return None
    return f


def _unwrap_payload(resp) -> dict:
    """剥离外层包裹,拿到含 hgt/sgt 的一层。

    真实响应是扁平结构(见模块 docstring),直接命中返回;data 单/双层包裹仅作防御。
    """
    if not isinstance(resp, dict):
        return {}
    if isinstance(resp.get("hgt"), (list, tuple)) or isinstance(resp.get("sgt"), (list, tuple)):
        return resp
    layer = resp.get("data")
    if not isinstance(layer, dict):
        return {}
    inner = layer.get("data")
    if isinstance(inner, dict):
        return inner
    return layer


def _last_point(series) -> tuple[object, object]:
    """从分钟序列取末值。序列元素是 [time, value] 或 {"time":.., "value":..} 形态时使用;
    扁平并行数组形态(time 轴独立)请走 _pick_point。取不到返回 (None, None)。
    """
    if not isinstance(series, (list, tuple)) or not series:
        return None, None
    last = series[-1]
    if isinstance(last, (list, tuple)) and len(last) >= 2:
        return last[0], last[1]
    if isinstance(last, dict):
        t = last.get("time") or last.get("t") or last.get("x")
        v = last.get("value") or last.get("v") or last.get("y") or last.get("net")
        return t, v
    return None, None


def _pick_point(times, series, now_hhmm: str | None) -> tuple[object, object]:
    """从分钟序列取"当前时点"的值。

    - 成对/字典元素:委托 _last_point 取末值(包装结构防御路径)。
    - 扁平并行数组(实抓结构):times=["09:10",...] 与 series=[0.03,...] 按下标对齐,
      取最后一个 time <= now_hhmm 的非空点——实抓发现数组会预填到 15:00 的残留值,
      直接取末值会拿到未来时段的脏数据;now_hhmm 缺失或无匹配时退化为最后一个非空点。
    """
    if not isinstance(series, (list, tuple)) or not series:
        return None, None
    if isinstance(series[0], (list, tuple, dict)):
        return _last_point(series)

    pairs: list[tuple[object, object]] = []
    for i, v in enumerate(series):
        if v is None or v == "":
            continue
        t = times[i] if isinstance(times, (list, tuple)) and i < len(times) else None
        pairs.append((t, v))
    if not pairs:
        return None, None
    if now_hhmm:
        timed = [(t, v) for t, v in pairs if isinstance(t, str) and t <= now_hhmm]
        if timed:
            return timed[-1]
    return pairs[-1]


class HexinNorthboundVendor(_NorthboundVendorBase):
    """北向资金(同花顺 hexin):市场级,fetch 忽略 symbols。取当日分钟序列末值组装 1 条。"""

    name = "ths"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[NorthboundItem]:
        data = market_get(
            _HEXIN_URL,
            host_key=_HEXIN_HOST,
            headers=_HEADERS,
            parse="json",
            retries=2,
            timeout=8,
            log_label="北向资金",
        )
        if not data:
            return []

        payload = _unwrap_payload(data)
        if not payload:
            return []

        # 宿主传入当前北京时间("HH:MM")用于对齐 time 轴;包内不调无参 now()
        now_raw = (config or {}).get("now")
        now_hhmm = str(now_raw)[:5] if now_raw else None

        times = payload.get("time")
        hgt_time, hgt_raw = _pick_point(times, payload.get("hgt"), now_hhmm)
        sgt_time, sgt_raw = _pick_point(times, payload.get("sgt"), now_hhmm)
        hgt_net = _to_float(hgt_raw)
        sgt_net = _sgt_valid(sgt_raw)
        if hgt_net is None and sgt_net is None:
            return []

        total_net = hgt_net + sgt_net if (hgt_net is not None and sgt_net is not None) else None
        date = str(
            (data.get("date") if isinstance(data, dict) else None)
            or payload.get("date")
            or (config or {}).get("date")
            or ""
        )
        time_point = hgt_time if hgt_time is not None else sgt_time
        time_str = str(time_point) if time_point is not None else ""

        return [
            NorthboundItem(
                date=date,
                hgt_net=hgt_net,
                sgt_net=sgt_net,
                total_net=total_net,
                time=time_str,
            )
        ]
