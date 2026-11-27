"""data/candidates/{날짜}.json + reports/notes/{날짜}.json (+ data/baserate.json)
→ reports/{날짜}.md, reports/{날짜}.html, reports/latest.html

보고서는 '오늘의 10종목' 매매표가 중심이다: 종목별 사는 주수(총자금 입력값 기준), 손절가, 1개월 목표가,
목표·손절 시 손익. 주수는 position-sizer 스킬의 고정비율 방식(plan.shares)으로 계산한다.

notes JSON (매일 Claude 가 조사해서 작성, 없으면 정량 결과만 출력):
{
  "summary": "오늘의 결론 2~3문장",
  "market_comment": "시장 환경 한두 문장",
  "picks": {
    "179880": {
      "verdict": "관심|관찰|보류",      # 생략하면 점수 등급
      "business": "무슨 회사인지 한 줄",
      "why": "이 종목을 고른 이유 한 줄",
      "thesis": ["좋은 점", ...],        # 2~3개
      "risks": ["주의할 점", ...],       # 2~3개
      "catalysts": ["다가오는 일정", ...],
      "sources": ["https://...", ...]
    }
  },
  "changes": "전일 대비 달라진 점"
}
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from .plan import RISK_BY_GRADE, shares

PART_LABELS = [
    ("G", "성장성", 30),
    ("P", "수익성", 15),
    ("N", "상장후 흐름", 20),
    ("S", "수급", 15),
    ("I", "공모 품질", 10),
    ("V", "밸류", 10),
]
WEEKDAY = "월화수목금토일"
TOP_N = 10
DEFAULT_BUDGET = 10_000_000


def _date_ko(s: str) -> str:
    d = datetime.strptime(s, "%Y%m%d")
    return f"{d.year}년 {d.month}월 {d.day}일({WEEKDAY[d.weekday()]})"


def _iso(s: str) -> str:
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def _pct(x, digits=0) -> str:
    return "–" if x is None else f"{x:+.{digits}%}"


def _won(x) -> str:
    return "–" if x is None else f"{x:,.0f}원"


def _man(x: float) -> str:
    """원 → 만원 표기 (부호 포함)"""
    sign = "−" if x < 0 else "+"
    return f"{sign}{abs(x) / 1e4:,.0f}만원"


def _eok(x) -> str:
    """원 → 억원 표기"""
    if x is None:
        return "–"
    e = x / 1e8
    return f"{e / 1e4:,.2f}조원" if e >= 1e4 else f"{e:,.0f}억원"


def basis_label(data: dict) -> str:
    """장 마감(15:30) + 종가 확정 여유 전이면 '장중 HH:MM 기준'."""
    g = datetime.fromisoformat(data["generated_at"])
    if g.strftime("%Y%m%d") == data["as_of"] and (g.hour, g.minute) < (15, 40):
        return f"장중 {g:%H:%M} 기준"
    return "장 마감 기준"


def previous_ranks(cand_dir: Path, as_of: str) -> dict[str, int] | None:
    prev = sorted(p for p in cand_dir.glob("*.json") if p.stem < as_of)
    if not prev:
        return None
    d = json.loads(prev[-1].read_text(encoding="utf-8"))
    return {c["code"]: i + 1 for i, c in enumerate(d["candidates"])}


def load(root: Path, as_of: str | None = None) -> tuple[dict, dict, dict[str, int] | None]:
    cand_dir = root / "data" / "candidates"
    path = cand_dir / f"{as_of}.json" if as_of else sorted(cand_dir.glob("*.json"))[-1]
    data = json.loads(path.read_text(encoding="utf-8"))
    notes_path = root / "reports" / "notes" / f"{data['as_of']}.json"
    notes = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.exists() else {}
    br = root / "data" / "baserate.json"
    data["baserate"] = json.loads(br.read_text(encoding="utf-8")) if br.exists() else None
    return data, notes, previous_ranks(cand_dir, data["as_of"])


def verdict_of(c: dict, notes: dict) -> str:
    return (notes.get("picks", {}).get(c["code"], {}) or {}).get("verdict") or c["grade"]


def rank_change(code: str, rank: int, prev: dict[str, int] | None) -> str:
    if prev is None:
        return ""
    if code not in prev:
        return "신규"
    d = prev[code] - rank
    return "–" if d == 0 else (f"▲{d}" if d > 0 else f"▼{-d}")


def top_picks(data: dict, n: int = TOP_N) -> list[dict]:
    return [c for c in data["candidates"] if c.get("plan")][:n]


def why_line(c: dict, note: dict) -> str:
    if note.get("why"):
        return note["why"]
    return " · ".join(c["score"]["notes"][:3])


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def to_markdown(data: dict, notes: dict, prev, budget: float = DEFAULT_BUDGET) -> str:
    a = data["as_of"]
    p = data["params"]
    br = data.get("baserate")
    picks = top_picks(data)
    L = [f"# 신규상장 1만원 이하 10종목 — {_iso(a)} ({WEEKDAY[datetime.strptime(a, '%Y%m%d').weekday()]}) {basis_label(data)}", ""]
    if notes.get("summary"):
        L += [notes["summary"], ""]
    if br:
        b = br["plan"]
        L += [
            "## 한 달 보유하면 어떻게 됐나 (과거 기록)",
            "",
            f"- {br['period']}에 상장한 1만원 이하 종목에 같은 손절·목표가 규칙을 적용한 {b['n']:,}번의 매매 중 "
            f"이익으로 끝난 비율 **{b['win_rate']:.0%}**, 중간 결과 **{b['median']:+.0%}**, 평균 {b['mean']:+.1%}.",
            f"- 목표가 도달 {b['exit_target']:.0%} · 손절 {b['exit_stop']:.0%} · 한 달 경과 {b['exit_time']:.0%}.",
            f"- 손절을 지켰을 때 하위 5% 손실 {b['p05']:+.0%} (그냥 들고 있었으면 {br['hold_20d']['p05']:+.0%}).",
            "- 결론: 한 달 안에 이익이 날 것이라고 기대할 근거는 약합니다. 주수는 '얼마나 벌까'가 아니라 '손절 시 얼마까지 잃을까'로 정했습니다.",
            "",
        ]
    L += [f"## 오늘의 10종목 (총자금 {budget / 1e4:,.0f}만원 기준)", "",
          "| # | 종목 | 등급 | 현재가 | 주수 | 매수금액 | 손절가 | 1개월 목표가 | 목표 시 | 손절 시 |",
          "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    tot = {"amt": 0.0, "gain": 0.0, "loss": 0.0}
    for i, c in enumerate(picks, 1):
        pl, v = c["plan"], verdict_of(c, notes)
        s = shares(budget, pl, v, c["price"]["tv_5d"])
        tot["amt"] += s["amount"]
        tot["gain"] += s["gain_at_target"]
        tot["loss"] += s["loss_at_stop"]
        L.append(f"| {i} | {c['name']} | {v} | {pl['entry']:,.0f} | {s['shares']:,} | {s['amount'] / 1e4:,.0f}만 | "
                 f"{pl['stop']:,.0f} ({pl['stop_pct']:+.0%}) | {pl['target']:,.0f} ({pl['target_pct']:+.0%}) | "
                 f"{_man(s['gain_at_target'])} | {_man(s['loss_at_stop'])} |")
    L += ["", f"합계 매수 {tot['amt'] / 1e4:,.0f}만원 (현금 {(budget - tot['amt']) / 1e4:,.0f}만원), "
          f"전부 목표 도달 시 {_man(tot['gain'])}, 전부 손절 시 {_man(tot['loss'])}.", ""]
    L += ["## 종목별 메모", ""]
    for i, c in enumerate(picks, 1):
        n = notes.get("picks", {}).get(c["code"], {})
        biz = n.get("business") or c["meta"].get("business") or ""
        L.append(f"### {i}. {c['name']} ({c['code']}) — {verdict_of(c, notes)} {c['score']['total']:.0f}점")
        if biz:
            L.append(f"{biz}")
        L.append("")
        L.append(f"- 왜: {why_line(c, n)}")
        for key, lab in (("thesis", "좋은 점"), ("risks", "주의할 점"), ("catalysts", "일정")):
            if n.get(key):
                L.append(f"- {lab}: " + " / ".join(n[key]))
        if not n.get("risks") and c["score"]["flags"]:
            L.append("- 주의할 점: " + " / ".join(c["score"]["flags"]))
        srcs = list(dict.fromkeys((n.get("sources") or []) + (c["meta"].get("sources") or [])))
        if srcs:
            L.append("- 출처: " + " ".join(f"<{u}>" for u in srcs))
        L.append("")
    rest = [c for c in data["candidates"] if c not in picks]
    if rest:
        L += ["## 나머지 후보", "", ", ".join(f"{c['name']} {c['score']['total']:.0f}점" for c in rest), ""]
    L += [
        "## 기준",
        "",
        f"- 대상: 최근 {p['days']}일 상장, 코스피·코스닥 보통주, 종가 {p['max_price']:,.0f}원 이하 (스팩·우선주·리츠 제외).",
        "- 점수(100점): 성장성 30, 수익성 15, 상장후 흐름 20, 수급 15, 공모 품질 10, 밸류 10, 감점. 65점↑ 관심, 50점↑ 관찰.",
        "- 손절가 = 현재가 − 2×ATR (8~20%). 목표가 = 현재가보다 8% 이상 위의 가장 가까운 저항(20일 평균가·20일 고점·공모가).",
        "- 주수 = min(총자금×위험비율 ÷ 1주당 손절폭, 총자금×종목당 상한 ÷ 현재가, 5일 평균 거래대금의 2%). "
        "위험비율·상한: 관심 1%·15%, 관찰 0.75%·10%, 보류 0.5%·5%.",
        "- 시세·재무: 네이버 증권. 공모 정보: 기사·IPO 정보 사이트(출처 표기).",
        "- 투자 권유가 아니며, 매수·매도 판단과 책임은 투자자 본인에게 있습니다.",
        "",
    ]
    return "\n".join(L)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
CSS = """
/* 레이아웃: 한 기둥(64rem). 결론 → 과거 기록 3수치 → 총자금 입력 → 10종목 매매표(행=종목) → 나머지·기준 */
:root{
  --paper:#f5f6f8; --panel:#ffffff; --ink:#16202a; --sub:#5a6774; --line:#dfe3e8; --soft:#eef1f4;
  --accent:#1d5d8a; --up:#cf2e35; --down:#1f5fc4;
  --g-watch:#0d7350; --g-obs:#9a5b00; --g-hold:#66717c;
  --chip-watch:#e1f3ea; --chip-obs:#fbefd9; --chip-hold:#eceff2;
  --f-body:"IBM Plex Sans KR","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;
  --f-num:"IBM Plex Mono",ui-monospace,"SFMono-Regular",Menlo,monospace;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --paper:#0f151b; --panel:#17212b; --ink:#e6ebf0; --sub:#97a5b2; --line:#2a3642; --soft:#1d2833;
  --accent:#72b2de; --up:#ff6b70; --down:#6ca4ff;
  --g-watch:#52d29f; --g-obs:#f1b252; --g-hold:#97a5b2;
  --chip-watch:#123526; --chip-obs:#3a2a0f; --chip-hold:#222d37; color-scheme:dark}}
:root[data-theme="dark"]{
  --paper:#0f151b; --panel:#17212b; --ink:#e6ebf0; --sub:#97a5b2; --line:#2a3642; --soft:#1d2833;
  --accent:#72b2de; --up:#ff6b70; --down:#6ca4ff;
  --g-watch:#52d29f; --g-obs:#f1b252; --g-hold:#97a5b2;
  --chip-watch:#123526; --chip-obs:#3a2a0f; --chip-hold:#222d37; color-scheme:dark}
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink);font-family:var(--f-body);font-size:16px;line-height:1.65;
  word-break:keep-all;overflow-wrap:break-word;
  padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:28px 64px}
.wrap{max-width:64rem;margin:0 auto;display:flex;flex-direction:column;gap:32px}
h1,h2,h3{text-wrap:balance;margin:0;line-height:1.3}
h1{font-size:1.55rem;font-weight:700}
h2{font-size:1.15rem;font-weight:700}
h3{font-size:1.1rem;font-weight:700}
a{color:var(--accent)}
a:focus-visible,button:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.num{font-family:var(--f-num);font-variant-numeric:tabular-nums}
.up{color:var(--up)} .down{color:var(--down)}
.muted{color:var(--sub);font-size:.9rem}
section{display:flex;flex-direction:column;gap:14px}
header{display:flex;flex-direction:column;gap:8px}
.eyebrow{font-size:.8rem;letter-spacing:.06em;color:var(--sub);font-weight:600}
.lead{font-size:1.12rem;max-width:62ch;margin:0}
.market{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:.9rem;color:var(--sub)}
.market b{color:var(--ink);font-weight:600}
.facts3{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,13rem),1fr));gap:12px}
.fact{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;display:flex;flex-direction:column;gap:4px}
.fact .big{font-size:2rem;font-weight:600;line-height:1.1}
.fact .lab{font-size:.88rem;color:var(--sub)}
.note{background:var(--soft);border-radius:10px;padding:14px 16px;max-width:none;margin:0}
.budget{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;display:flex;flex-direction:column;gap:12px}
.budget-row{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.budget label{font-weight:600}
.budget input{font:inherit;font-family:var(--f-num);font-size:1.1rem;width:11rem;max-width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--paper);color:var(--ink);text-align:right}
.quick{display:flex;flex-wrap:wrap;gap:6px}
.quick button{font:inherit;font-size:.88rem;padding:6px 12px;border-radius:999px;border:1px solid var(--line);background:var(--paper);color:var(--ink);cursor:pointer}
.quick button[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:var(--panel)}
.totals{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,11rem),1fr));gap:8px 16px}
.totals div{display:flex;flex-direction:column}
.totals dt{font-size:.8rem;color:var(--sub)} .totals dd{margin:0;font-size:1.15rem}
.picks{display:flex;flex-direction:column;gap:12px;list-style:none;margin:0;padding:0}
.pick{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;display:flex;flex-direction:column;gap:12px;min-width:0}
.pick-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.rank{font-family:var(--f-num);color:var(--sub);font-size:1rem;min-width:1.6rem}
.pick-head h3 a{color:var(--ink);text-decoration:none}
.pick-head h3 a:hover{text-decoration:underline}
.code{color:var(--sub);font-size:.85rem}
.score{margin-left:auto;font-size:.9rem;color:var(--sub)}
.score b{font-family:var(--f-num);color:var(--ink);font-size:1.05rem}
.chip{display:inline-block;font-size:.78rem;font-weight:700;border-radius:999px;padding:1px 10px}
.chip.관심{background:var(--chip-watch);color:var(--g-watch)}
.chip.관찰{background:var(--chip-obs);color:var(--g-obs)}
.chip.보류{background:var(--chip-hold);color:var(--g-hold)}
.why{margin:0;max-width:70ch}
.biz{color:var(--sub);font-size:.92rem}
.ticket{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:0;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.ticket div{padding:10px 12px;display:flex;flex-direction:column;gap:2px;border-right:1px solid var(--line);min-width:0}
.ticket div:last-child{border-right:0}
.ticket dt{font-size:.78rem;color:var(--sub)}
.ticket dd{margin:0;font-size:1.05rem}
.ticket .buy{background:var(--soft)}
.ticket .buy dd{font-size:1.25rem;font-weight:600}
.ticket small{font-size:.8rem;color:var(--sub);font-family:var(--f-body)}
@media (max-width:640px){
  .ticket{grid-template-columns:repeat(2,minmax(0,1fr))}
  .ticket div{border-right:0;border-bottom:1px solid var(--line)}
  .ticket .buy{grid-column:1 / -1}
  .ticket div:nth-child(2n+1):not(.buy){border-right:1px solid var(--line)}
  .score{margin-left:0;width:100%}
}
.pros{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,16rem),1fr));gap:10px 20px;font-size:.93rem}
.pros h4{margin:0 0 2px;font-size:.82rem;color:var(--sub);font-weight:600}
.pros ul{margin:0;padding-left:1.1em}
.pros li{margin:2px 0}
.src{font-size:.8rem;color:var(--sub)}
.rest{font-size:.92rem;color:var(--sub);margin:0}
footer{color:var(--sub);font-size:.86rem;display:flex;flex-direction:column;gap:6px;max-width:72ch}
footer p{margin:0}
"""

JS = """
(function(){
  var data = JSON.parse(document.getElementById('plan-data').textContent);
  var input = document.getElementById('budget');
  var KEY = 'ipo-report-budget';
  var fmt = function(n){ return Math.round(n).toLocaleString('ko-KR'); };
  var man = function(n){ var s = n < 0 ? '−' : '+'; return s + fmt(Math.abs(n) / 1e4) + '만원'; };
  function calc(budget){
    var tot = {amt:0, gain:0, loss:0, n:0};
    data.rows.forEach(function(r){
      var rc = data.risk[r.grade] || data.risk['보류'];
      var per = r.entry - r.stop;
      var n = Math.min(per > 0 ? Math.floor(budget * rc[0] / per) : 0, Math.floor(budget * rc[1] / r.entry));
      if (r.tv5) n = Math.min(n, Math.floor(r.tv5 * 0.02 / r.entry));
      n = Math.max(0, n);
      var amt = n * r.entry, gain = n * (r.target - r.entry), loss = n * (r.stop - r.entry);
      tot.amt += amt; tot.gain += gain; tot.loss += loss; if (n > 0) tot.n++;
      var el = document.querySelector('[data-code="' + r.code + '"]');
      if (!el) return;
      el.querySelector('.q-shares').textContent = fmt(n) + '주';
      el.querySelector('.q-amount').textContent = fmt(amt / 1e4) + '만원';
      el.querySelector('.q-gain').textContent = man(gain);
      el.querySelector('.q-loss').textContent = man(loss);
    });
    document.getElementById('t-amt').textContent = fmt(tot.amt / 1e4) + '만원';
    document.getElementById('t-cash').textContent = fmt((budget - tot.amt) / 1e4) + '만원';
    document.getElementById('t-gain').textContent = man(tot.gain);
    document.getElementById('t-loss').textContent = man(tot.loss);
    document.querySelectorAll('.quick button').forEach(function(b){
      b.setAttribute('aria-pressed', String(Number(b.dataset.v) === budget));
    });
  }
  function parse(v){ var n = Number(String(v).replace(/[^0-9]/g, '')); return n > 0 ? n * 1e4 : 0; }
  function set(budget, save){
    input.value = fmt(budget / 1e4);
    calc(budget);
    if (save) { try { localStorage.setItem(KEY, String(budget)); } catch (e) {} }
  }
  input.addEventListener('input', function(){ var b = parse(input.value); if (b) { calc(b); try { localStorage.setItem(KEY, String(b)); } catch (e) {} } });
  input.addEventListener('blur', function(){ var b = parse(input.value) || data.budget; set(b, true); });
  document.querySelectorAll('.quick button').forEach(function(b){
    b.addEventListener('click', function(){ set(Number(b.dataset.v), true); });
  });
  var saved = 0;
  try { saved = Number(localStorage.getItem(KEY)) || 0; } catch (e) {}
  set(saved || data.budget, false);
})();
"""


def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _signed(x, digits=0) -> str:
    if x is None:
        return "<span>–</span>"
    cls = "up" if x > 0 else ("down" if x < 0 else "")
    return f'<span class="num {cls}">{x:+.{digits}%}</span>'


def _host(u: str) -> str:
    try:
        return u.split("/")[2].removeprefix("www.")
    except IndexError:
        return u


def _pick(i: int, c: dict, n: dict, verdict: str, budget: float, prev) -> str:
    pl, pm, meta = c["plan"], c["price"], c["meta"]
    s = shares(budget, pl, verdict, pm["tv_5d"])
    biz = n.get("business") or meta.get("business")
    ch = rank_change(c["code"], i, prev)
    base = f'{pm["base_kind"]} {pm["ipo_price"] or pm["first_close"]:,.0f}원 대비 {_signed(pm["vs_base"])}'

    def lst(key, title, fallback=None):
        items = n.get(key) or fallback or []
        if not items:
            return ""
        return f"<div><h4>{title}</h4><ul>" + "".join(f"<li>{_e(x)}</li>" for x in items[:3]) + "</ul></div>"

    srcs = list(dict.fromkeys((n.get("sources") or []) + (meta.get("sources") or [])))
    src_html = (
        '<div class="src">출처: ' + " · ".join(f'<a href="{_e(u)}" target="_blank" rel="noopener">{_e(_host(u))}</a>' for u in srcs[:4]) + "</div>"
        if srcs else ""
    )
    return f"""
<li class="pick" data-code="{_e(c['code'])}">
  <div class="pick-head">
    <span class="rank">{i}</span>
    <h3><a href="{_e(c['url'])}" target="_blank" rel="noopener">{_e(c['name'])}</a></h3>
    <span class="chip {_e(verdict)}">{_e(verdict)}</span>
    <span class="code num">{_e(c['code'])}{' · ' + _e(ch) if ch else ''}</span>
    <span class="score">점수 <b>{c['score']['total']:.0f}</b>/100</span>
  </div>
  {f'<div class="biz">{_e(biz)}</div>' if biz else ''}
  <p class="why">{_e(why_line(c, n))}</p>
  <dl class="ticket">
    <div class="buy"><dt>사는 주수</dt><dd><span class="q-shares num">{s['shares']:,}주</span> <small class="q-amount">{s['amount'] / 1e4:,.0f}만원</small></dd></div>
    <div><dt>현재가</dt><dd class="num">{pl['entry']:,.0f}</dd><small>{base}</small></div>
    <div><dt>손절가</dt><dd class="num down">{pl['stop']:,.0f}</dd><small>{pl['stop_pct']:+.0%} · 손실 <span class="q-loss num">{_man(s['loss_at_stop'])}</span></small></div>
    <div><dt>1개월 목표가</dt><dd class="num up">{pl['target']:,.0f}</dd><small>{pl['target_pct']:+.0%} · 이익 <span class="q-gain num">{_man(s['gain_at_target'])}</span></small></div>
    <div><dt>목표가 근거</dt><dd style="font-size:.95rem">{_e(pl['target_basis'])}</dd><small>손익비 {pl['rr']:.1f}</small></div>
  </dl>
  <div class="pros">{lst('thesis', '좋은 점')}{lst('risks', '주의할 점', c['score']['flags'])}{lst('catalysts', '일정')}</div>
  {src_html}
</li>"""


def to_html(data: dict, notes: dict, prev, budget: float = DEFAULT_BUDGET) -> str:
    a = data["as_of"]
    p = data["params"]
    br = data.get("baserate")
    picks = top_picks(data)
    mk = " ".join(
        f'<span><b>{_e(k)}</b> <span class="num">{m["close"]:,.2f}</span> 오늘 {_signed(m["chg_1d"], 1)} · 20일 {_signed(m["ret_20d"], 1)}</span>'
        for k, m in data["market"].items()
    )
    facts = ""
    if br:
        b = br["plan"]
        facts = f"""
<section aria-labelledby="h-base">
  <h2 id="h-base">한 달 보유하면 어떻게 됐나</h2>
  <div class="facts3">
    <div class="fact"><span class="big num">{b['win_rate']:.0%}</span><span class="lab">이익으로 끝난 비율<br>(10번 중 {round(b['win_rate'] * 10)}번)</span></div>
    <div class="fact"><span class="big num down">{b['median']:+.0%}</span><span class="lab">중간 결과<br>(평균 {b['mean']:+.1%})</span></div>
    <div class="fact"><span class="big num">{b['p05']:+.0%}</span><span class="lab">손절을 지켰을 때 최악 5%<br>(안 지키면 {br['hold_20d']['p05']:+.0%})</span></div>
  </div>
  <p class="note">{_e(br['period'])}에 상장한 1만원 이하 종목 {b['n']:,}건에 이 보고서와 같은 손절·목표가 규칙을 적용한 결과입니다. 목표가 도달 {b['exit_target']:.0%}, 손절 {b['exit_stop']:.0%}, 한 달 경과 {b['exit_time']:.0%}. 이 그룹은 한 달 안에 이익을 기대할 근거가 약해서, 아래 주수는 <b>손절 시 잃는 금액을 총자금의 1% 이내</b>로 묶는 기준으로 정했습니다. 상장폐지 종목은 빠져 있어 실제 결과는 이보다 나빴을 수 있습니다.</p>
</section>"""
    rows = []
    payload = {"budget": budget, "risk": {k: list(v) for k, v in RISK_BY_GRADE.items()}, "rows": []}
    for i, c in enumerate(picks, 1):
        v = verdict_of(c, notes)
        rows.append(_pick(i, c, notes.get("picks", {}).get(c["code"], {}), v, budget, prev))
        pl = c["plan"]
        payload["rows"].append({"code": c["code"], "grade": v, "entry": pl["entry"], "stop": pl["stop"],
                                "target": pl["target"], "tv5": c["price"]["tv_5d"]})
    t = {"amt": 0.0, "gain": 0.0, "loss": 0.0}
    for c in picks:
        s = shares(budget, c["plan"], verdict_of(c, notes), c["price"]["tv_5d"])
        t["amt"] += s["amount"]
        t["gain"] += s["gain_at_target"]
        t["loss"] += s["loss_at_stop"]
    rest = [c for c in data["candidates"] if c not in picks]
    rest_html = ", ".join(f'{_e(c["name"])} <span class="num">{c["score"]["total"]:.0f}</span>점' for c in rest) or "없음"
    quick = "".join(f'<button type="button" data-v="{v}">{v // 10_000:,}만원</button>' for v in (3_000_000, 5_000_000, 10_000_000, 30_000_000))
    summary = notes.get("summary") or f"최근 {p['days']}일 신규상장 1만원 이하 {len(data['candidates'])}종목 중 점수 상위 10종목입니다."
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f"""<title>신규상장 저가주 리포트</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans+KR:wght@400;600;700&display=swap">
<style>{CSS}</style>
<div class="wrap">
<header>
  <div class="eyebrow">신규상장 · 1만원 이하 · 오늘의 10종목</div>
  <h1>{_date_ko(a)} {basis_label(data)}</h1>
  <p class="lead">{_e(summary)}</p>
  <div class="market">{mk}</div>
  {f'<p class="muted" style="margin:0">{_e(notes["market_comment"])}</p>' if notes.get("market_comment") else ''}
</header>
{facts}
<section aria-labelledby="h-budget">
  <h2 id="h-budget">내 투자금으로 계산하기</h2>
  <div class="budget">
    <div class="budget-row">
      <label for="budget">총 투자금</label>
      <input id="budget" inputmode="numeric" autocomplete="off" value="{budget / 1e4:,.0f}" aria-describedby="budget-unit"><span id="budget-unit">만원</span>
      <div class="quick">{quick}</div>
    </div>
    <dl class="totals">
      <div><dt>10종목 매수 합계</dt><dd class="num" id="t-amt">{t['amt'] / 1e4:,.0f}만원</dd></div>
      <div><dt>현금으로 남는 돈</dt><dd class="num" id="t-cash">{(budget - t['amt']) / 1e4:,.0f}만원</dd></div>
      <div><dt>모두 목표가 도달 시</dt><dd class="num up" id="t-gain">{_man(t['gain'])}</dd></div>
      <div><dt>모두 손절 시</dt><dd class="num down" id="t-loss">{_man(t['loss'])}</dd></div>
    </dl>
    <div class="muted">종목당 손절 손실이 총투자금의 1%(관심)·0.75%(관찰)·0.5%(보류)를 넘지 않게, 한 종목 비중은 15%·10%·5% 이하로 맞췄습니다. 거래가 적은 종목은 하루 거래대금의 2%까지만 담습니다.</div>
  </div>
</section>
<section aria-labelledby="h-picks">
  <h2 id="h-picks">오늘의 10종목</h2>
  <ol class="picks">{''.join(rows)}</ol>
</section>
<section aria-labelledby="h-rest">
  <h2 id="h-rest">나머지 후보</h2>
  <p class="rest">{rest_html}</p>
  {f'<p class="rest">{_e(notes["changes"])}</p>' if notes.get("changes") else ''}
</section>
<footer>
  <p>대상: 최근 {p['days']}일 안에 상장한 코스피·코스닥 보통주 중 종가 {p['max_price']:,.0f}원 이하 (스팩·우선주·리츠 제외). 점수 100점 = 성장성 30 + 수익성 15 + 상장후 흐름 20 + 수급 15 + 공모 품질 10 + 밸류 10 − 감점. 65점 이상 관심, 50점 이상 관찰.</p>
  <p>손절가 = 현재가 − 2×ATR(8~20% 범위). 1개월 목표가 = 현재가보다 8% 이상 위에 있는 가장 가까운 가격대(20일 평균가, 최근 20일 고점, 공모가). 주수 계산은 고정비율 방식입니다.</p>
  <p>시세·재무는 네이버 증권, 공모가·수요예측·확약은 기사와 IPO 정보 사이트입니다. 생성 {_e(data['generated_at'])}. 이 보고서는 투자 권유가 아니며, 매수·매도 판단과 책임은 투자자 본인에게 있습니다.</p>
</footer>
</div>
<script type="application/json" id="plan-data">{payload_json}</script>
<script>{JS}</script>
"""


def run(root: Path, as_of: str | None = None) -> tuple[Path, Path]:
    data, notes, prev = load(root, as_of)
    out = root / "reports"
    out.mkdir(exist_ok=True)
    md = out / f"{data['as_of']}.md"
    ht = out / f"{data['as_of']}.html"
    md.write_text(to_markdown(data, notes, prev), encoding="utf-8")
    page = to_html(data, notes, prev)
    ht.write_text(page, encoding="utf-8")
    (out / "latest.html").write_text(page, encoding="utf-8")
    return md, ht
