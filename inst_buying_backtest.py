#!/usr/bin/env python3
"""
기관 N일 연속 순매수 → 다음 날 시가 매수 → H거래일 보유 백테스트

전략 (기본값)
  - 신호일 t : 기관합계 순매수거래대금 > 0 이 3거래일 연속(t-2, t-1, t)
               연속일수가 '정확히 3'이 되는 날만 신호 (4일째, 5일째 연속은 중복 신호로 보지 않음)
  - 진입     : t+1 거래일 시가 매수
  - 청산     : 진입일 포함 5거래일째(t+5) 종가 매도
  - 비용     : 매수/매도 수수료 각 0.015%, 매도 거래세 0.20%, 슬리피지 편도 0.05%
  - 외국인   : --foreign streak  → 같은 N일 동안 기관·외국인 모두 매일 순매수 (쌍끌이)
               --foreign signal  → 기관 N일 연속 + 신호일에 외국인도 순매수
               --compare         → off / signal / streak 세 조건 비교표

데이터
  - pykrx (KRX 정보데이터시스템). pykrx 1.2.x 부터 KRX 계정 로그인이 필요합니다.
        export KRX_ID=아이디
        export KRX_PW=비밀번호
  - 일자별 전종목 시세 + 기관합계 순매수를 하루 단위로 받아 ./cache 에 저장합니다.
    (첫 실행은 3년치 약 750거래일 × 6회 호출이라 수십 분 걸리고, 이후엔 캐시를 씁니다.)

수익률 계산
  - 진입일 수익률은 시가→종가, 이후 보유일은 KRX 등락률(FLUC_RT)을 연결해서 계산합니다.
    등락률은 액면분할·유상증자 등 권리락 기준가를 반영하므로 수정주가 없이도 왜곡이 적습니다.

사용 예
  python inst_buying_backtest.py                       # 최근 3년, 기본 설정
  python inst_buying_backtest.py --start 20231127 --end 20261125
  python inst_buying_backtest.py --streak 3 --hold 5 --min-value 1e9 --max-per-day 20
  python inst_buying_backtest.py --streak-mode atleast  # 3일 이상 연속인 날마다 신호
  python inst_buying_backtest.py --foreign streak --compare  # 기관+외국인 동시 순매수, 비교표
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

MARKETS = ("KOSPI", "KOSDAQ")
COLS = ["market", "name", "open", "high", "low", "close", "volume", "value", "chg", "inst_net", "frgn_net"]


# ---------------------------------------------------------------------------
# 1. 데이터 수집 (pykrx + 일자별 캐시)
# ---------------------------------------------------------------------------
def _import_pykrx():
    """pykrx 는 import 시점에 KRX 로그인을 하므로 환경변수 확인 후 늦게 import 한다."""
    if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
        sys.exit(
            "[오류] KRX 로그인 정보가 없습니다.\n"
            "  pykrx 1.2.x 는 data.krx.co.kr 계정이 필요합니다.\n"
            "  export KRX_ID=아이디 ; export KRX_PW=비밀번호  후 다시 실행하세요."
        )
    from pykrx import stock  # noqa: WPS433

    return stock


def _retry(fn, *args, tries: int = 4, pause: float = 1.5):
    for i in range(tries):
        try:
            return fn(*args)
        except Exception as e:  # 네트워크/파싱 오류
            if i == tries - 1:
                raise
            print(f"    재시도 {i + 1}/{tries - 1}: {e}")
            time.sleep(pause * (i + 1))


def fetch_day(stock, date: str, sleep: float) -> pd.DataFrame:
    """하루치 KOSPI+KOSDAQ 전종목 시세와 기관합계 순매수거래대금. 휴장일이면 빈 DataFrame."""
    frames = []
    for mkt in MARKETS:
        ohlcv = _retry(stock.get_market_ohlcv_by_ticker, date, mkt)
        time.sleep(sleep)
        if ohlcv is None or ohlcv.empty or (ohlcv[["시가", "고가", "저가", "종가"]] == 0).all(axis=None):
            return pd.DataFrame(columns=COLS)  # 휴장일
        net = _retry(stock.get_market_net_purchases_of_equities, date, date, mkt, "기관합계")
        time.sleep(sleep)
        frgn = _retry(stock.get_market_net_purchases_of_equities, date, date, mkt, "외국인")
        time.sleep(sleep)

        df = pd.DataFrame(
            {
                "market": mkt,
                "open": ohlcv["시가"],
                "high": ohlcv["고가"],
                "low": ohlcv["저가"],
                "close": ohlcv["종가"],
                "volume": ohlcv["거래량"],
                "value": ohlcv["거래대금"],
                "chg": ohlcv["등락률"] if "등락률" in ohlcv else np.nan,
            }
        )
        if net is not None and not net.empty:
            df["name"] = net["종목명"].reindex(df.index)
            # 순매수 목록에 없는 종목 = 기관 거래 없음 → 0
            df["inst_net"] = net["순매수거래대금"].reindex(df.index).fillna(0)
        else:
            df["name"] = None
            df["inst_net"] = 0.0
        if frgn is not None and not frgn.empty:
            df["frgn_net"] = frgn["순매수거래대금"].reindex(df.index).fillna(0)
            df["name"] = df["name"].fillna(frgn["종목명"].reindex(df.index))
        else:
            df["frgn_net"] = 0.0
        frames.append(df)
    out = pd.concat(frames)
    out.index.name = "ticker"
    return out[COLS]


def load_data(start: str, end: str, cache_dir: Path, sleep: float) -> pd.DataFrame:
    """start~end 평일을 돌며 캐시가 없으면 받아온다. long-format (date, ticker) 반환."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    days = pd.bdate_range(start, end).strftime("%Y%m%d")
    today = datetime.now().strftime("%Y%m%d")
    stock = None
    frames = []
    for i, d in enumerate(days, 1):
        path = cache_dir / f"{d}.pkl"
        df = pd.read_pickle(path) if path.exists() else None
        if df is not None and not df.empty and "frgn_net" not in df:
            df = None                                # 구버전 캐시(외국인 없음) → 다시 받기
        if df is None:
            if stock is None:
                stock = _import_pykrx()
            print(f"  [{i}/{len(days)}] {d} 다운로드")
            df = fetch_day(stock, d, sleep)
            if d < today:  # 장중 데이터는 캐시하지 않음
                df.to_pickle(path)
        if not df.empty:
            df = df.copy()
            df["date"] = pd.Timestamp(d)
            frames.append(df.reset_index())
    if not frames:
        sys.exit("[오류] 데이터가 없습니다. 기간/로그인을 확인하세요.")
    data = pd.concat(frames, ignore_index=True)
    data["ticker"] = data["ticker"].astype(str)
    return data


# ---------------------------------------------------------------------------
# 2. 백테스트
# ---------------------------------------------------------------------------
@dataclass
class Config:
    streak: int = 3                # 연속 순매수 일수
    streak_mode: str = "exact"     # exact: 연속일수가 정확히 N인 날만 / atleast: N 이상인 모든 날
    hold: int = 5                  # 보유 거래일 수 (진입일 포함)
    fee: float = 0.00015           # 편도 수수료
    tax: float = 0.0020            # 매도 거래세
    slippage: float = 0.0005       # 편도 슬리피지
    min_value: float = 1e9         # 신호일 거래대금 하한 (원)
    min_price: float = 1000        # 신호일 종가 하한 (원)
    min_inst_net: float = 0        # 신호일 기관 순매수 금액 하한 (원)
    foreign: str = "off"           # off: 외국인 무관 / streak: N일 모두 외국인도 동시 순매수 / signal: 신호일에만 외국인 순매수
    max_per_day: int = 0           # 하루 최대 편입 종목 수 (0=제한 없음, 신호일 순매수/거래대금 비율 순)
    skip_limit_up_open: bool = True  # 진입일 시가가 상한가(+29%↑)면 체결 불가로 제외


_WIDE_CACHE: dict = {}


def to_wide(data: pd.DataFrame, col: str) -> pd.DataFrame:
    key = (id(data), len(data), col)                 # 민감도 분석에서 여러 번 돌릴 때 pivot 재사용
    if key not in _WIDE_CACHE:
        _WIDE_CACHE[key] = data.pivot(index="date", columns="ticker", values=col).sort_index()
    return _WIDE_CACHE[key]


def consecutive_streak(pos: pd.DataFrame) -> pd.DataFrame:
    """True 가 연속된 일수 (False/NaN 이면 0으로 리셋)."""
    p = pos.fillna(False).astype(int)
    cs = p.cumsum()
    last_reset = cs.where(p == 0).ffill().fillna(0)  # 마지막 False 시점의 누적값
    return (cs - last_reset).astype(int)


def forward_trade_returns(o, c, chg, hold: int) -> pd.DataFrame:
    """신호일 t 기준: t+1 시가 매수 → t+hold 종가 매도의 총수익률(비용 전)."""
    r = 1 + chg / 100.0
    entry_open = o.shift(-1)
    entry_day = (c / o.where(o > 0)).shift(-1)       # 진입일 시가→종가
    gross = entry_day.copy()
    for k in range(2, hold + 1):                     # 이후 보유일 등락률 연결
        gross = gross * r.shift(-k)
    return gross - 1, entry_open


def run_backtest(data: pd.DataFrame, start: str, cfg: Config):
    o, c, chg = to_wide(data, "open"), to_wide(data, "close"), to_wide(data, "chg")
    val, net = to_wide(data, "value"), to_wide(data, "inst_net")
    frgn = to_wide(data, "frgn_net").fillna(0) if "frgn_net" in data else net * 0
    trading = c.notna() & (val > 0)                  # 상장 & 거래 발생일

    pos = (net > 0) & trading
    if cfg.foreign == "streak":                      # 기관·외국인 쌍끌이가 N일 연속
        pos &= frgn > 0
    st = consecutive_streak(pos)
    sig = (st == cfg.streak) if cfg.streak_mode == "exact" else (st >= cfg.streak)
    if cfg.foreign == "signal":                      # 기관 N일 연속 + 신호일에 외국인도 순매수
        sig &= frgn > 0

    gross, entry_open = forward_trade_returns(o, c, chg, cfg.hold)
    gap = entry_open / c - 1                         # 신호일 종가 대비 진입 시가 갭

    # 공통 유니버스 필터 (신호일 기준)
    universe = trading & (val >= cfg.min_value) & (c >= cfg.min_price) & (entry_open > 0)
    if cfg.skip_limit_up_open:
        universe &= gap < 0.29
    universe.loc[universe.index < pd.Timestamp(start)] = False

    def cost(g):
        return (1 + g) * (1 - cfg.slippage) * (1 - cfg.fee - cfg.tax) / ((1 + cfg.slippage) * (1 + cfg.fee)) - 1

    # ---- 전략 매매 ----
    mask = (sig & universe & (net >= cfg.min_inst_net)).fillna(False)
    m = mask.stack()
    keys = m[m].index                                # (signal_date, ticker)

    def pick(df):
        return df.stack(future_stack=True).reindex(keys).to_numpy()

    trades = pd.DataFrame(
        {
            "signal_date": keys.get_level_values(0),
            "ticker": keys.get_level_values(1),
            "gross": pick(gross),
            "inst_net": pick(net),
            "frgn_net": pick(frgn),
            "value": pick(val),
            "signal_close": pick(c),
            "entry_open": pick(entry_open),
            "entry_gap": pick(gap),
        }
    )
    n_raw = len(trades)
    trades = trades.dropna(subset=["gross"]).copy()  # 보유기간 미완료/상장폐지 제외
    n_incomplete = n_raw - len(trades)

    trades["strength"] = trades["inst_net"] / trades["value"]
    if cfg.max_per_day > 0:
        trades = (
            trades.sort_values("strength", ascending=False)
            .groupby("signal_date", group_keys=False)
            .head(cfg.max_per_day)
        )
    trades["ret"] = cost(trades["gross"])

    dates = c.index
    pos_of = pd.Series(np.arange(len(dates)), index=dates)
    si = pos_of.reindex(trades["signal_date"]).to_numpy()
    trades["entry_date"] = dates[si + 1]
    trades["exit_date"] = dates[si + cfg.hold]

    meta = data.drop_duplicates("ticker", keep="last").set_index("ticker")[["name", "market"]]
    trades = trades.join(meta, on="ticker").sort_values(["signal_date", "ticker"]).reset_index(drop=True)

    # ---- 벤치마크: 같은 필터의 모든 종목-일자에 같은 방식으로 진입했을 때 ----
    bench = cost(gross.where(universe).stack().dropna())

    period_dates = dates[dates >= pd.Timestamp(start)]
    return trades, bench, n_incomplete, period_dates


# ---------------------------------------------------------------------------
# 3. 성과 집계
# ---------------------------------------------------------------------------
def stats(r: pd.Series) -> dict:
    if len(r) == 0:
        return {"거래수": 0}
    win, loss = r[r > 0], r[r <= 0]
    pf = win.sum() / -loss.sum() if loss.sum() < 0 else np.inf
    return {
        "거래수": len(r),
        "승률": (r > 0).mean(),
        "평균수익률": r.mean(),
        "중앙값": r.median(),
        "평균수익(승)": win.mean() if len(win) else np.nan,
        "평균손실(패)": loss.mean() if len(loss) else np.nan,
        "손익비": (win.mean() / -loss.mean()) if len(win) and len(loss) and loss.mean() < 0 else np.nan,
        "Profit Factor": pf,
        "표준편차": r.std(),
        "최대수익": r.max(),
        "최대손실": r.min(),
    }


def sleeve_equity(trades: pd.DataFrame, dates: pd.DatetimeIndex, hold: int) -> pd.DataFrame:
    """
    자금을 hold 개 슬리브로 나눠, 매 거래일 한 슬리브씩 그날 진입 종목에 동일가중 투자.
    진입일 i 의 슬리브 = i % hold. 청산일(i+hold-1) 종가에 손익 확정 → 다음 날 같은 슬리브가 재진입.
    중복 포지션 없이 복리로 굴리는 현실적인 운용 가정. 손익은 청산일에 반영(미실현 평가 없음).
    """
    pos = pd.Series(np.arange(len(dates)), index=dates)
    cohort = trades.groupby("entry_date").agg(ret=("ret", "mean"), exit_date=("exit_date", "first"))
    cohort["sleeve"] = pos.reindex(cohort.index).to_numpy() % hold
    by_exit = cohort.groupby("exit_date")
    sleeves = np.ones(hold) / hold
    rows = []
    for d in dates:
        if d in by_exit.groups:
            for _, row in by_exit.get_group(d).iterrows():
                sleeves[int(row["sleeve"])] *= 1 + row["ret"]
        rows.append((d, sleeves.sum()))
    eq = pd.DataFrame(rows, columns=["date", "equity"]).set_index("date")
    eq["drawdown"] = eq["equity"] / eq["equity"].cummax() - 1
    return eq


def fmt(d: dict) -> str:
    lines = []
    for k, v in d.items():
        if isinstance(v, (int, np.integer)):
            lines.append(f"  {k:<14}{v:>12,d}")
        elif k in ("손익비", "Profit Factor"):
            lines.append(f"  {k:<14}{v:>12.2f}")
        else:
            lines.append(f"  {k:<14}{v:>11.2%}")
    return "\n".join(lines)


FOREIGN_LABEL = {"off": "외국인 조건 없음", "streak": "외국인도 같은 기간 매일 순매수", "signal": "신호일 외국인 순매수"}


def portfolio(trades, dates, hold):
    eq = sleeve_equity(trades, dates, hold)
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    return eq, eq["equity"].iloc[-1] - 1, eq["equity"].iloc[-1] ** (1 / years) - 1, eq["drawdown"].min()


def report(trades, bench, n_incomplete, dates, cfg: Config, start, end, out_dir: Path):
    print("\n" + "=" * 64)
    print(f" 기관 {cfg.streak}일 연속 순매수({cfg.streak_mode}) + {FOREIGN_LABEL[cfg.foreign]}")
    print(f" → 익일 시가 매수 → {cfg.hold}거래일 보유")
    print(f" 기간 {start} ~ {end} | 비용: 수수료 {cfg.fee:.3%}×2, 세금 {cfg.tax:.2%}, 슬리피지 {cfg.slippage:.2%}×2")
    print(f" 필터: 거래대금≥{cfg.min_value:,.0f}원, 종가≥{cfg.min_price:,.0f}원"
          + (f", 하루 최대 {cfg.max_per_day}종목" if cfg.max_per_day else ""))
    print("=" * 64)

    r = trades["ret"]
    print("\n[전략 - 비용 차감 후, 거래 1건당]")
    print(fmt(stats(r)))
    print(f"  (보유기간 미완료/데이터 누락으로 제외된 신호: {n_incomplete:,}건)")

    print("\n[벤치마크 - 같은 필터의 모든 종목·일자에 무조건 진입]")
    b = stats(bench)
    print(f"  승률 {b['승률']:.2%} | 평균 {b['평균수익률']:.2%} | 중앙값 {b['중앙값']:.2%} | 표본 {b['거래수']:,}")
    print(f"  → 전략 초과: 승률 {stats(r)['승률'] - b['승률']:+.2%}p, 평균수익률 {r.mean() - b['평균수익률']:+.2%}p")

    print("\n[연도별]")
    by_y = trades.groupby(trades["entry_date"].dt.year)["ret"]
    print(pd.DataFrame({"거래수": by_y.size(), "승률": by_y.apply(lambda x: (x > 0).mean()),
                        "평균": by_y.mean(), "중앙값": by_y.median()})
          .to_string(formatters={"승률": "{:.2%}".format, "평균": "{:.2%}".format, "중앙값": "{:.2%}".format}))

    print("\n[시장별]")
    by_m = trades.groupby("market")["ret"]
    print(pd.DataFrame({"거래수": by_m.size(), "승률": by_m.apply(lambda x: (x > 0).mean()),
                        "평균": by_m.mean(), "중앙값": by_m.median()})
          .to_string(formatters={"승률": "{:.2%}".format, "평균": "{:.2%}".format, "중앙값": "{:.2%}".format}))

    eq, total, cagr, _ = portfolio(trades, dates, cfg.hold)
    print(f"\n[포트폴리오 - 자금 {cfg.hold}분할 순환, 신호 종목 동일가중]")
    print(f"  누적수익률 {total:.2%} | CAGR {cagr:.2%} | MDD {eq['drawdown'].min():.2%}")
    print(f"  거래일당 평균 신호 수 {trades.groupby('entry_date').size().mean():.1f}종목, "
          f"신호 발생일 비율 {trades['entry_date'].nunique() / len(dates):.1%}")

    out_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out_dir / "trades.csv", index=False, encoding="utf-8-sig")
    eq.to_csv(out_dir / "equity.csv", encoding="utf-8-sig")
    print(f"\n거래내역: {out_dir / 'trades.csv'}\n자산곡선: {out_dir / 'equity.csv'}")

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True, height_ratios=[3, 1])
        a1.plot(eq.index, eq["equity"], lw=1.4)
        a1.set_title(f"Institutional {cfg.streak}-day net buying, hold {cfg.hold}d (net of costs)")
        a1.set_ylabel("Equity")
        a1.grid(alpha=0.3)
        a2.fill_between(eq.index, eq["drawdown"], 0, alpha=0.5)
        a2.set_ylabel("Drawdown")
        a2.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(out_dir / "equity.png", dpi=120)
        print(f"차트:     {out_dir / 'equity.png'}")
    except ImportError:
        pass


def compare_foreign(data, start, cfg: Config):
    rows = {}
    for mode in ("off", "signal", "streak"):
        c2 = Config(**{**cfg.__dict__, "foreign": mode})
        tr, bench, _, dates = run_backtest(data, start, c2)
        if tr.empty:
            rows[FOREIGN_LABEL[mode]] = {"거래수": 0}
            continue
        st = stats(tr["ret"])
        _, total, cagr, mdd = portfolio(tr, dates, c2.hold)
        rows[FOREIGN_LABEL[mode]] = {
            "거래수": st["거래수"], "승률": st["승률"], "평균": st["평균수익률"], "중앙값": st["중앙값"],
            "손익비": st["손익비"], "PF": st["Profit Factor"], "누적": total, "CAGR": cagr, "MDD": mdd,
        }
    rows["벤치마크(무조건 진입)"] = {"거래수": len(bench), "승률": (bench > 0).mean(),
                               "평균": bench.mean(), "중앙값": bench.median()}
    df = pd.DataFrame(rows).T
    pct = "{:.2%}".format
    print("\n[외국인 조건 비교 - 비용 차감 후]")
    print(df.to_string(na_rep="-", formatters={
        "거래수": lambda v: f"{int(v):,}", "승률": pct, "평균": pct, "중앙값": pct,
        "손익비": "{:.2f}".format, "PF": "{:.2f}".format, "누적": pct, "CAGR": pct, "MDD": pct}))


# ---------------------------------------------------------------------------
# 4. 견고성 검증 (backtest-expert 스킬 방법론: "이익이 최대인 전략이 아니라 가장 덜 깨지는 전략")
# ---------------------------------------------------------------------------
EVALUATOR = Path(__file__).resolve().parent / ".claude/skills/backtest-expert/scripts/evaluate_backtest.py"


def _summary(cfg: Config, data, start, end=None):
    tr, _, _, dates = run_backtest(data, start, cfg)
    if end is not None:
        tr = tr[tr["entry_date"] <= pd.Timestamp(end)]
        dates = dates[dates <= pd.Timestamp(end)]
    if len(tr) == 0:
        return None, tr, dates
    return stats(tr["ret"]), tr, dates


def robustness(data, start, end, cfg: Config, out_dir: Path):
    pct = "{:.2%}".format
    print("\n" + "=" * 64)
    print(" 견고성 검증 (backtest-expert)")
    print("=" * 64)

    # (1) 파라미터 민감도: 연속일수 × 보유일 — 좁은 '봉우리'가 아니라 넓은 '고원'이 있어야 함
    streaks, holds = [2, 3, 4, 5], [3, 5, 7, 10]
    avg = pd.DataFrame(index=pd.Index(streaks, name="연속일"), columns=pd.Index(holds, name="보유일"), dtype=float)
    win = avg.copy()
    for n in streaks:
        for h in holds:
            st, *_ = _summary(Config(**{**cfg.__dict__, "streak": n, "hold": h}), data, start)
            if st:
                avg.loc[n, h], win.loc[n, h] = st["평균수익률"], st["승률"]
    print("\n[1] 파라미터 민감도 - 거래당 평균수익률 (비용 차감)")
    print(avg.map(lambda v: "-" if pd.isna(v) else pct(v)).to_string())
    print("\n    승률")
    print(win.map(lambda v: "-" if pd.isna(v) else pct(v)).to_string())
    pos_cells = (avg > 0).sum().sum()
    print(f"    → 양(+)의 평균수익률 칸: {pos_cells}/{avg.notna().sum().sum()}")

    # (2) 비용 스트레스: 슬리피지·수수료 1x / 1.5x / 2x
    print("\n[2] 비용 스트레스")
    rows = []
    for m in (1.0, 1.5, 2.0):
        c2 = Config(**{**cfg.__dict__, "slippage": cfg.slippage * m, "fee": cfg.fee * m})
        st, tr, dates = _summary(c2, data, start)
        _, total, cagr, mdd = portfolio(tr, dates, c2.hold)
        rows.append({"배수": f"{m:.1f}x", "승률": st["승률"], "평균": st["평균수익률"], "누적": total, "MDD": mdd})
    print(pd.DataFrame(rows).set_index("배수").to_string(formatters={k: pct for k in ("승률", "평균", "누적", "MDD")}))

    # (3) 연도별 일관성
    base, tr, dates = _summary(cfg, data, start)
    yearly = tr.groupby(tr["entry_date"].dt.year)["ret"].mean()
    print(f"\n[3] 연도별 일관성: 평균수익률 양(+)인 해 {int((yearly > 0).sum())}/{len(yearly)}"
          f"  ({', '.join(f'{y}:{v:+.2%}' for y, v in yearly.items())})")

    # (4) 표본 외 검증: 앞 2/3 기간에서 최적 파라미터 선택 → 뒤 1/3에서 성과 비교
    t0, t1 = pd.Timestamp(start), pd.Timestamp(end)
    split = t0 + (t1 - t0) * 2 / 3
    best, best_avg = None, -np.inf
    for n in streaks:
        for h in holds:
            st, *_ = _summary(Config(**{**cfg.__dict__, "streak": n, "hold": h}), data, start, split)
            if st and st["거래수"] >= 30 and st["평균수익률"] > best_avg:
                best, best_avg = (n, h), st["평균수익률"]
    print(f"\n[4] 표본 외 검증 (IS {t0:%Y-%m-%d}~{split:%Y-%m-%d} / OOS ~{t1:%Y-%m-%d})")
    if best:
        oos_tr = run_backtest(data, start, Config(**{**cfg.__dict__, "streak": best[0], "hold": best[1]}))[0]
        oos = oos_tr[oos_tr["entry_date"] > split]["ret"]
        ratio = oos.mean() / best_avg if best_avg > 0 else np.nan
        print(f"    IS 최적: 연속 {best[0]}일 · 보유 {best[1]}일, IS 평균 {best_avg:+.2%}")
        print(f"    OOS 평균 {oos.mean():+.2%} ({len(oos):,}건) → OOS/IS = {ratio:.0%}"
              + ("  ⚠ 50% 미만: 과최적화 의심" if not (ratio >= 0.5) else ""))
        base_oos = tr[tr["entry_date"] > split]["ret"]
        print(f"    기본 파라미터(연속 {cfg.streak}·보유 {cfg.hold}) OOS 평균 {base_oos.mean():+.2%}")

    # (5) backtest-expert 평가 스크립트로 정량 판정
    _, _, _, mdd = portfolio(tr, dates, cfg.hold)
    years = int((t1 - t0).days // 365)
    n_params = 4 + (cfg.foreign != "off")             # streak, hold, min_value, min_price (+ foreign)
    args = ["--total-trades", str(base["거래수"]), "--win-rate", f"{base['승률'] * 100:.4f}",
            "--avg-win-pct", f"{base['평균수익(승)'] * 100:.4f}",
            "--avg-loss-pct", f"{abs(base['평균손실(패)']) * 100:.4f}",
            "--max-drawdown-pct", f"{abs(mdd) * 100:.4f}", "--years-tested", str(max(years, 1)),
            "--num-parameters", str(n_params), "--slippage-tested", "--output-dir", str(out_dir / "reports")]
    print("\n[5] backtest-expert 정량 평가")
    if EVALUATOR.exists():
        res = subprocess.run([sys.executable, str(EVALUATOR), *args], capture_output=True, text=True)
        print("    " + (res.stdout or res.stderr).strip().replace("\n", "\n    "))
        if years < 5:
            print("    ※ 검증 기간 5년 미만이면 VALIDATION_REQUIRED 가 나옵니다. --start 를 5년 이상 앞당겨 보세요.")
    else:
        print(f"    평가 스크립트 없음: {EVALUATOR}\n    수동 실행 인자: {' '.join(args)}")


# ---------------------------------------------------------------------------
def main():
    today = datetime.now()
    ap = argparse.ArgumentParser(description="기관 연속 순매수 백테스트")
    ap.add_argument("--start", default=(today - timedelta(days=365 * 3)).strftime("%Y%m%d"))
    ap.add_argument("--end", default=(today - timedelta(days=1)).strftime("%Y%m%d"))
    ap.add_argument("--streak", type=int, default=3)
    ap.add_argument("--streak-mode", choices=["exact", "atleast"], default="exact")
    ap.add_argument("--hold", type=int, default=5)
    ap.add_argument("--fee", type=float, default=0.00015)
    ap.add_argument("--tax", type=float, default=0.0020)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--min-value", type=float, default=1e9, help="신호일 거래대금 하한(원)")
    ap.add_argument("--min-price", type=float, default=1000)
    ap.add_argument("--min-inst-net", type=float, default=0, help="신호일 기관 순매수 하한(원)")
    ap.add_argument("--max-per-day", type=int, default=0)
    ap.add_argument("--foreign", choices=["off", "streak", "signal"], default="off",
                    help="streak: N일 모두 기관+외국인 동시 순매수 / signal: 신호일에만 외국인 순매수")
    ap.add_argument("--compare", action="store_true", help="외국인 조건 off/signal/streak 비교표 출력")
    ap.add_argument("--robustness", action="store_true",
                    help="파라미터 민감도·비용 스트레스·연도별·표본외 검증 + backtest-expert 평가")
    ap.add_argument("--allow-limit-up-open", action="store_true", help="상한가 시가 진입도 허용")
    ap.add_argument("--cache", default="cache")
    ap.add_argument("--out", default="output")
    ap.add_argument("--sleep", type=float, default=0.3, help="KRX 호출 간격(초)")
    a = ap.parse_args()

    cfg = Config(a.streak, a.streak_mode, a.hold, a.fee, a.tax, a.slippage, a.min_value,
                 a.min_price, a.min_inst_net, a.foreign, a.max_per_day, not a.allow_limit_up_open)

    # 연속 판정용 앞쪽 여유 + 청산용 뒤쪽은 end 까지의 데이터로 처리
    fetch_start = (pd.Timestamp(a.start) - pd.tseries.offsets.BDay(cfg.streak + 10)).strftime("%Y%m%d")
    print(f"데이터 로딩 {fetch_start} ~ {a.end} (캐시: {a.cache}/)")
    data = load_data(fetch_start, a.end, Path(a.cache), a.sleep)

    if a.compare:
        compare_foreign(data, a.start, cfg)

    trades, bench, n_inc, dates = run_backtest(data, a.start, cfg)
    if trades.empty:
        sys.exit("신호가 없습니다. 필터를 완화해 보세요.")
    report(trades, bench, n_inc, dates, cfg, a.start, a.end, Path(a.out))
    if a.robustness:
        robustness(data, a.start, a.end, cfg, Path(a.out))


if __name__ == "__main__":
    main()
