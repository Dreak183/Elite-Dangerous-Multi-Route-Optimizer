// The invite landing page (spec B1 S5). The invite lives in the URL fragment, which browsers never
// send to the server; the script shows it as text, never as markup.
export const JOIN_SCRIPT =
  "(function(){var code='';try{code=decodeURIComponent(location.hash.slice(1));}catch(e){}" +
  "var ok=/^edmrn-crew:1:[A-Za-z0-9_-]{1,1987}$/.test(code);var out=document.getElementById('code');" +
  "out.textContent=ok?code:'This link holds no valid EDMRN crew invite.';var b=document.getElementById('copy');" +
  "b.hidden=!ok;b.addEventListener('click',function(){navigator.clipboard.writeText(code).then(function(){b.textContent='Copied';},function(){b.textContent='Copy failed - select the text';});});})();";

export const JOIN_STYLE =
  'body{font-family:system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;background:#111;color:#eee}' +
  '#code{word-break:break-all;background:#222;padding:.75rem;border-radius:.4rem;font-family:monospace}' +
  'button{margin-top:.75rem;padding:.5rem 1rem}';

async function hash(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return 'sha256-' + btoa(String.fromCharCode(...new Uint8Array(digest)));
}

export async function joinPage(): Promise<Response> {
  const html =
    '<!doctype html><html lang="en"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1"><title>EDMRN crew invite</title>' +
    `<style>${JOIN_STYLE}</style></head><body><h1>EDMRN crew invite</h1>` +
    '<p>Copy this code, then in EDMRN open Settings &gt; Crew... &gt; Join with invite and paste it.</p>' +
    `<div id="code"></div><button id="copy" hidden>Copy</button><script>${JOIN_SCRIPT}</script></body></html>`;
  const csp =
    `default-src 'none'; script-src '${await hash(JOIN_SCRIPT)}'; style-src '${await hash(JOIN_STYLE)}'; ` +
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'";
  return new Response(html, {
    headers: {
      'content-type': 'text/html; charset=utf-8',
      'content-security-policy': csp,
      'referrer-policy': 'no-referrer',
      'cache-control': 'no-store',
    },
  });
}
