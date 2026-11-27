"""후보 수집 → 채점 → data/candidates/YYYYMMDD.json 저장."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import listings, naver
from .score import grade, parse_fundamentals, price_metrics, score

KST = ZoneInfo("Asia/Seoul")
SPAC_RE = re.compile(r"스팩|기업인수목적|SPAC", re.I)
REIT_RE = re.compile(r"리츠|REIT", re.I)


def exclude_reason(code: str, name: str, row: dict) -> str | None:
    if SPAC_RE.search(name):
        return "스팩"
    if code[-1] != "0":
        return "우선주"
    if REIT_RE.search(name):
        return "리츠"
    if row.get("trade_stop") and row["trade_stop"] != "TRADING":
        return f"거래정지({row['trade_stop']})"
    return None


def market_context() -> dict:
    out = {}
    for sym, label in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
        b = naver.daily_bars(sym, 60)
        c = b["close"]
        out[label] = {
            "date": b["date"].iloc[-1],
            "close": float(c.iloc[-1]),
            "chg_1d": float(c.iloc[-1] / c.iloc[-2] - 1),
            "ret_20d": float(c.iloc[-1] / c.iloc[-21] - 1) if len(c) > 21 else None,
            "above_ma20": bool(c.iloc[-1] >= c.tail(20).mean()),
            "above_ma50": bool(c.iloc[-1] >= c.tail(50).mean()) if len(c) >= 50 else None,
        }
    return out


def _flows(integ: dict) -> dict:
    rows = integ.get("dealTrendInfos") or []
    f = [naver.to_num(r.get("foreignerPureBuyQuant")) for r in rows]
    o = [naver.to_num(r.get("organPureBuyQuant")) for r in rows]
    f = [x for x in f if x is not None]
    o = [x for x in o if x is not None]
    return {
        "days": len(rows),
        "foreign_net": sum(f) if f else None,
        "organ_net": sum(o) if o else None,
        "foreign_ratio": naver.to_num(rows[0].get("foreignerHoldRatio")) if rows else None,
    }


def analyze(c: dict, as_of_hint: str, meta: dict) -> dict:
    code = c["code"]
    bars = naver.daily_bars(code, 300)
    integ = naver.integration(code)
    fu = parse_fundamentals(naver.finance(code, "annual"), naver.finance(code, "quarter"))
    info = {x["code"]: x.get("value") for x in integ.get("totalInfos", [])}
    m = meta.get(code, {})
    pm = price_metrics(bars, naver.to_num(m.get("ipo_price")))
    as_of = bars["date"].iloc[-1] if len(bars) else as_of_hint
    per = naver.to_num(info.get("per"))
    flows = _flows(integ)
    sc = score(
        listed=c["listed"], as_of=as_of, pm=pm, fu=fu, mcap=c.get("mcap"), per=per, flows=flows, meta=m
    )
    return {
        **c,
        "as_of": as_of,
        "industry_code": integ.get("industryCode"),
        "pbr": naver.to_num(info.get("pbr")),
        "per": per,
        "eps": naver.to_num(info.get("eps")),
        "price": pm,
        "fund": {
            "rev": fu.rev,
            "op": fu.op,
            "net": fu.net,
            "q_rev": fu.q_rev,
            "debt_ratio": fu.debt_ratio,
        },
        "flows": flows,
        "meta": m,
        "score": sc,
        "grade": grade(sc["total"]),
        "url": f"https://m.stock.naver.com/domestic/stock/{code}",
    }


def run(
    root: Path,
    *,
    days: int = 90,
    max_price: float = 10_000,
    as_of: str | None = None,
    log=print,
) -> Path:
    data = root / "data"
    stocks, cache = listings.refresh(data / "listing_dates.json", log=log)
    today = as_of or datetime.now(KST).strftime("%Y%m%d")
    cutoff = (datetime.strptime(today, "%Y%m%d") - timedelta(days=days)).strftime("%Y%m%d")
    meta_path = data / "ipo_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    recent, excluded, above = [], [], []
    for s in stocks:
        listed = (cache.get(s["code"]) or {}).get("listed")
        if not listed or listed < cutoff:
            continue
        row = {**s, "listed": listed}
        why = exclude_reason(s["code"], s["name"], s)
        if why:
            excluded.append({**row, "reason": why})
        elif s["close"] is None or s["close"] > max_price:
            above.append(row)
        else:
            recent.append(row)
    log(f"  최근 {days}일 신규상장 {len(recent) + len(excluded) + len(above)}종목 → "
        f"{max_price:,.0f}원 이하 {len(recent)} / 초과 {len(above)} / 제외 {len(excluded)}")

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda c: _safe(analyze, c, today, meta, log), recent))
    results = sorted([r for r in results if r], key=lambda r: -r["score"]["total"])
    as_of_final = max((r["as_of"] for r in results), default=today)

    out = {
        "as_of": as_of_final,
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "params": {"days": days, "max_price": max_price, "cutoff": cutoff},
        "market": market_context(),
        "candidates": results,
        "above_price": sorted(above, key=lambda r: r["listed"], reverse=True),
        "excluded": sorted(excluded, key=lambda r: r["listed"], reverse=True),
    }
    path = data / "candidates" / f"{as_of_final}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    return path


def _safe(fn, c, today, meta, log):
    try:
        return fn(c, today, meta)
    except Exception as e:  # 한 종목 실패가 전체를 막지 않게
        log(f"  [경고] {c['code']} {c['name']} 분석 실패: {e}")
        return None
