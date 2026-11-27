---
name: kr-ipo-daily-report
description: 최근 상장한 코스피·코스닥 종목 중 1만원 이하 종목을 찾아 성장 가능성을 채점하고, 공모 정보·사업 내용을 웹에서 조사해 일일 보고서(Markdown + HTML Artifact)를 만든다. "신규상장 보고서", "공모주 일일 리포트", "새로 상장한 저가주 분석" 요청이나 매일 예약 실행에서 사용한다.
---

# 신규상장 저가 성장주 일일 보고서

정량 채점은 스크립트가, 공모 정보 확인과 정성 판단은 Claude 가 맡는다.
채점 기준은 `canslim-screener` 의 CANSLIM 을 상장 초기 한국 종목에 맞게 바꾼 것이고
(`ipo_report/score.py` 머리말 참고), 보고서 구성은 `us-stock-analysis` 의 report-template 을,
검수는 `data-quality-checker` 를 따른다.

## 필요 환경

- 네트워크 허용 호스트: `m.stock.naver.com`, `fchart.stock.naver.com` (필수). KIND·KRX 는 해외 IP 차단/로그인 필요로 쓰지 않는다.
- 공모가·수요예측·확약은 자동 소스가 없다 (38커뮤니케이션은 약한 TLS 로 접속 불가). WebSearch 로 조사한다.
- Python: `pandas`, `requests` (`pip install -r requirements.txt`).

## 절차

1. **수집·채점**
   ```bash
   python ipo_daily_report.py collect
   ```
   출력의 `기준일` 이 `data/candidates/` 에 이미 있는 마지막 날짜와 같고 오늘(KST)과 다르면 휴장일이다.
   이 경우 보고서를 새로 만들지 말고 "휴장" 이라고만 보고하고 끝낸다.

2. **공모 정보 채우기** — `python ipo_daily_report.py pending-meta` 로 나온 종목(공모가 미확인)마다
   WebSearch `"{종목명} 공모가 수요예측 경쟁률 의무보유확약"` 으로 조사해 `data/ipo_meta.json` 에 추가한다.
   필드: `name, ipo_price, band, demand_competition, lockup_commit_pct(신청수량 기준 %), retail_competition,
   float_pct_at_listing, underwriter, business, sources[], checked(YYYY-MM-DD)`.
   찾지 못한 값은 `null` 로 두고 추측하지 않는다. 기존 항목은 고치지 않는다(오류를 발견했을 때만).
   새 정보가 생겼으면 `collect` 를 한 번 더 실행해 점수에 반영한다.

3. **정성 분석** — 점수 상위 10개(관심·관찰 등급은 전부)에 대해:
   - 사업 내용 한 줄, 성장 근거, 리스크, 다가오는 일정(보호예수 해제·실적 발표)을 조사한다.
     WebSearch `"{종목명} 사업 매출"`, `"{종목명} 주가 {이번 달}"` 정도면 충분하다.
   - 숫자는 스크립트 결과(`data/candidates/{기준일}.json`)를 우선하고, 기사 수치는 출처 URL 을 붙인다.
   - 점수 등급을 그대로 쓰되, 근거가 분명하면 `verdict` 로 한 단계까지 조정할 수 있다(이유를 risks/thesis 에 적는다).
   - 결과를 `reports/notes/{기준일}.json` 으로 저장한다. 형식은 `ipo_report/render.py` 머리말과
     `reports/notes/20261127.json` 예시를 따른다. `summary` 는 2~3문장 결론, `changes` 는 전일 대비 달라진 점.

4. **렌더링·검수**
   ```bash
   python ipo_daily_report.py render
   python .claude/skills/data-quality-checker/scripts/check_data_quality.py --file reports/{기준일}.md --as-of {YYYY-MM-DD} --output-dir /tmp/dq
   python -m pytest tests -q
   ```
   검수 경고는 원인을 확인해 노트나 데이터를 고친 뒤 다시 렌더링한다.

5. **게시** — Artifact 도구로 기존 보고서 페이지를 갱신한다: 먼저 `action: "read"` 로
   `https://claude.ai/artifact/5cFRCd5LjM9LtfWtAEKgR2` 를 읽고, 같은 `url` 로 `reports/latest.html` 을 publish 한다.
   새 URL 을 만들지 않는다.

6. **커밋** — `data/`, `reports/` 변경을 커밋해 푸시한다(캐시·전일 순위 비교에 다음 날 필요하다).

7. **보고** — 마지막 응답은 짧게: 기준일, 후보 수, 관심·관찰 종목 이름과 점수 한 줄씩, 새로 편입된 종목, 보고서 링크.

## 주의

- 투자 권유가 아니다. "매수 추천" 대신 "관심/관찰/보류" 와 근거·리스크를 쓴다.
- 네이버 API 는 비공식이다. 응답 구조가 바뀌어 수집이 실패하면 보고서를 만들지 말고 실패 내용을 보고한다.
- 장중(15:40 이전) 실행이면 보고서 제목이 "장중 HH:MM 기준" 으로 표시된다. 예약 실행은 장 마감 후로 잡는다.
