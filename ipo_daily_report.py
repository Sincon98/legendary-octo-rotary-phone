#!/usr/bin/env python3
"""신규상장 1만원 이하 성장주 일일 보고서.

  python ipo_daily_report.py collect            # 수집·채점 → data/candidates/{날짜}.json
  python ipo_daily_report.py render             # (+ reports/notes/{날짜}.json) → reports/{날짜}.md/.html
  python ipo_daily_report.py all                # collect + render
  python ipo_daily_report.py pending-meta       # 공모가·수요예측 정보가 없는 후보 목록 (조사 대상)
  python ipo_daily_report.py baserate           # 과거 신규상장주에 손절·목표 규칙 적용 1개월 성과 → data/baserate.json

옵션: --days 90 (상장 후 경과 일수 상한), --max-price 10000, --as-of YYYYMMDD
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ipo_report import baserate, collect, render

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["collect", "render", "all", "pending-meta", "baserate"])
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--max-price", type=float, default=10_000)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()

    if a.cmd in ("collect", "all"):
        path = collect.run(ROOT, days=a.days, max_price=a.max_price, as_of=a.as_of)
        d = json.loads(path.read_text(encoding="utf-8"))
        print(f"\n저장: {path.relative_to(ROOT)}  (기준일 {d['as_of']}, 후보 {len(d['candidates'])})")
        for i, c in enumerate(d["candidates"][: a.top], 1):
            print(f"  {i:2d}. {c['score']['total']:5.1f} {c['grade']} {c['name']}({c['code']}) "
                  f"{c['price']['close']:,.0f}원 | {'; '.join(c['score']['flags']) or '-'}")
    if a.cmd in ("render", "all"):
        md, ht = render.run(ROOT, a.as_of)
        print(f"보고서: {md.relative_to(ROOT)}, {ht.relative_to(ROOT)}, reports/latest.html")
    if a.cmd == "baserate":
        r = baserate.run(ROOT)
        b = r["plan"]
        print(f"{r['period']} 상장 {b['n']}건: 승률 {b['win_rate']:.0%}, 중간 {b['median']:+.1%}, 평균 {b['mean']:+.1%}, "
              f"목표 {b['exit_target']:.0%} / 손절 {b['exit_stop']:.0%} / 만료 {b['exit_time']:.0%}")
    if a.cmd == "pending-meta":
        d, _, _ = render.load(ROOT, a.as_of)
        for c in d["candidates"]:
            if not c["meta"].get("ipo_price"):
                print(f"{c['code']}\t{c['name']}\t상장 {c['listed']}\t점수 {c['score']['total']:.0f}")


if __name__ == "__main__":
    main()
