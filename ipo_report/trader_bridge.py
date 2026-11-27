"""일일 보고서의 10종목 매매표 → trader-skill 파일 (trader/ 폴더) → trader-risk 점검.

trader-skill(Jakeschincariol/trader-skill)은 미국 시장용이지만 파일 형식이 단순해서
한국 종목도 그대로 넘길 수 있다. trader-risk 는 config "market": "KRX" 일 때 6자리 종목코드를 받도록
로컬 패치했다(.claude/skills/trader-risk/SOURCE.txt).

  trader/config.json   규칙 (없을 때만 만든다. 사용자가 고친 값은 유지, --budget 을 주면 예산만 바꾼다)
  trader/prices.json   10종목 기준 가격 (원)
  trader/orders.json   10종목 매수 제안 (주 단위 지정가, 상태 proposed)
  trader/metrics.json  trader-screener 용 지표 (매출 성장률, 영업이익률, PER, 점수 …)

주문은 제안일 뿐이다. 이 환경에는 국내 증권사 연결이 없으므로 trader-broker 로 주문하지 않는다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .plan import shares
from .render import top_picks, verdict_of

RISK_SCRIPT = Path(".claude/skills/trader-risk/check.py")


def default_config(budget: float) -> dict:
    return {
        "budget": budget,
        "currency": "KRW",
        "market": "KRX",
        "cash_buffer_pct": 10,
        "max_position_pct": 15,
        "max_order_usd": round(budget * 0.15),  # 원 단위 (필드 이름은 trader-skill 호환)
        "min_order_usd": 10000,
        "max_orders_per_day": 10,
        "fractional": False,
        "broker": "manual",
        "paper": True,
        "blocked": [],
        "allow_options": False,
        "allow_margin": False,
        "allow_short": False,
    }


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def export(root: Path, data: dict, notes: dict, budget: float | None = None) -> dict:
    tdir = root / "trader"
    cfg_path = tdir / "config.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        if budget:
            cfg["budget"] = budget
    else:
        cfg = default_config(budget or 10_000_000)
    _write(cfg_path, cfg)
    b = float(cfg["budget"])

    picks = top_picks(data)
    _write(tdir / "prices.json", {"as_of": data["as_of"], "currency": "KRW",
                                  "prices": {c["code"]: c["plan"]["entry"] for c in picks}})
    orders = []
    for c in picks:
        v = verdict_of(c, notes)
        s = shares(b, c["plan"], v, c["price"]["tv_5d"])
        if s["shares"] <= 0:
            continue
        orders.append({
            "symbol": c["code"], "side": "buy", "qty": str(s["shares"]),
            "type": "limit", "limit_price": str(int(c["plan"]["entry"])), "time_in_force": "day",
            "why": f"{c['name']} {v} {c['score']['total']:.0f}점, 손절 {c['plan']['stop']:,.0f} / 목표 {c['plan']['target']:,.0f}",
        })
    _write(tdir / "orders.json", {
        "created_by": "kr-ipo-daily-report",
        "created_at": datetime.now().replace(microsecond=0).isoformat(),
        "status": "proposed",
        "paper": True,
        "note": "제안일 뿐입니다. 주문은 본인이 증권사 앱에서 직접 넣습니다.",
        "source": f"data/candidates/{data['as_of']}.json",
        "orders": orders,
    })
    metrics = []
    for c in data["candidates"]:
        f = c["fund"]
        rev, op = f.get("rev") or [], f.get("op") or []
        metrics.append({
            "ticker": c["code"], "name": c["name"], "market": c["market"],
            "price": c["price"]["close"],
            "revenue_growth_yoy": round(c["score"]["rev_yoy"] * 100, 1) if c["score"].get("rev_yoy") is not None else None,
            "operating_margin": round(op[-1][1] / rev[-1][1] * 100, 1) if op and rev and rev[-1][1] > 0 else None,
            "pe": c.get("per"),
            "psr": round(c["score"]["psr"], 2) if c["score"].get("psr") else None,
            "vs_ipo_pct": round(c["price"]["vs_base"] * 100, 1) if c["price"].get("ipo_price") else None,
            "demand_competition": c["meta"].get("demand_competition"),
            "lockup_commit_pct": c["meta"].get("lockup_commit_pct"),
            "score": c["score"]["total"],
        })
    _write(tdir / "metrics.json", metrics)
    return {"budget": b, "orders": orders, "config": cfg}


def risk_check(root: Path) -> dict:
    """trader-risk check.py 를 돌려 PASS/BLOCK 결과(JSON)를 받는다. 기록은 남기지 않는다(--no-write)."""
    out = subprocess.run(
        [sys.executable, str(RISK_SCRIPT), "--json", "--no-write"],
        cwd=root, capture_output=True, text=True, timeout=60,
    )
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"trader-risk 실행 실패: {out.stderr or out.stdout}")
