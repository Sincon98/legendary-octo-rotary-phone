"""1개월(약 20거래일) 매매 계획: 기준 매수가, 손절가, 목표가, 손익비.

position-sizer 스킬의 ATR 기반 손절 + 고정비율(fixed fractional) 방식을 따른다.
  - 손절 폭 = 2 × ATR, 단 8% ~ 20% 로 제한 (상장 초기 종목은 변동성이 커서 상한을 둔다)
  - 목표가 = 현재가보다 8% 이상 위에 있는 가장 가까운 저항 (20일선, 최근 20일 고점, 공모가).
    없으면 손절 폭의 1.5배. 최대 +50%.
  - 주식 수 = min(총자금 × 위험비율 / (매수가 - 손절가), 총자금 × 종목당 상한 / 매수가)
    위험비율·상한은 등급별(관심 1%·15%, 관찰 0.75%·10%, 보류 0.5%·5%).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

RISK_BY_GRADE = {"관심": (0.010, 0.15), "관찰": (0.0075, 0.10), "보류": (0.005, 0.05)}
HOLD_DAYS = 20
COST = 0.0025  # 왕복 수수료+세금+슬리피지


def tick(price: float) -> int:
    """KRX 호가단위 (2023년 개편 기준)."""
    for lim, t in ((2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500)):
        if price < lim:
            return t
    return 1000


def floor_tick(p: float) -> float:
    t = tick(p)
    return math.floor(p / t) * t


def ceil_tick(p: float) -> float:
    t = tick(p)
    return math.ceil(p / t) * t


def atr(bars: pd.DataFrame, n: int = 14) -> float | None:
    """상장 첫날(가격제한폭이 넓어 왜곡)을 뺀 최근 n일 평균 진폭."""
    b = bars.iloc[1:]
    if len(b) < 3:
        return None
    prev = bars["close"].shift(1).iloc[1:]
    tr = pd.concat([b["high"] - b["low"], (b["high"] - prev).abs(), (b["low"] - prev).abs()], axis=1).max(axis=1)
    return float(tr.tail(n).mean())


def make_plan(bars: pd.DataFrame, ipo_price: float | None = None) -> dict | None:
    if len(bars) < 4:
        return None
    close = float(bars["close"].iloc[-1])
    a = atr(bars)
    if not a:
        return None
    atr_pct = a / close
    stop_pct = min(max(2 * atr_pct, 0.08), 0.20)
    stop = floor_tick(close * (1 - stop_pct))

    after = bars.iloc[1:]
    ma20 = float(after["close"].tail(20).mean())
    hi20 = float(after["high"].tail(20).max())
    levels = [("20일 평균가", ma20), ("최근 20일 고점", hi20)]
    if ipo_price:
        levels.append(("공모가", float(ipo_price)))
    above = sorted([(v, k) for k, v in levels if v >= close * 1.08])
    if above:
        target, basis = above[0]
    else:
        target, basis = close * (1 + 1.5 * stop_pct), "손절폭의 1.5배"
    if target > close * 1.5:
        target, basis = close * 1.5, f"{basis}(+50% 상한)"
    target = ceil_tick(target)
    return {
        "entry": close,
        "stop": stop,
        "stop_pct": stop / close - 1,
        "target": target,
        "target_pct": target / close - 1,
        "target_basis": basis,
        "rr": (target - close) / (close - stop),
        "atr_pct": atr_pct,
    }


def shares(budget: float, plan: dict, grade: str, tv_5d: float | None = None) -> dict:
    risk_pct, cap_pct = RISK_BY_GRADE.get(grade, RISK_BY_GRADE["보류"])
    per_share_risk = plan["entry"] - plan["stop"]
    n_risk = math.floor(budget * risk_pct / per_share_risk) if per_share_risk > 0 else 0
    n_cap = math.floor(budget * cap_pct / plan["entry"])
    n = min(n_risk, n_cap)
    if tv_5d:  # 하루 거래대금의 2% 이내 (체결 가능성)
        n = min(n, math.floor(tv_5d * 0.02 / plan["entry"]))
    return {
        "shares": n,
        "amount": n * plan["entry"],
        "loss_at_stop": n * (plan["stop"] - plan["entry"]),
        "gain_at_target": n * (plan["target"] - plan["entry"]),
        "risk_pct": risk_pct,
        "cap_pct": cap_pct,
    }


def simulate(bars: pd.DataFrame, i: int, plan: dict) -> tuple[float, str] | None:
    """i일 종가에 사서 손절/목표/20거래일 중 먼저 닿는 쪽으로 판 (수익률, 'stop'|'target'|'time').
    같은 날 손절·목표가 모두 닿으면 손절로 본다(보수적). 갭으로 넘어서 열리면 시가에 체결."""
    fut = bars.iloc[i + 1 : i + 1 + HOLD_DAYS]
    if len(fut) < HOLD_DAYS:
        return None
    e = plan["entry"]
    for o, h, lo in zip(fut["open"], fut["high"], fut["low"]):
        if lo <= plan["stop"]:
            return min(o, plan["stop"]) / e - 1 - COST, "stop"
        if h >= plan["target"]:
            return max(o, plan["target"]) / e - 1 - COST, "target"
    return float(fut["close"].iloc[-1]) / e - 1 - COST, "time"


def summarize(rets: list[float], exits: list[str] | None = None) -> dict:
    r = np.array(rets)
    if not len(r):
        return {"n": 0}
    wins, losses = r[r > 0], r[r <= 0]
    ex = {}
    if exits:
        ex = {f"exit_{k}": exits.count(k) / len(exits) for k in ("target", "stop", "time")}
    return {
        **ex,
        "p05": float(np.percentile(r, 5)),
        "p95": float(np.percentile(r, 95)),
        "n": int(len(r)),
        "win_rate": float((r > 0).mean()),
        "mean": float(r.mean()),
        "median": float(np.median(r)),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
    }
