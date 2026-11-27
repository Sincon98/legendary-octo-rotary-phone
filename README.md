# 기관 연속 순매수 백테스트

기관(기관합계)이 **3거래일 연속 순매수**한 종목을 **다음 날 시가에 매수**해 **5거래일 보유(진입일 포함, 5일째 종가 매도)**했을 때의 승률·수익률을 계산합니다. 대상은 KOSPI와 KOSDAQ 전 종목입니다.

## 실행

```bash
pip install -r requirements.txt
export KRX_ID=KRX정보데이터시스템_아이디   # pykrx 1.2.x부터 로그인 필요
export KRX_PW=비밀번호
python inst_buying_backtest.py            # 기본: 최근 3년
python inst_buying_backtest.py --foreign streak --compare   # 기관+외국인 동시 순매수 + 비교표
```

첫 실행에서는 약 750거래일 × 6회 KRX를 호출하므로 시간이 걸립니다. 받은 데이터는 `cache/`에 날짜별로 저장되고, 다음 실행부터는 이 캐시를 사용합니다. 외국인 데이터가 없는 이전 캐시는 자동으로 다시 받습니다.

## 주요 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--start/--end` | 3년 전 ~ 어제 | 백테스트 기간 (YYYYMMDD) |
| `--streak` | 3 | 연속 순매수 일수 |
| `--streak-mode` | exact | `exact`는 연속 일수가 정확히 N이 되는 날만 신호로 봅니다. `atleast`는 N일 이상 이어지는 동안 매일 신호로 봅니다. |
| `--hold` | 5 | 보유 거래일 |
| `--fee / --tax / --slippage` | 0.015% / 0.20% / 0.05% | 비용 |
| `--min-value` | 10억 원 | 신호일 거래대금 하한 |
| `--min-inst-net` | 0 | 신호일 기관 순매수 금액 하한 |
| `--foreign` | off | `streak`: 같은 N일 동안 기관과 외국인이 모두 매일 순매수 / `signal`: 기관 N일 연속 + 신호일에 외국인 순매수 |
| `--compare` | - | 외국인 조건 off·signal·streak 비교표 출력 |
| `--robustness` | - | 견고성 검증 실행 (아래 참고) |
| `--max-per-day` | 0(무제한) | 하루 편입 종목 수 상한 (순매수/거래대금 비율이 큰 순서) |

## 출력

- 거래당 승률, 평균·중앙값 수익률, 손익비, Profit Factor
- 벤치마크: 같은 필터를 통과한 모든 종목·일자에 무조건 진입했을 때와 비교
- 연도별·시장별 성과
- 포트폴리오: 자금을 5개로 나눠 매일 한 몫씩 순환 투자했을 때의 누적수익률, CAGR, MDD
- `output/trades.csv`, `output/equity.csv`, `output/equity.png`

## 견고성 검증 (`--robustness`)

[`backtest-expert`](.claude/skills/backtest-expert/SKILL.md) 스킬(tradermonty/claude-trading-skills, MIT)의 방법론을 따릅니다. 이 스킬은 `.claude/skills/`에 설치돼 있어 이 저장소에서 Claude Code를 쓰면 자동으로 인식됩니다. 목표는 수익이 가장 큰 설정이 아니라 조건을 바꿔도 **가장 덜 무너지는** 설정을 찾는 것입니다.

1. **파라미터 민감도**: 연속일(2~5) × 보유일(3·5·7·10) 격자에서 평균수익률과 승률을 봅니다. 특정 한 칸만 좋은 '봉우리'가 아니라 넓게 고르게 좋은 '고원'이 있어야 합니다.
2. **비용 스트레스**: 슬리피지와 수수료를 1.5배, 2배로 늘려도 수익이 남는지 확인합니다.
3. **연도별 일관성**: 평균수익률이 양(+)인 해가 과반인지 봅니다.
4. **표본 외 검증**: 앞 2/3 기간에서 고른 최적 파라미터가 뒤 1/3 기간에서도 통하는지 봅니다. OOS/IS가 50% 미만이면 과최적화를 의심합니다.
5. **정량 판정**: `evaluate_backtest.py`로 100점 만점 점수와 `decision`(DEPLOY/REFINE/ABANDON/VALIDATION_REQUIRED 등)을 냅니다. 결과는 `output/reports/`에 저장됩니다.

검증 기간이 5년 미만이면 판정이 `VALIDATION_REQUIRED`로 막힙니다. 제대로 판정하려면 `--start`를 5년 이상 앞으로 당기세요.

```bash
python inst_buying_backtest.py --start 20200101 --foreign streak --compare --robustness
```

## 한계

- 상장폐지 등으로 보유 기간 중 데이터가 끊긴 거래는 제외합니다. 이 때문에 생존 편향이 일부 남아 있습니다.
- 진입일 시가가 +29% 이상이면 상한가라 체결이 어렵다고 보고 제외합니다.
- 체결 가능 수량(호가 잔량)은 고려하지 않습니다.

테스트(KRX 접속 불필요): `python -m pytest tests -q`

---

# 신규상장 1만원 이하 성장주 일일 보고서

최근 90일 안에 상장한 코스피·코스닥 보통주 중 종가 1만원 이하 종목을 매일 채점하고, 공모 정보와 사업 내용을 조사해 보고서를 만듭니다. 절차 전체는 [`kr-ipo-daily-report`](.claude/skills/kr-ipo-daily-report/SKILL.md) 스킬에 있습니다.

```bash
python ipo_daily_report.py collect        # 수집·채점 → data/candidates/{날짜}.json
python ipo_daily_report.py pending-meta   # 공모가 등 미확인 종목
python ipo_daily_report.py render         # → reports/{날짜}.md, reports/{날짜}.html, reports/latest.html
```

- 데이터: 네이버 증권(전 종목 목록, 상장일=차트 첫 거래일, 일봉, 재무, 수급). KRX 로그인 불필요.
- 공모가·수요예측 경쟁률·의무보유확약: `data/ipo_meta.json` (웹 검색으로 채우고 출처 URL 기록).
- 채점(100점): 성장성 30 · 수익성 15 · 상장후 흐름 20 · 수급 15 · 공모 품질 10 · 밸류 10, 보호예수 해제 임박·과도한 변동성·고부채 감점. 65점 이상 관심, 50점 이상 관찰.
- 제외: 스팩, 우선주, 리츠, 거래정지.
- 함께 설치한 스킬(tradermonty/claude-trading-skills, MIT): `canslim-screener`(채점 방법론), `us-stock-analysis`(보고서 구성), `data-quality-checker`(검수). 세 스킬의 스크립트는 미국 시장용(FMP API)이라 직접 실행하지 않고 방법론만 씁니다.
- 투자 권유가 아닙니다.
