"""네이버 증권 공개 API 클라이언트.

KRX 정보데이터시스템(로그인 필요)과 KIND(해외 IP 차단)를 쓸 수 없는 환경을 위해
네이버 증권만으로 신규상장 종목을 찾고 시세·재무를 받는다.

  - 전 종목 목록 : m.stock.naver.com/api/stocks/marketValue/{KOSPI|KOSDAQ}
  - 상장일       : fchart.stock.naver.com 일봉 XML 의 origintime (첫 거래일)
  - 일봉         : fchart.stock.naver.com
  - 종합/수급    : m.stock.naver.com/api/stock/{code}/integration
  - 재무(연/분기): m.stock.naver.com/api/stock/{code}/finance/{annual|quarter}
"""
from __future__ import annotations

import re
import time
from typing import Any

import pandas as pd
import requests

UA = "kr-ipo-report/1.0 (personal research script)"
MOBILE = "https://m.stock.naver.com/api"
FCHART = "https://fchart.stock.naver.com/sise.nhn"

_session = requests.Session()
_session.headers.update({"User-Agent": UA})


def _get(url: str, params: dict | None = None, tries: int = 4, timeout: float = 15) -> requests.Response:
    last: Exception | None = None
    for i in range(tries):
        try:
            r = _session.get(url, params=params, timeout=timeout)
            if r.status_code == 404:
                return r
            r.raise_for_status()
            return r
        except requests.RequestException as e:  # 네트워크 오류·5xx
            last = e
            time.sleep(1.0 * (i + 1))
    raise RuntimeError(f"요청 실패: {url} {params} ({last})")


def to_num(v: Any) -> float | None:
    """'1,234', '5.59배', '45.84%', '-' 같은 문자열을 숫자로. 실패하면 None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m or s in ("-", "N/A", ""):
        return None
    return float(m.group())


# ---------------------------------------------------------------------------
def list_stocks(market: str, page_size: int = 100) -> list[dict]:
    """시장 전 종목(보통주·ETF 등 포함) 현재가 목록. stockEndType == 'stock' 만 남긴다."""
    out: list[dict] = []
    page = 1
    while True:
        d = _get(f"{MOBILE}/stocks/marketValue/{market}", {"page": page, "pageSize": page_size}).json()
        rows = d.get("stocks") or []
        for s in rows:
            if s.get("stockEndType") != "stock":
                continue
            out.append(
                {
                    "code": s["itemCode"],
                    "name": s["stockName"],
                    "market": market,
                    "close": to_num(s.get("closePriceRaw") or s.get("closePrice")),
                    "chg_pct": to_num(s.get("fluctuationsRatio")),
                    "value": to_num(s.get("accumulatedTradingValueRaw")),
                    "mcap": to_num(s.get("marketValueRaw")),
                    "trade_stop": (s.get("tradeStopType") or {}).get("name"),
                }
            )
        total = d.get("totalCount") or 0
        if not rows or page * page_size >= total:
            break
        page += 1
        time.sleep(0.1)
    return out


_ITEM_RE = re.compile(r'<item data="(\d{8})\|([\d.]+)\|([\d.]+)\|([\d.]+)\|([\d.]+)\|(\d+)"')
_ORIGIN_RE = re.compile(r'origintime="(\d{8})"')


def _fchart(code: str, count: int) -> str:
    r = _get(FCHART, {"symbol": code, "timeframe": "day", "count": count, "requestType": 0})
    return r.content.decode("euc-kr", errors="replace")


def listing_date(code: str) -> str | None:
    """fchart 의 origintime = 해당 코드의 첫 거래일(신규상장일)."""
    m = _ORIGIN_RE.search(_fchart(code, 1))
    return m.group(1) if m else None


def parse_fchart(xml: str) -> pd.DataFrame:
    rows = [
        (d, float(o), float(h), float(lo), float(c), int(v))
        for d, o, h, lo, c, v in _ITEM_RE.findall(xml)
    ]
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    # 거래 없는 날(시가 0) 은 종가로 채운다
    for col in ("open", "high", "low"):
        df.loc[df[col] <= 0, col] = df["close"]
    return df


def daily_bars(code: str, count: int = 300) -> pd.DataFrame:
    return parse_fchart(_fchart(code, count))


def integration(code: str) -> dict:
    r = _get(f"{MOBILE}/stock/{code}/integration")
    return r.json() if r.status_code == 200 else {}


def finance(code: str, period: str = "annual") -> dict:
    """{'periods': [(key, title, is_consensus)], 'rows': {항목: {key: 값}}}"""
    r = _get(f"{MOBILE}/stock/{code}/finance/{period}")
    if r.status_code != 200:
        return {"periods": [], "rows": {}}
    fi = (r.json() or {}).get("financeInfo") or {}
    periods = [(t["key"], t["title"], t.get("isConsensus") == "Y") for t in fi.get("trTitleList", [])]
    rows = {}
    for row in fi.get("rowList", []):
        rows[row["title"]] = {k: to_num(v.get("value")) for k, v in (row.get("columns") or {}).items()}
    return {"periods": periods, "rows": rows}
