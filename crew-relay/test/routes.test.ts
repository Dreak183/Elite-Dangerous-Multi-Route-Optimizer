import { SELF } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';
import { JOIN_SCRIPT, JOIN_STYLE } from '../src/join';

async function sha(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return 'sha256-' + btoa(String.fromCharCode(...new Uint8Array(digest)));
}

describe('routes', () => {
  it('greets with the source URL', async () => {
    const res = await SELF.fetch('https://relay.test/');
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toContain('text/plain');
    expect(await res.text()).toContain('https://example.invalid/edmrn');
  });

  it('serves the invite page with a hash-only CSP', async () => {
    const res = await SELF.fetch('https://relay.test/join');
    const csp = res.headers.get('content-security-policy')!;
    expect(csp).toContain(`script-src '${await sha(JOIN_SCRIPT)}'`);
    expect(csp).toContain(`style-src '${await sha(JOIN_STYLE)}'`);
    expect(csp).toContain("default-src 'none'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(res.headers.get('referrer-policy')).toBe('no-referrer');
    const html = await res.text();
    expect(html).toContain(`<script>${JOIN_SCRIPT}</script>`);
    expect(JOIN_SCRIPT).toContain('textContent');
    expect(JOIN_SCRIPT).not.toContain('innerHTML');
    expect(JOIN_SCRIPT).toContain('{1,1987}'); // 2000-character invite limit minus the 13-character prefix
    expect(JOIN_SCRIPT).toContain('Copy failed'); // clipboard rejection is handled
  });

  it('refuses unknown paths, bad group ids and plain GETs on the socket path', async () => {
    expect((await SELF.fetch('https://relay.test/nope')).status).toBe(404);
    expect((await SELF.fetch('https://relay.test/v1/groups/not-a-uuid/ws', { headers: { Upgrade: 'websocket' } })).status).toBe(404);
    expect((await SELF.fetch('https://relay.test/v1/groups/01920000-0000-7000-8000-0000000000a1/ws')).status).toBe(426);
  });

  it('forwards a valid socket upgrade to the group object and refuses malformed ids', async () => {
    const id = '01920000-0000-7000-8000-0000000000a2';
    expect((await SELF.fetch(`https://relay.test/v1/groups/${id}/ws`, { headers: { Upgrade: 'websocket' } })).status).toBe(101);
    const upper = id.toUpperCase();
    expect((await SELF.fetch(`https://relay.test/v1/groups/${upper}/ws`, { headers: { Upgrade: 'websocket' } })).status).toBe(404);
    const v4 = '01920000-0000-4000-8000-0000000000a2';
    expect((await SELF.fetch(`https://relay.test/v1/groups/${v4}/ws`, { headers: { Upgrade: 'websocket' } })).status).toBe(404);
  });
});
