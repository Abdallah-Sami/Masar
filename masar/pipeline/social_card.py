"""
Turns a post's "card" (from social.json) into a 1200x675 PNG image for X,
in the site's design (navy, teal, gold). Rendered with Playwright, which the
workflow already installs for the scrapers.
"""
from __future__ import annotations

import base64
import html
from pathlib import Path

LOGO = Path(__file__).resolve().parents[2] / "website" / "assets" / "logo-light.svg"
W, H = 1200, 675


def _logo_uri() -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(LOGO.read_bytes()).decode() if LOGO.exists() else ""


def _body(card: dict) -> str:
    e = html.escape
    kind = card.get("kind")
    if kind == "stats":
        cells = "".join(f'<div class="cell"><b>{e(str(v))}</b><span>{e(str(l))}</span></div>' for v, l in card["items"])
        return f'<div class="stats">{cells}</div>'
    if kind == "bars":
        top = max(v for _, v in card["items"]) or 1
        rows = "".join(
            f'<div class="bar"><div class="row"><span><bdi>{e(str(n))}</bdi></span><b>{v}%</b></div>'
            f'<div class="track"><div class="fill" style="width:{max(4, 100 * v / top):.0f}%"></div></div></div>'
            for n, v in card["items"])
        note = f'<p class="note">{e(card.get("note", ""))}</p>' if card.get("note") else ""
        return f'<div class="bars">{rows}</div>{note}'
    if kind == "list":
        rows = "".join(f'<div class="li"><i>{i}</i><span><bdi>{e(str(n))}</bdi></span><b>{e(str(v))}</b></div>'
                       for i, (n, v) in enumerate(card["items"], 1))
        return f'<div class="list">{rows}</div>'
    # "big": one large number
    return (f'<div class="big"><b>{e(card.get("number", ""))}</b><span>{e(card.get("label", ""))}</span>'
            f'<p>{e(card.get("sub", ""))}</p></div>')


def card_html(card: dict, date_text: str = "") -> str:
    return f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@500;600;700&family=Noto+Kufi+Arabic:wght@800&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box}} body{{margin:0}}
.c{{width:{W}px;height:{H}px;background:#0F2A3D;color:#fff;font-family:'IBM Plex Sans Arabic',sans-serif;
   padding:56px 72px;display:flex;flex-direction:column;position:relative;overflow:hidden}}
.route{{position:absolute;inset:0;width:100%;height:100%;opacity:.35}}
.top{{display:flex;align-items:center;justify-content:space-between;position:relative}}
.top img{{height:48px}} .top span{{color:#C9D6DE;font-size:22px}}
h1{{font-family:'Noto Kufi Arabic',sans-serif;font-weight:800;font-size:50px;margin:34px 0 26px;line-height:1.35;position:relative}}
.main{{flex:1;position:relative}}
.stats{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
.cell{{background:rgba(255,255,255,.07);border-radius:18px;padding:22px 28px}}
.cell b{{display:block;font-size:44px;color:#F0B35F;line-height:1.3}} .cell span{{font-size:24px;color:#C9D6DE}}
.bars{{display:flex;flex-direction:column;gap:18px}}
.row{{display:flex;justify-content:space-between;font-size:30px;font-weight:600}} .row b{{color:#F0B35F}}
.track{{height:14px;background:rgba(255,255,255,.12);border-radius:99px;margin-top:8px}}
.fill{{height:14px;background:#13A39A;border-radius:99px}}
.note{{font-size:20px;color:#9DB0BC;margin:20px 0 0}}
.list{{display:flex;flex-direction:column;gap:14px}}
.li{{display:flex;align-items:center;gap:20px;font-size:32px;font-weight:600;background:rgba(255,255,255,.07);border-radius:16px;padding:12px 24px}}
.li i{{font-style:normal;color:#0F2A3D;background:#F0B35F;width:46px;height:46px;border-radius:12px;display:grid;place-items:center;font-size:26px}}
.li span{{flex:1}} .li b{{color:#9FE0D8;font-size:26px}}
.big b{{display:block;font-family:'Noto Kufi Arabic',sans-serif;font-size:150px;color:#F0B35F;line-height:1.2}}
.big span{{font-size:34px;font-weight:600}} .big p{{font-size:26px;color:#C9D6DE;margin:18px 0 0;line-height:1.6}}
</style></head><body><div class="c">
<svg class="route" viewBox="0 0 1200 675" preserveAspectRatio="none"><path d="M-20 600 C 200 540, 260 420, 450 440 S 760 600, 900 450 S 1100 220, 1220 260" fill="none" stroke="#13A39A" stroke-width="4" stroke-dasharray="2 16" stroke-linecap="round"/></svg>
<div class="top"><img src="{_logo_uri()}" alt=""><span>{html.escape(date_text)}</span></div>
<h1>{html.escape(card.get("headline", ""))}</h1>
<div class="main">{_body(card)}</div>
</div></body></html>"""


def render(card: dict, target: Path, date_text: str = "") -> Path:
    from playwright.sync_api import sync_playwright
    target.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        page.set_content(card_html(card, date_text), wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(target), clip={"x": 0, "y": 0, "width": W, "height": H})
        browser.close()
    return target
