"""Render the question bank to a standalone, searchable HTML handout for
students. Regenerated in a background thread whenever a question is added."""
import json
import html
import time

import config

_CSS = (
    "body{font-family:system-ui,'Segoe UI',Roboto,sans-serif;margin:0;"
    "background:#0e1b3a;color:#eaf2ff;}"
    "header{position:sticky;top:0;background:#0a1738;padding:14px 20px;"
    "border-bottom:1px solid #1f3a78;}"
    "h1{margin:0 0 8px;font-size:18px;color:#9bdcff;}"
    "#s{width:100%;max-width:480px;padding:8px 12px;border-radius:8px;"
    "border:1px solid #1f3a78;background:#142555;color:#eaf2ff;font-size:14px;}"
    ".wrap{padding:16px 20px;max-width:900px;margin:0 auto;}"
    ".qa{background:#142555;border:1px solid #1f3a78;border-radius:10px;"
    "padding:12px 14px;margin:10px 0;}"
    ".q{font-weight:700;color:#fff;}.n{color:#5aa9e6;margin-right:8px;}"
    ".a{margin-top:6px;color:#cfe0ff;white-space:pre-wrap;}"
    ".meta{margin-top:6px;font-size:12px;color:#94a8d4;}"
    ".count{font-size:12px;color:#94a8d4;}"
)

_SCRIPT = (
    "<script>function f(){var v=document.getElementById('s').value.toLowerCase();"
    "document.querySelectorAll('.qa').forEach(function(e){"
    "e.style.display=e.getAttribute('data-text').indexOf(v)>=0?'':'none';});}</script>"
)


def render(bank: list) -> str:
    rows = []
    for i, e in enumerate(bank, 1):
        q = html.escape(e.get("question", ""))
        a = html.escape(e.get("answer", ""))
        src = e.get("source") or {}
        ref = str(src.get("book") or "")
        page = src.get("page")
        if page not in (None, ""):
            ref = (ref + ", p." + str(page)) if ref else ("p." + str(page))
        topic = str(e.get("topic") or "")
        meta = html.escape(ref + ((" • " + topic) if topic else ""))
        dt = html.escape((e.get("question", "") + " " + e.get("answer", "")).lower())
        rows.append(
            '<div class="qa" data-text="' + dt + '">'
            '<div class="q"><span class="n">' + str(i) + '</span>' + q + '</div>'
            '<div class="a">' + a + '</div>'
            '<div class="meta">' + meta + '</div></div>'
        )
    ts = time.strftime("%Y-%m-%d %H:%M")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>EV Lab — Question Bank</title><style>' + _CSS + '</style></head><body>'
        '<header><h1>EV Lab — Question Bank</h1>'
        '<input id="s" placeholder="Search questions…" oninput="f()">'
        '<div class="count">' + str(len(bank)) + ' questions • generated ' + ts + '</div></header>'
        '<div class="wrap">' + "\n".join(rows) + '</div>' + _SCRIPT + '</body></html>'
    )


def render_to_file(bank_json_path=None, html_path=None) -> int:
    bank_json_path = bank_json_path or config.BANK_JSON
    html_path = html_path or config.BANK_HTML
    with open(bank_json_path, encoding="utf-8") as fh:
        data = json.load(fh)
    bank = data["qa"] if isinstance(data, dict) and "qa" in data else data
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(render(bank))
    return len(bank)
