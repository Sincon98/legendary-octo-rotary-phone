"""합성 데이터로 신호/수익률 계산 검증 (KRX 접속 불필요): python -m pytest tests -q"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import inst_buying_backtest as bt  # noqa: E402


def make_data(net_a, prices_a, n=15, frgn_a=None):
    frgn_a = frgn_a or [1] * n
    dates = pd.bdate_range("2024-01-01", periods=n)
    rows = []
    for i, d in enumerate(dates):
        pa = prices_a[i]
        prev = prices_a[i - 1] if i else pa
        rows.append(dict(date=d, ticker="AAA", market="KOSPI", name="A", open=pa, high=pa, low=pa,
                         close=pa, volume=1, value=5e9, chg=(pa / prev - 1) * 100, inst_net=net_a[i],
                         frgn_net=frgn_a[i]))
        rows.append(dict(date=d, ticker="BBB", market="KOSDAQ", name="B", open=1e4, high=1e4, low=1e4,
                         close=1e4, volume=1, value=5e9, chg=0.0, inst_net=-1, frgn_net=1))
    return pd.DataFrame(rows)


def test_signal_and_return():
    net = [-1, 1, 1, 1, 1, 1, -1, 1, 1, -1, -1, -1, -1, -1, -1]
    #      0  1  2  3  4  5   6  7  8 ...   -> 연속 3일째 = day3 (exact 모드는 day3 한 번만)
    px = [10000] * 4 + [10000, 10500, 11000, 11000, 12000] + [12000] * 6
    data = make_data(net, px)
    cfg = bt.Config(fee=0, tax=0, slippage=0, min_value=0)
    trades, bench, n_inc, dates = bt.run_backtest(data, "2024-01-01", cfg)
    assert list(trades["ticker"]) == ["AAA"]
    t = trades.iloc[0]
    assert t["signal_date"] == dates[3] and t["entry_date"] == dates[4] and t["exit_date"] == dates[8]
    # day4 시가 10000 매수 → day8 종가 12000 매도
    assert np.isclose(t["ret"], 0.2)


def test_atleast_mode_and_costs():
    net = [-1, 1, 1, 1, 1, 1, -1] + [-1] * 8
    px = [10000] * 15
    data = make_data(net, px)
    cfg = bt.Config(streak_mode="atleast", min_value=0)
    trades, *_ = bt.run_backtest(data, "2024-01-01", cfg)
    assert len(trades) == 3                      # day3, day4, day5
    expected = (1 - 0.0005) * (1 - 0.00015 - 0.002) / (1.0005 * 1.00015) - 1
    assert np.allclose(trades["ret"], expected)


def test_incomplete_holding_excluded():
    net = [-1] * 9 + [1, 1, 1, -1, -1, -1]       # day11 신호 → 청산일 day16 없음
    data = make_data(net, [10000] * 15)
    trades, _, n_inc, _ = bt.run_backtest(data, "2024-01-01", bt.Config(min_value=0))
    assert trades.empty and n_inc == 1


def test_foreign_conditions():
    net = [-1, 1, 1, 1, 1, 1, -1] + [-1] * 8       # 기관: day1~5 연속 순매수
    frgn = [1, 1, -1, 1, 1, 1, 1] + [1] * 8        # 외국인: day2 순매도
    data = make_data(net, [10000] * 15, frgn_a=frgn)
    base = dict(min_value=0)
    off, *_ = bt.run_backtest(data, "2024-01-01", bt.Config(**base))
    sig, *_ = bt.run_backtest(data, "2024-01-01", bt.Config(foreign="signal", **base))
    stk, _, _, dates = bt.run_backtest(data, "2024-01-01", bt.Config(foreign="streak", **base))
    assert list(off["signal_date"]) == [dates[3]]
    assert list(sig["signal_date"]) == [dates[3]]   # day3 외국인 순매수 → 통과
    assert list(stk["signal_date"]) == [dates[5]]   # 동시 순매수는 day3~5 가 첫 3연속


def test_foreign_signal_day_filter():
    net = [-1, 1, 1, 1, -1] + [-1] * 10
    frgn = [1, 1, 1, -1, 1] + [1] * 10             # 신호일(day3) 외국인 순매도
    data = make_data(net, [10000] * 15, frgn_a=frgn)
    sig, *_ = bt.run_backtest(data, "2024-01-01", bt.Config(foreign="signal", min_value=0))
    assert sig.empty
