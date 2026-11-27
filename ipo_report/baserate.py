"""과거 신규상장 저가주에 매매 계획(plan.py)을 적용했을 때의 1개월 성과.

대상: 2024-01-01 ~ (기준일-60일) 상장, 스팩·우선주·리츠 제외, 지금도 상장 중인 종목(생존 편향 있음).
진입: 상장 후 5~60거래일 사이, 종가 1만원 이하인 날 (같은 종목은 5거래일 간격으로만 표본 추출).
공모가 자료가 없어 목표가 후보에서 공모가는 뺀다.
결과: data/baserate.json
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from . import naver
from .collect import exclude_reason
from .plan import make_plan, simulate, summarize


def run(root: Path, start: str = "20240101", max_price: float = 10_000, log=print) -> dict:
    cache = json.loads((root / "data" / "listing_dates.json").read_text(encoding="utf-8"))
    end = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")
    codes = [
        (c, v["name"]) for c, v in cache.items()
        if v.get("listed") and start <= v["listed"] <= end and not exclude_reason(c, v["name"], {})
    ]
    log(f"  과거 신규상장 {len(codes)}종목 시세 조회")

    def one(item):
        code, _ = item
        try:
            bars = naver.daily_bars(code, 900).reset_index(drop=True)
        except Exception:
            return []
        if not len(bars) or bars["date"].iloc[0] != cache[code]["listed"]:
            return []  # 첫 봉이 상장일이 아니면(이력 부족·재상장) 제외
        out = []
        last = -99
        for i in range(5, min(61, len(bars) - 21)):
            if bars["close"].iloc[i] > max_price or i - last < 5:
                continue
            p = make_plan(bars.iloc[: i + 1])
            if not p:
                continue
            sim = simulate(bars, i, p)
            if sim is not None:
                r, how = sim
                out.append({"r": r, "exit": how, "rr": p["rr"], "year": bars["date"].iloc[i][:4], "hold_r": float(bars["close"].iloc[i + 20] / bars["close"].iloc[i] - 1 - 0.0025)})
                last = i
        return out

    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = [x for part in ex.map(one, codes) for x in part]
    res = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "period": f"{start[:4]}년 {int(start[4:6])}월~{end[:4]}년 {int(end[4:6])}월",
        "stocks": len(codes),
        "samples_note": "같은 종목은 5거래일 간격으로만 표본 추출, 비용 0.25% 차감, 상장폐지 종목 미포함",
        "plan": summarize([x["r"] for x in rows], [x["exit"] for x in rows]),
        "plan_rr15": summarize([x["r"] for x in rows if x["rr"] >= 1.5], [x["exit"] for x in rows if x["rr"] >= 1.5]),
        "plan_by_year": {
            y: summarize([x["r"] for x in rows if x["year"] == y], [x["exit"] for x in rows if x["year"] == y])
            for y in sorted({x["year"] for x in rows})
        },
        "hold_20d": summarize([x["hold_r"] for x in rows]),
    }
    (root / "data" / "baserate.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res
