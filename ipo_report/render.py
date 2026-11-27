"""data/candidates/{날짜}.json + reports/notes/{날짜}.json → reports/{날짜}.md, reports/{날짜}.html

notes JSON (매일 Claude 가 조사해서 작성, 없으면 정량 결과만 출력):
{
  "summary": "오늘의 결론 2~3문장",
  "market_comment": "시장 환경 한 문단",
  "picks": {
    "179880": {
      "verdict": "관심|관찰|보류",      # 생략하면 점수 등급
      "business": "무슨 회사인지 한 줄",
      "thesis": ["성장 근거", ...],
      "risks": ["위험 요인", ...],
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

PART_LABELS = [
    ("G", "성장성", 30),
    ("P", "수익성", 15),
    ("N", "상장후 흐름", 20),
    ("S", "수급", 15),
    ("I", "공모 품질", 10),
    ("V", "밸류", 10),
]
WEEKDAY = "월화수목금토일"


def _date_ko(s: str) -> str:
    d = datetime.strptime(s, "%Y%m%d")
    return f"{d.year}년 {d.month}월 {d.day}일({WEEKDAY[d.weekday()]})"


def _iso(s: str) -> str:
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def _pct(x, digits=0) -> str:
    return "–" if x is None else f"{x:+.{digits}%}"


def _won(x) -> str:
    return "–" if x is None else f"{x:,.0f}원"


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


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def to_markdown(data: dict, notes: dict, prev) -> str:
    a = data["as_of"]
    p = data["params"]
    L = [f"# 신규상장 저가 성장주 일일 보고서 — {_iso(a)} ({WEEKDAY[datetime.strptime(a, '%Y%m%d').weekday()]}) {basis_label(data)}", ""]
    L.append(f"대상: 최근 {p['days']}일 안에 상장한 코스피·코스닥 보통주 중 종가 {p['max_price']:,.0f}원 이하 "
             f"(스팩·우선주·리츠 제외). 후보 {len(data['candidates'])}종목.")
    L.append("")
    if notes.get("summary"):
        L += ["## 오늘의 결론", "", notes["summary"], ""]
    L += ["## 시장 환경", ""]
    for k, m in data["market"].items():
        L.append(f"- {k} {m['close']:,.2f} (전일 {_pct(m['chg_1d'], 1)}, 20일 {_pct(m['ret_20d'], 1)}, "
                 f"20일선 {'위' if m['above_ma20'] else '아래'})")
    if notes.get("market_comment"):
        L += ["", notes["market_comment"]]
    L.append("")
    picks = [c for c in data["candidates"] if verdict_of(c, notes) in ("관심", "관찰")]
    L += ["## 주목 종목", ""]
    if not picks:
        L += ["오늘은 관심·관찰 등급 종목이 없습니다.", ""]
    for c in picks:
        n = notes.get("picks", {}).get(c["code"], {})
        pm, sc = c["price"], c["score"]
        L.append(f"### {c['name']} ({c['code']}, {c['market']}) — {verdict_of(c, notes)} · {sc['total']:.0f}점")
        L.append("")
        if n.get("business") or c["meta"].get("business"):
            L.append(f"*{n.get('business') or c['meta'].get('business')}*")
            L.append("")
        L.append(f"- 상장일 {_iso(c['listed'])} · 종가 {_won(pm['close'])} · 시가총액 {_eok(c.get('mcap'))}")
        L.append(f"- {pm['base_kind']} {_won(pm['ipo_price'] or pm['first_close'])} 대비 {_pct(pm['vs_base'])}, "
                 f"상장후 고점 대비 {_pct(pm['from_high'])}")
        L.append("- 점수: " + ", ".join(f"{lab} {sc['parts'][k]:.0f}/{mx}" for k, lab, mx in PART_LABELS)
                 + f", 감점 {sc['parts']['penalty']:.0f}")
        L.append("- 근거: " + "; ".join(sc["notes"]))
        if sc["flags"]:
            L.append("- 주의: " + "; ".join(sc["flags"]))
        for key, lab in (("thesis", "성장 포인트"), ("risks", "리스크"), ("catalysts", "일정·촉매")):
            if n.get(key):
                L.append(f"- {lab}:")
                L += [f"  - {x}" for x in n[key]]
        srcs = (n.get("sources") or []) + (c["meta"].get("sources") or [])
        if srcs:
            L.append("- 출처: " + " ".join(f"<{u}>" for u in dict.fromkeys(srcs)))
        L.append("")
    L += ["## 전체 후보", "", "| 순위 | 변동 | 종목 | 상장일 | 종가 | 공모가 대비 | 점수 | 등급 |", "|---|---|---|---|---:|---:|---:|---|"]
    for i, c in enumerate(data["candidates"], 1):
        pm = c["price"]
        vs = _pct(pm["vs_base"]) if pm["ipo_price"] else f"({_pct(pm['vs_base'])} 상장일종가)"
        L.append(f"| {i} | {rank_change(c['code'], i, prev)} | {c['name']} ({c['code']}) | {_iso(c['listed'])} | "
                 f"{pm['close']:,.0f} | {vs} | {c['score']['total']:.0f} | {verdict_of(c, notes)} |")
    L.append("")
    if data["above_price"]:
        L += ["## 가격 기준 초과 (참고)", "", ", ".join(f"{r['name']} {r['close']:,.0f}원" for r in data["above_price"]), ""]
    if notes.get("changes"):
        L += ["## 전일 대비", "", notes["changes"], ""]
    L += [
        "## 방법과 한계",
        "",
        "- 점수(100점): 성장성 30, 수익성 15, 상장후 흐름 20, 수급 15, 공모 품질 10, 밸류 10, 보호예수 해제·변동성·부채 감점. "
        "CANSLIM 을 상장 초기 종목에 맞게 바꾼 것입니다. 65점 이상 관심, 50점 이상 관찰.",
        "- 시세·재무: 네이버 증권. 공모가·수요예측·확약: 언론·IPO 정보 사이트 검색 결과(출처 표기). DART 원문과 다를 수 있습니다.",
        "- 이 보고서는 투자 권유가 아니며 매수·매도 판단과 책임은 투자자 본인에게 있습니다.",
        "",
    ]
    return "\n".join(L)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
CSS = """
/* 레이아웃: 한 줄 기둥(본문 72rem) — 시장 띠 → 결론 → 주목 종목 카드 → 전체 표 → 방법 */
:root{
  --paper:#f6f7f9; --panel:#ffffff; --ink:#15202b; --sub:#5b6875; --line:#dde2e8;
  --accent:#1f5f8b; --up:#d1343a; --down:#1f62c9;
  --g-watch:#0f7a55; --g-obs:#a46200; --g-hold:#6b7682;
  --chip-watch:#e3f4ec; --chip-obs:#fbf0dc; --chip-hold:#eceff2;
  --seg1:#1f5f8b; --seg2:#3f86b5; --seg3:#79aed2; --seg4:#5d7f99; --seg5:#9fb7c9; --seg6:#c3d4e1;
  --f-body:"IBM Plex Sans KR","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;
  --f-num:"IBM Plex Mono",ui-monospace,"SFMono-Regular",Menlo,monospace;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --paper:#0f151b; --panel:#16202a; --ink:#e4eaf0; --sub:#93a2b0; --line:#28343f;
  --accent:#6fb0dc; --up:#ff6b6f; --down:#6aa2ff;
  --g-watch:#4fd19c; --g-obs:#f0b04d; --g-hold:#93a2b0;
  --chip-watch:#123526; --chip-obs:#3a2a0f; --chip-hold:#222d37;
  --seg1:#6fb0dc; --seg2:#4f8fbd; --seg3:#3a7099; --seg4:#8aa6bb; --seg5:#5b7487; --seg6:#3f5262;
  color-scheme:dark}}
:root[data-theme="dark"]{
  --paper:#0f151b; --panel:#16202a; --ink:#e4eaf0; --sub:#93a2b0; --line:#28343f;
  --accent:#6fb0dc; --up:#ff6b6f; --down:#6aa2ff;
  --g-watch:#4fd19c; --g-obs:#f0b04d; --g-hold:#93a2b0;
  --chip-watch:#123526; --chip-obs:#3a2a0f; --chip-hold:#222d37;
  --seg1:#6fb0dc; --seg2:#4f8fbd; --seg3:#3a7099; --seg4:#8aa6bb; --seg5:#5b7487; --seg6:#3f5262;
  color-scheme:dark}
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink);font-family:var(--f-body);font-size:15px;line-height:1.6;word-break:keep-all;overflow-wrap:break-word;
  padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:28px 56px}
.wrap{max-width:72rem;margin:0 auto;display:flex;flex-direction:column;gap:28px}
h1,h2,h3{text-wrap:balance;margin:0;line-height:1.3}
h1{font-size:1.6rem;font-weight:700;letter-spacing:-.01em}
h2{font-size:1.05rem;font-weight:700;color:var(--sub);letter-spacing:.04em}
h3{font-size:1.15rem;font-weight:700}
a{color:var(--accent)}
a:focus-visible,button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.num{font-family:var(--f-num);font-variant-numeric:tabular-nums}
.up{color:var(--up)} .down{color:var(--down)}
header{display:flex;flex-direction:column;gap:6px}
.eyebrow{font-size:.78rem;letter-spacing:.08em;color:var(--sub);font-weight:600}
.scope{color:var(--sub);max-width:65ch}
.market{display:flex;flex-wrap:wrap;gap:12px}
.idx{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 14px;display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
.idx b{font-size:.9rem}
.idx .v{font-size:1.05rem}
.idx .m{font-size:.82rem;color:var(--sub)}
.summary{font-size:1.05rem;max-width:68ch}
.summary p{margin:0 0 .6em}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,30rem),1fr));gap:16px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px;display:flex;flex-direction:column;gap:12px;min-width:0}
.card-top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.card-top .who{min-width:0}
.code{color:var(--sub);font-size:.82rem}
.biz{color:var(--sub);font-size:.9rem;margin-top:2px}
.score{text-align:right;flex:none}
.score .t{font-size:1.7rem;font-weight:600;line-height:1}
.score .u{font-size:.75rem;color:var(--sub)}
.chip{display:inline-block;font-size:.75rem;font-weight:700;border-radius:999px;padding:2px 10px;letter-spacing:.02em}
.chip.관심{background:var(--chip-watch);color:var(--g-watch)}
.chip.관찰{background:var(--chip-obs);color:var(--g-obs)}
.chip.보류{background:var(--chip-hold);color:var(--g-hold)}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(7.5rem,1fr));gap:8px 14px;margin:0}
.facts div{display:flex;flex-direction:column}
.facts dt{font-size:.72rem;color:var(--sub);letter-spacing:.04em}
.facts dd{margin:0;font-size:.95rem}
.bar{display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--line)}
.bar span{display:block;height:100%}
.legend{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:.76rem;color:var(--sub)}
.legend i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px;vertical-align:baseline}
.block{font-size:.9rem}
.block h4{margin:0 0 2px;font-size:.78rem;color:var(--sub);letter-spacing:.04em}
.block ul{margin:0;padding-left:1.1em}
.flags{display:flex;flex-wrap:wrap;gap:6px}
.flag{font-size:.76rem;border:1px solid var(--line);border-radius:6px;padding:1px 8px;color:var(--sub)}
.flag.warn{border-color:var(--g-obs);color:var(--g-obs)}
.src{font-size:.78rem;color:var(--sub);overflow-wrap:anywhere}
.tbl{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:.88rem;min-width:44rem}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{font-size:.74rem;color:var(--sub);letter-spacing:.04em;font-weight:600}
td.r,th.r{text-align:right}
tr:last-child td{border-bottom:0}
.muted{color:var(--sub);font-size:.88rem}
footer{color:var(--sub);font-size:.82rem;max-width:72ch;display:flex;flex-direction:column;gap:6px}
footer p{margin:0}
"""

SEG_VARS = ["--seg1", "--seg2", "--seg3", "--seg4", "--seg5", "--seg6"]


def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _signed(x, digits=0) -> str:
    if x is None:
        return "<span>–</span>"
    cls = "up" if x > 0 else ("down" if x < 0 else "")
    return f'<span class="num {cls}">{x:+.{digits}%}</span>'


def _card(c: dict, n: dict, verdict: str) -> str:
    pm, sc, meta = c["price"], c["score"], c["meta"]
    segs = "".join(
        f'<span style="width:{sc["parts"][k]:.1f}%;background:var({SEG_VARS[i]})" title="{lab} {sc["parts"][k]:.0f}/{mx}"></span>'
        for i, (k, lab, mx) in enumerate(PART_LABELS)
    )
    legend = "".join(
        f'<span><i style="background:var({SEG_VARS[i]})"></i>{lab} <span class="num">{sc["parts"][k]:.0f}/{mx}</span></span>'
        for i, (k, lab, mx) in enumerate(PART_LABELS)
    )
    if sc["parts"]["penalty"]:
        legend += f'<span>감점 <span class="num">{sc["parts"]["penalty"]:.0f}</span></span>'
    base_price = pm["ipo_price"] or pm["first_close"]
    facts = [
        ("상장일", _iso(c["listed"])),
        ("종가", f'<span class="num">{pm["close"]:,.0f}</span>원'),
        ("시가총액", _eok(c.get("mcap"))),
        (f'{pm["base_kind"]}', f'<span class="num">{base_price:,.0f}</span>원 · {_signed(pm["vs_base"])}'),
        ("상장후 고점 대비", _signed(pm["from_high"])),
        ("5일 평균 거래대금", _eok(pm["tv_5d"])),
    ]
    if meta.get("demand_competition") is not None:
        facts.append(("수요예측 경쟁률", f'<span class="num">{meta["demand_competition"]:,.0f}</span> : 1'))
    if meta.get("lockup_commit_pct") is not None:
        facts.append(("의무보유확약", f'<span class="num">{meta["lockup_commit_pct"]:.1f}</span>%'))
    facts_html = "".join(f"<div><dt>{_e(k)}</dt><dd>{v}</dd></div>" for k, v in facts)

    def block(key, title):
        if not n.get(key):
            return ""
        return f'<div class="block"><h4>{title}</h4><ul>' + "".join(f"<li>{_e(x)}</li>" for x in n[key]) + "</ul></div>"

    flags = "".join(
        f'<span class="flag{" warn" if ("보호예수" in f or "변동성" in f or "자본잠식" in f) else ""}">{_e(f)}</span>'
        for f in sc["flags"]
    )
    srcs = list(dict.fromkeys((n.get("sources") or []) + (meta.get("sources") or [])))
    src_html = (
        '<div class="src">출처 ' + " · ".join(f'<a href="{_e(u)}" target="_blank" rel="noopener">{_e(_host(u))}</a>' for u in srcs) + "</div>"
        if srcs else ""
    )
    biz = n.get("business") or meta.get("business")
    return f"""
<article class="card">
  <div class="card-top">
    <div class="who">
      <h3><a href="{_e(c['url'])}" target="_blank" rel="noopener">{_e(c['name'])}</a></h3>
      <div class="code num">{_e(c['code'])} · {_e(c['market'])}</div>
      {f'<div class="biz">{_e(biz)}</div>' if biz else ''}
    </div>
    <div class="score"><span class="chip {_e(verdict)}">{_e(verdict)}</span><div class="t num">{sc['total']:.0f}</div><div class="u">/ 100점</div></div>
  </div>
  <dl class="facts">{facts_html}</dl>
  <div><div class="bar" role="img" aria-label="점수 구성">{segs}</div><div class="legend">{legend}</div></div>
  <div class="block"><h4>정량 근거</h4>{_e(' · '.join(sc['notes']))}</div>
  {block('thesis', '성장 포인트')}{block('risks', '리스크')}{block('catalysts', '일정·촉매')}
  {f'<div class="flags">{flags}</div>' if flags else ''}
  {src_html}
</article>"""


def _host(u: str) -> str:
    try:
        return u.split("/")[2].removeprefix("www.")
    except IndexError:
        return u


def to_html(data: dict, notes: dict, prev) -> str:
    a = data["as_of"]
    p = data["params"]
    mk = "".join(
        f'<div class="idx"><b>{_e(k)}</b><span class="v num">{m["close"]:,.2f}</span>'
        f'<span class="m">전일 {_signed(m["chg_1d"], 1)} · 20일 {_signed(m["ret_20d"], 1)} · 20일선 {"위" if m["above_ma20"] else "아래"}</span></div>'
        for k, m in data["market"].items()
    )
    summary = "".join(f"<p>{_e(x)}</p>" for x in [notes.get("summary"), notes.get("market_comment")] if x)
    picks = [c for c in data["candidates"] if verdict_of(c, notes) in ("관심", "관찰")]
    cards = "".join(_card(c, notes.get("picks", {}).get(c["code"], {}), verdict_of(c, notes)) for c in picks)
    if not cards:
        cards = '<p class="muted">오늘은 관심·관찰 등급 종목이 없습니다. 아래 전체 후보 표를 참고하세요.</p>'
    rows = []
    for i, c in enumerate(data["candidates"], 1):
        pm = c["price"]
        v = verdict_of(c, notes)
        vs = _signed(pm["vs_base"]) + ("" if pm["ipo_price"] else ' <span class="muted">(상장일 종가)</span>')
        rows.append(
            f'<tr><td class="num">{i}</td><td class="num">{_e(rank_change(c["code"], i, prev))}</td>'
            f'<td><a href="{_e(c["url"])}" target="_blank" rel="noopener">{_e(c["name"])}</a> <span class="muted num">{_e(c["code"])}</span></td>'
            f'<td class="num">{_iso(c["listed"])}</td><td class="r num">{pm["close"]:,.0f}</td><td class="r">{vs}</td>'
            f'<td class="r">{_signed(pm["from_high"])}</td><td class="r num">{c["score"]["total"]:.0f}</td>'
            f'<td><span class="chip {_e(v)}">{_e(v)}</span></td></tr>'
        )
    above = ", ".join(f'{_e(r["name"])} <span class="num">{r["close"]:,.0f}</span>원' for r in data["above_price"]) or "없음"
    excluded = ", ".join(f'{_e(r["name"])}({_e(r["reason"])})' for r in data["excluded"]) or "없음"
    changes = f'<section><h2>전일 대비</h2><p class="summary">{_e(notes["changes"])}</p></section>' if notes.get("changes") else ""
    return f"""<title>신규상장 저가주 리포트</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans+KR:wght@400;600;700&display=swap">
<style>{CSS}</style>
<div class="wrap">
<header>
  <div class="eyebrow">신규상장 · {p['max_price']:,.0f}원 이하 · 일일 스크리닝</div>
  <h1>{_date_ko(a)} {basis_label(data)}</h1>
  <div class="scope">최근 {p['days']}일 안에 상장한 코스피·코스닥 보통주 중 종가 {p['max_price']:,.0f}원 이하 종목 {len(data['candidates'])}개를 채점했습니다. 스팩·우선주·리츠는 뺐습니다.</div>
</header>
<section class="market" aria-label="시장 환경">{mk}</section>
{f'<section class="summary">{summary}</section>' if summary else ''}
<section style="display:flex;flex-direction:column;gap:12px"><h2>주목 종목</h2><div class="cards">{cards}</div></section>
<section style="display:flex;flex-direction:column;gap:12px"><h2>전체 후보</h2>
<div class="tbl"><table>
<thead><tr><th>순위</th><th>변동</th><th>종목</th><th>상장일</th><th class="r">종가</th><th class="r">공모가 대비</th><th class="r">고점 대비</th><th class="r">점수</th><th>등급</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<div class="muted">가격 기준 초과: {above}</div>
<div class="muted">제외: {excluded}</div>
</section>
{changes}
<footer>
  <p>점수 100점 = 성장성 30 + 수익성 15 + 상장후 흐름 20 + 수급 15 + 공모 품질 10 + 밸류 10, 보호예수 해제 임박·과도한 변동성·고부채는 감점합니다. CANSLIM 을 상장 초기 종목에 맞게 바꾼 기준이며 65점 이상 관심, 50점 이상 관찰입니다.</p>
  <p>시세·재무는 네이버 증권, 공모가·수요예측·확약 비율은 언론·IPO 정보 사이트 검색 결과입니다. DART 원문과 다를 수 있습니다. 생성 {_e(data['generated_at'])}.</p>
  <p>이 보고서는 투자 권유가 아닙니다. 매수·매도 판단과 그 결과의 책임은 투자자 본인에게 있습니다.</p>
</footer>
</div>
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
