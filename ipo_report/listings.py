"""신규상장 종목 찾기.

전 종목 목록을 받아 상장일 캐시(data/listing_dates.json)에 없는 코드만 fchart 로 상장일을 조회한다.
첫 실행은 약 2,700종목을 조회해 몇 분 걸리고, 이후에는 새로 생긴 코드만 조회한다.
캐시는 저장소에 커밋해 매일 새 세션에서도 재사용한다.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import naver

MARKETS = ("KOSPI", "KOSDAQ")


def load_cache(path: Path) -> dict[str, dict]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_cache(path: Path, cache: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(cache.items())), ensure_ascii=False, indent=0), encoding="utf-8")


def refresh(cache_path: Path, workers: int = 6, log=print) -> tuple[list[dict], dict[str, dict]]:
    """(오늘 전 종목 목록, 상장일 캐시) 를 돌려준다."""
    stocks: list[dict] = []
    for m in MARKETS:
        part = naver.list_stocks(m)
        log(f"  {m}: {len(part)}종목")
        stocks += part
    cache = load_cache(cache_path)
    todo = [s for s in stocks if s["code"] not in cache or not cache[s["code"]].get("listed")]
    if todo:
        log(f"  상장일 조회 {len(todo)}종목 (캐시 {len(cache)}종목)")
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for s, d in zip(todo, ex.map(lambda s: _safe_listing(s["code"]), todo)):
                cache[s["code"]] = {"name": s["name"], "market": s["market"], "listed": d}
        save_cache(cache_path, cache)
    return stocks, cache


def _safe_listing(code: str) -> str | None:
    try:
        return naver.listing_date(code)
    except Exception:
        return None
