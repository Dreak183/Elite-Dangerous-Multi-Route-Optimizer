"""The invite landing page (spec B1 S5, R1 S17), a fixed page.

The invite lives in the URL fragment, which browsers never send to the server; the script shows it as
text, never as markup. The Content-Security-Policy allows exactly this script and this style by hash.
"""
from __future__ import annotations

import base64
import hashlib

JOIN_SCRIPT = (
    "(function(){var code='';try{code=decodeURIComponent(location.hash.slice(1));}catch(e){}"
    "var ok=/^edmrn-crew:1:[A-Za-z0-9_-]{1,1987}$/.test(code);var out=document.getElementById('code');"
    "out.textContent=ok?code:'This link holds no valid EDMRN crew invite.';var b=document.getElementById('copy');"
    "b.hidden=!ok;b.addEventListener('click',function(){navigator.clipboard.writeText(code).then(function(){b.textContent='Copied';},function(){b.textContent='Copy failed - select the text';});});})();"
)

JOIN_STYLE = (
    "body{font-family:system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;background:#111;color:#eee}"
    "#code{word-break:break-all;background:#222;padding:.75rem;border-radius:.4rem;font-family:monospace}"
    "button{margin-top:.75rem;padding:.5rem 1rem}"
)


def _hash(text: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()


JOIN_HTML = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1"><title>EDMRN crew invite</title>'
    f"<style>{JOIN_STYLE}</style></head><body><h1>EDMRN crew invite</h1>"
    "<p>Copy this code, then in EDMRN open Settings &gt; Crew... &gt; Join with invite and paste it.</p>"
    f'<div id="code"></div><button id="copy" hidden>Copy</button><script>{JOIN_SCRIPT}</script></body></html>'
)

JOIN_CSP = (
    f"default-src 'none'; script-src '{_hash(JOIN_SCRIPT)}'; style-src '{_hash(JOIN_STYLE)}'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

JOIN_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Type", "text/html; charset=utf-8"),
    ("Content-Security-Policy", JOIN_CSP),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
)


def greeting(source_url: str) -> str:
    return f"EDMRN crew relay. Licence: AGPL-3.0. Source: {source_url or 'not yet published'}\n"
