# trader-skill 11종 활용 안내 (한국 신규상장주 기준)

출처: github.com/Jakeschincariol/trader-skill (MIT, commit 5189e79). `.claude/skills/trader-*` 에 설치.
원래 미국 시장(SEC·NYSE·Alpaca)용이라, 한국 종목에 쓸 수 있는 것과 없는 것을 나눠 둔다.

## 바로 쓰는 것

| 스킬 | 한국 종목에서 하는 일 | 실행 |
|---|---|---|
| trader-risk | 10종목 매수 제안이 내 규칙(종목당 비중, 현금 버퍼, 1회 주문 한도, 금지 종목, 하루 주문 수)을 넘는지 PASS/BLOCK | `python ipo_daily_report.py trader-check --budget 10000000` |
| trader-screener | 내 규칙으로 후보를 거르고 순위 매기기 (매출 성장률, 영업이익률, PER, PSR, 공모가 대비, 수요예측 경쟁률, 확약 비율, 점수) | `python .claude/skills/trader-screener/screen.py trader/metrics.json --rules config/trader-rules.kr-ipo.json` |
| trader-budget | 비중(%)을 정해 두고 예산을 나누는 계산. 원화도 숫자라 그대로 계산된다(화면에 "$" 로 찍히지만 값은 원). 주 단위는 `--whole-shares --prices trader/prices.json` | `python .claude/skills/trader-budget/size.py --weights 179880=30,0013Y0=30,420610=40 --budget 10000000 --whole-shares --prices trader/prices.json` |
| trader-rebalance | `trader/portfolio.json` 에 실제 보유를 적어 두면 목표 비중에서 벗어난 종목을 계산 | `python .claude/skills/trader-rebalance/rebalance.py` |

`trader-check` 는 보고서의 10종목을 `trader/config.json`·`prices.json`·`orders.json`·`metrics.json` 으로 내보낸 뒤
trader-risk 를 돌린다. `trader/config.json` 은 처음 한 번 만들어지고, 그 뒤로는 사용자가 고친 규칙을 유지한다
(`--budget` 을 주면 예산만 바꾼다). trader-risk 는 `"market": "KRX"`, `"currency": "KRW"` 일 때 6자리 종목코드와
원 단위를 받도록 로컬 패치했다. 필드 이름의 `_usd` 는 호환용 이름일 뿐 값은 원이다.

## 방법만 빌려 쓰는 것

| 스킬 | 이유 | 대신 |
|---|---|---|
| trader-news | Yahoo 영문 헤드라인 + 미국 SEC 8-K 전용 | WebSearch 로 "{종목명} 공시", "{종목명} 주가" 조사. 규칙은 그대로: 헤드라인은 단서일 뿐, 원문·공시를 읽고 출처를 단다. 공시가 기사보다 우선. 예측 금지 |
| trader-alerts | Yahoo 시세(이 환경에서 차단) + NYSE 개장 시간 | 일일 보고서가 매일 손절가·목표가와 현재가를 비교한다. 규칙: 알림은 정보일 뿐, 자동 매도 없음 |
| trader-analyst | SEC XBRL 재무 전용 | 네이버 재무(연간·분기) → `data/candidates/*.json` 의 `fund`. 규칙: 같은 분기끼리 비교, 일회성 이익 표시, 없는 숫자는 만들지 않음 |
| trader-broker | Alpaca·IBKR 미국 브로커용. 이 환경에 국내 증권사 연결 없음 | 주문은 사용자가 증권사 앱에서 직접. 원칙은 그대로: 주문마다 명시적 "예", 예약 작업에서 주문 금지, 키를 채팅에 쓰지 않음 |

## 한국 종목에 해당 없음

- trader-copycat (미국 13F 공시), trader-pelosi (미국 의회 거래 공시): 미국 주식 참고용으로만 사용.
- trader-tax: 미국 세법(단기/장기, wash sale). 한국 상장주식 매매는 증권거래세 등 체계가 달라 쓰지 않는다.
  미국 주식을 산다면 해외주식 양도세는 국내 세법이 적용되므로 결과를 그대로 믿지 말 것.

## 공통 안전 원칙 (모든 trader 스킬)

1. 어떤 스킬도 스스로 주문하지 않는다. 제안 → trader-risk PASS → 사용자의 명시적 "예" → 사용자가 직접 주문.
2. 모의투자(paper) 기본. 실계좌 전환은 사용자가 직접.
3. 옵션·신용(마진)·공매도는 꺼 둔다.
4. API 키·비밀번호는 채팅에 쓰지 않는다.
5. 수익 예측·매수 권유 표현을 쓰지 않는다.
