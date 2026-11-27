"""신규상장 보고서: 파싱·채점·렌더링 검증 (네트워크 불필요): python -m pytest tests -q"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ipo_report import collect, naver, render  # noqa: E402
from ipo_report.score import (  # noqa: E402
    Fundamentals,
    add_months,
    grade,
    lockup_events,
    parse_fundamentals,
    price_metrics,
    score,
)
from datetime import date  # noqa: E402


def test_to_num():
    assert naver.to_num("1,234") == 1234
    assert naver.to_num("5.59배") == 5.59
    assert naver.to_num("-129원") == -129
    assert naver.to_num("N/A") is None and naver.to_num("-") is None


def test_parse_fchart():
    xml = (
        '<chartdata symbol="000001" origintime="20261015">'
        '<item data="20261015|19000|28350|13300|13320|39071717" />'
        '<item data="20261016|0|0|0|13500|0" />'
        "</chartdata>"
    )
    df = naver.parse_fchart(xml)
    assert list(df["close"]) == [13320, 13500]
    assert df.loc[1, "open"] == 13500  # 거래 없는 날 시가는 종가로


def test_lockup_dates():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    ev = lockup_events("20261030", "20261127")
    assert ev == [{"months": 1, "date": "2026-11-30", "days_left": 3}]
    assert lockup_events("20260301", "20261127") == []
    assert lockup_events("20260601", "20261127")[0]["months"] == 6


def test_exclusions():
    assert collect.exclude_reason("0222E0", "아이엠스팩1호", {}) == "스팩"
    assert collect.exclude_reason("0220WL", "한화머시너리3우B", {}) == "우선주"
    assert collect.exclude_reason("179880", "멜콘", {"trade_stop": "TRADING"}) is None


def _bars(closes):
    return pd.DataFrame(
        {
            "date": [f"202611{i + 1:02d}" for i in range(len(closes))],
            "open": closes,
            "high": [c * 1.02 for c in closes],
            "low": [c * 0.98 for c in closes],
            "close": closes,
            "volume": [100_000] * len(closes),
        }
    )


def test_score_prefers_growth_and_ipo_quality():
    annual = {
        "periods": [("202312", "", False), ("202412", "", False), ("202512", "", False), ("202612", "", True)],
        "rows": {"매출액": {"202312": 100, "202412": 150, "202512": 250}, "영업이익": {"202412": -5, "202512": 30},
                 "부채비율": {"202512": 80}},
    }
    fu = parse_fundamentals(annual, {"periods": [], "rows": {}})
    assert fu.rev[-1] == ("202512", 250) and fu.debt_ratio == 80
    pm = price_metrics(_bars([9000, 8500, 8000, 8200, 8600, 8800, 9100]), ipo_price=9000)
    assert abs(pm["vs_base"] - (9100 / 9000 - 1)) < 1e-9
    good = score(listed="20261101", as_of="20261107", pm=pm, fu=fu, mcap=500e8, per=12, flows={"foreign_net": 10, "organ_net": 5},
                 meta={"demand_competition": 1100, "lockup_commit_pct": 35})
    weak = score(listed="20261101", as_of="20261107", pm=pm, fu=Fundamentals(), mcap=500e8, per=None, flows={}, meta={})
    assert good["total"] > weak["total"]
    assert "영업이익 흑자전환" in good["notes"]
    assert "수요예측·확약 정보 확인필요" in weak["flags"]
    assert grade(good["total"]) in ("관심", "관찰")


def test_render(tmp_path):
    cand = {
        "code": "179880", "name": "테스트", "market": "KOSDAQ", "listed": "20261015", "mcap": 1.1e11, "url": "u",
        "price": {"close": 9000, "ipo_price": 12300, "first_close": 17000, "vs_base": -0.27, "base_kind": "공모가",
                  "from_high": -0.69, "tv_5d": 2.6e9},
        "meta": {"demand_competition": 1136.8, "lockup_commit_pct": 36.1, "sources": ["https://example.com/a"]},
        "score": {"total": 72, "parts": {"G": 24, "P": 13, "N": 12, "S": 12, "I": 9, "V": 5, "penalty": -3},
                  "notes": ["근거"], "flags": ["보호예수 1개월 해제 2026-11-30 (D-3)"]},
        "grade": "관심",
    }
    data = {"as_of": "20261127", "generated_at": "2026-11-27T17:45:00+09:00", "params": {"days": 90, "max_price": 10000},
            "market": {"코스닥": {"close": 695.2, "chg_1d": 0.06, "ret_20d": -0.04, "above_ma20": True}},
            "candidates": [cand], "above_price": [], "excluded": []}
    md = render.to_markdown(data, {"summary": "요약"}, None)
    page = render.to_html(data, {"picks": {"179880": {"risks": ["<script>"]}}}, {"179880": 2})
    assert "장 마감 기준" in md and "테스트" in md
    assert "&lt;script&gt;" in page and "<script>" not in page.split("</style>")[1]
    assert render.rank_change("179880", 1, {"179880": 2}) == "▲1"
    assert render.rank_change("x", 1, {"179880": 2}) == "신규"
