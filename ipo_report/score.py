"""신규상장 저가주 성장 가능성 채점.

canslim-screener(O'Neil CANSLIM) 를 상장 초기 한국 종목에 맞게 바꾼 100점 체계.
상장 직후 종목은 분기 실적·52주 상대강도가 없으므로 C·A 는 연간 매출 성장/수익성으로,
L 은 상장 이후 주가 흐름으로, I 는 수요예측·의무보유확약·기관/외국인 수급으로 대신한다.

  G 성장성        30  최근 연간 매출 성장률(+ 분기 YoY 보조)
  P 수익성        15  영업이익률, 흑자전환 여부
  N 상장후 흐름   20  공모가(없으면 상장일 종가) 대비 위치, 단기 추세, 고점 대비 낙폭 안정
  S 수급/유동성   15  5일 평균 거래대금, 기관·외국인 순매수
  I 공모 품질     10  수요예측 경쟁률, 의무보유확약 비율 (data/ipo_meta.json, 없으면 0점 '확인필요')
  V 밸류에이션    10  PSR / PER
  감점                 보호예수 해제 임박(1·3·6개월, 1년), 과도한 변동성, 자본잠식
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

LOCKUP_MONTHS = (1, 3, 6, 12)


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise ValueError(d)


def lockup_events(listed: str, as_of: str, horizon_days: int = 21) -> list[dict]:
    """as_of 이후 horizon_days 안에 오는 보호예수(의무보유) 해제 기준일."""
    ld, ad = _d(listed), _d(as_of)
    out = []
    for m in LOCKUP_MONTHS:
        ev = add_months(ld, m)
        if ad <= ev <= ad + timedelta(days=horizon_days):
            out.append({"months": m, "date": ev.isoformat(), "days_left": (ev - ad).days})
    return out


# ---------------------------------------------------------------------------
@dataclass
class Fundamentals:
    rev: list[tuple[str, float]] = field(default_factory=list)  # 실적 확정 연도 (key, 억원) 오래된 순
    op: list[tuple[str, float]] = field(default_factory=list)
    net: list[tuple[str, float]] = field(default_factory=list)
    debt_ratio: float | None = None
    q_rev: list[tuple[str, float]] = field(default_factory=list)  # 분기 실적 (key, 억원)
    cns_rev: float | None = None  # 컨센서스 다음 연도 매출


def parse_fundamentals(annual: dict, quarter: dict) -> Fundamentals:
    f = Fundamentals()
    actual = [k for k, _, cns in annual.get("periods", []) if not cns]
    cns = [k for k, _, c in annual.get("periods", []) if c]
    rows = annual.get("rows", {})

    def series(name: str, keys: list[str], src: dict) -> list[tuple[str, float]]:
        r = src.get(name, {})
        return [(k, r[k]) for k in keys if r.get(k) is not None]

    f.rev = series("매출액", actual, rows)
    f.op = series("영업이익", actual, rows)
    f.net = series("당기순이익", actual, rows)
    dr = series("부채비율", actual, rows)
    f.debt_ratio = dr[-1][1] if dr else None
    if cns and rows.get("매출액", {}).get(cns[0]) is not None:
        f.cns_rev = rows["매출액"][cns[0]]
    q_actual = [k for k, _, c in quarter.get("periods", []) if not c]
    f.q_rev = series("매출액", q_actual, quarter.get("rows", {}))
    return f


def growth(a: float | None, b: float | None) -> float | None:
    """a → b 성장률. 기준값이 0 이하이면 None."""
    if a is None or b is None or a <= 0:
        return None
    return b / a - 1


# ---------------------------------------------------------------------------
def price_metrics(bars: pd.DataFrame, ipo_price: float | None) -> dict:
    c = bars["close"].to_numpy(float)
    v = bars["volume"].to_numpy(float)
    last = c[-1]
    first_open, first_close = float(bars["open"].iloc[0]), float(c[0])
    base = ipo_price or first_close
    hi = float(bars["high"].max())
    lo_after = float(bars["low"].iloc[1:].min()) if len(bars) > 1 else float(bars["low"].iloc[0])
    rets = np.diff(c) / c[:-1] if len(c) > 1 else np.array([0.0])

    def ma(n: int) -> float | None:
        return float(c[-n:].mean()) if len(c) >= n else None

    tv = c * v
    return {
        "close": last,
        "trading_days": len(c),
        "first_open": first_open,
        "first_close": first_close,
        "ipo_price": ipo_price,
        "vs_base": last / base - 1,
        "base_kind": "공모가" if ipo_price else "상장일 종가",
        "vs_first_open": last / first_open - 1,
        "high": hi,
        "from_high": last / hi - 1,
        "from_low": last / lo_after - 1 if lo_after > 0 else None,
        "ma5": ma(5),
        "ma20": ma(20),
        "ret_5d": c[-1] / c[-6] - 1 if len(c) >= 6 else None,
        "ret_20d": c[-1] / c[-21] - 1 if len(c) >= 21 else None,
        "vol_daily": float(np.std(rets[1:])) if len(rets) > 2 else None,  # 상장일 제외 일간 변동성
        "tv_5d": float(tv[-5:].mean()),
        "tv_ratio": float(v[-5:].mean() / v[1:6].mean()) if len(v) >= 11 and v[1:6].mean() > 0 else None,
    }


def _band(x: float | None, steps: list[tuple[float, float]], default: float = 0.0) -> float:
    """x 가 임계값 이상이 되는 첫 단계의 점수 (steps 는 임계값 내림차순)."""
    if x is None:
        return default
    for th, pts in steps:
        if x >= th:
            return pts
    return default


def score(
    *,
    listed: str,
    as_of: str,
    pm: dict,
    fu: Fundamentals,
    mcap: float | None,
    per: float | None,
    flows: dict,
    meta: dict,
) -> dict:
    """반환: {'total', 'parts': {G,P,N,S,I,V,penalty}, 'notes': [...], 'flags': [...], 'lockups': [...]}"""
    notes: list[str] = []
    flags: list[str] = []
    parts: dict[str, float] = {}

    # ---- G 성장성 (30)
    g = 0.0
    yoy = growth(fu.rev[-2][1], fu.rev[-1][1]) if len(fu.rev) >= 2 else None
    cagr = None
    if len(fu.rev) >= 3 and fu.rev[0][1] > 0 and fu.rev[-1][1] > 0:
        cagr = (fu.rev[-1][1] / fu.rev[0][1]) ** (1 / (len(fu.rev) - 1)) - 1
    if yoy is not None:
        g += _band(yoy, [(1.0, 22), (0.5, 19), (0.3, 16), (0.15, 12), (0.05, 8), (0.0, 5)], 0)
        notes.append(f"최근 연매출 성장률 {yoy:+.0%}")
    else:
        g += 6
        flags.append("연간 매출 비교 불가(재무 이력 부족)")
    if cagr is not None:
        g += _band(cagr, [(0.5, 5), (0.25, 4), (0.1, 2)], 0)
    q_yoy = None
    if len(fu.q_rev) >= 2:
        # 네이버 분기표는 보통 [전년 동분기, 직전 분기들...] 형태 → 같은 분기 비교
        k_last, v_last = fu.q_rev[-1]
        same = [v for k, v in fu.q_rev if k[4:] == k_last[4:] and k[:4] == str(int(k_last[:4]) - 1)]
        if same:
            q_yoy = growth(same[0], v_last)
            if q_yoy is not None:
                g += _band(q_yoy, [(0.3, 3), (0.1, 2), (0.0, 1)], 0)
                notes.append(f"최근 분기 매출 YoY {q_yoy:+.0%}")
    parts["G"] = min(g, 30)

    # ---- P 수익성 (15)
    p = 0.0
    if fu.op and fu.rev and fu.rev[-1][1] > 0:
        opm = fu.op[-1][1] / fu.rev[-1][1]
        prev_op = fu.op[-2][1] if len(fu.op) >= 2 else None
        if fu.op[-1][1] > 0:
            p = _band(opm, [(0.2, 15), (0.1, 13), (0.05, 10), (0.0, 8)], 8)
            if prev_op is not None and prev_op <= 0:
                p = max(p, 12)
                notes.append("영업이익 흑자전환")
            notes.append(f"영업이익률 {opm:.1%}")
        else:
            improving = prev_op is not None and fu.op[-1][1] > prev_op
            p = 4 if improving else 1
            notes.append(f"영업적자({fu.op[-1][1]:,.0f}억){' · 적자 축소' if improving else ''}")
    else:
        flags.append("영업이익 데이터 없음")
        p = 3
    parts["P"] = p

    # ---- N 상장후 흐름 (20)
    n = 0.0
    vb = pm["vs_base"]
    if pm["ipo_price"]:
        n += _band(vb, [(1.0, 2), (0.5, 4), (0.0, 7), (-0.15, 5), (-0.3, 3)], 1)
    else:
        n += _band(vb, [(0.5, 3), (0.0, 6), (-0.2, 5), (-0.4, 3)], 1)
    c = pm["close"]
    if pm["ma5"] and c >= pm["ma5"]:
        n += 4
    if pm["ma20"] and c >= pm["ma20"]:
        n += 4
    elif pm["ma20"] is None and pm["trading_days"] >= 3 and c >= np.mean([pm["first_close"], c]):
        n += 2
    if pm["from_low"] is not None and pm["from_low"] >= 0.05:
        n += 3  # 상장 후 저점에서 반등 중
    if pm["ret_5d"] is not None and pm["ret_5d"] > 0:
        n += 2
    parts["N"] = min(n, 20)
    notes.append(f"{pm['base_kind']} 대비 {vb:+.0%}, 고점 대비 {pm['from_high']:+.0%}")

    # ---- S 수급/유동성 (15)
    s = _band(pm["tv_5d"], [(10e9, 6), (3e9, 5), (1e9, 3), (3e8, 1)], 0)
    if pm["tv_5d"] < 3e8:
        flags.append("거래대금 3억 미만(유동성 부족)")
    fb, ob = flows.get("foreign_net"), flows.get("organ_net")
    if fb is not None and fb > 0:
        s += 4
    if ob is not None and ob > 0:
        s += 4
    if fb is not None and ob is not None and fb > 0 and ob > 0:
        s += 1
        notes.append("최근 기관·외국인 동반 순매수")
    parts["S"] = min(s, 15)

    # ---- I 공모 품질 (10)
    i = 0.0
    comp = meta.get("demand_competition")
    lock = meta.get("lockup_commit_pct")
    if comp is None and lock is None:
        flags.append("수요예측·확약 정보 확인필요")
    i += _band(comp, [(1000, 5), (500, 4), (200, 2), (0, 0)], 0)
    i += _band(lock, [(40, 5), (20, 4), (10, 2), (0, 0)], 0)
    parts["I"] = i

    # ---- V 밸류에이션 (10)
    v = 0.0
    rev_now = fu.rev[-1][1] if fu.rev else None
    psr = mcap / (rev_now * 1e8) if mcap and rev_now and rev_now > 0 else None
    if per is not None and per > 0:
        v = _band(-per, [(-10, 10), (-20, 8), (-35, 5), (-60, 2)], 0)
        notes.append(f"PER {per:.1f}배")
    elif psr is not None:
        v = _band(-psr, [(-1, 8), (-3, 6), (-6, 4), (-12, 2)], 0)
        notes.append(f"PSR {psr:.1f}배")
    else:
        flags.append("밸류에이션 산출 불가")
    parts["V"] = v

    # ---- 감점
    pen = 0.0
    lk = lockup_events(listed, as_of)
    for e in lk:
        pen += 3 if e["months"] == 1 else 5
        flags.append(f"보호예수 {e['months']}개월 해제 {e['date']} (D-{e['days_left']})")
    if pm["vol_daily"] is not None and pm["vol_daily"] > 0.08:
        pen += 3
        flags.append(f"일간 변동성 {pm['vol_daily']:.0%} (매우 큼)")
    if fu.debt_ratio is not None and fu.debt_ratio < 0:
        flags.append("상장 전 결산 자본잠식(공모로 해소됐는지 확인)")
    elif fu.debt_ratio is not None and fu.debt_ratio > 300:
        pen += 2
        flags.append(f"부채비율 {fu.debt_ratio:.0f}%")
    parts["penalty"] = -pen

    total = max(0.0, sum(parts.values()))
    return {
        "total": round(total, 1),
        "parts": parts,
        "notes": notes,
        "flags": flags,
        "lockups": lk,
        "rev_yoy": yoy,
        "q_rev_yoy": q_yoy,
        "psr": psr,
    }


def grade(total: float) -> str:
    if total >= 65:
        return "관심"
    if total >= 50:
        return "관찰"
    return "보류"
