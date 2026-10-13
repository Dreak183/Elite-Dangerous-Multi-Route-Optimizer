import { describe, expect, it } from 'vitest';
import raw_vectors from './vectors.json?raw';
import { b64urlDecode } from '../src/b64';
import { isSmallOrder, parseEnvelope, verifySignature } from '../src/envelope';
import { computeRoster, plausible, realStamp, rosterEntry, sha256hex, validRosterPayload } from '../src/roster';

// Not a JSON import: the file carries a lone-surrogate escape the bundler refuses.
const v = JSON.parse(raw_vectors) as any;
const NOW = realStamp(v.now)!;
const raw = (text: string) => b64urlDecode(text, 1 << 20)!;
const utf8 = (text: string) => new TextEncoder().encode(text);

describe('shared vectors (tests/vectors/make_crew_vectors.py)', () => {
  it('structure cases, judged per layer', () => {
    expect(v.structure_cases.length).toBeGreaterThan(20);
    for (const c of v.structure_cases) {
      const p = parseEnvelope(raw(c.e));
      const ok = c.layer === 'roster' ? p !== null && rosterEntry(p.env, p.text, NOW) !== null : p !== null;
      expect(ok, c.name).toBe(c.valid);
    }
  });

  it('signature cases', async () => {
    for (const c of v.signature_cases) {
      expect(await verifySignature(c.author, raw(c.s), raw(c.e)), c.name).toBe(c.valid);
    }
  });

  it('small-order keys are refused by the blocklist, not by the library', () => {
    const identity = raw(v.signature_cases.find((c: any) => c.name === 'small_order_author').author);
    expect(isSmallOrder(identity)).toBe(true);
    expect(isSmallOrder(raw(v.keys.leader.author))).toBe(false);
  });

  it('every small-order signature case is accepted by plain verification, so only the blocklist refuses it', async () => {
    const cases = v.signature_cases.filter((c: any) => c.name.startsWith('small_order_'));
    expect(cases.length).toBe(15);
    for (const c of cases) {
      const key = await crypto.subtle.importKey('raw', raw(c.author), { name: 'Ed25519' }, false, ['verify']);
      expect(await crypto.subtle.verify({ name: 'Ed25519' }, key, raw(c.s), raw(c.e)), c.name).toBe(true);
      expect(isSmallOrder(raw(c.author)), c.name).toBe(true);
    }
  });

  it('sha256hex is total', async () => {
    expect(await sha256hex('')).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
    expect(await sha256hex('00')).toBe('6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d');
  });

  it('canonical cases', () => {
    for (const c of v.canonical_cases) {
      const parsed = parseEnvelope(raw(c.e));
      const entry = parsed ? rosterEntry(parsed.env, parsed.text, NOW) : null;
      expect(entry !== null, c.name).toBe(c.canonical);
    }
  });

  it('roster payload cases', () => {
    for (const c of v.roster_payload_cases) {
      expect(validRosterPayload(c.kind, c.payload), JSON.stringify(c)).toBe(c.valid);
    }
  });

  it('plausibility cases', () => {
    for (const c of v.plausibility_cases) expect(plausible(c.created_at, NOW), c.created_at).toBe(c.plausible);
  });

  it('roster cases', async () => {
    for (const c of v.roster_cases) {
      const entries = c.envelopes
        .map((name: string) => {
          const parsed = parseEnvelope(raw(v.envelopes[name].e))!;
          return rosterEntry(parsed.env, parsed.text, NOW);
        })
        .filter((e: unknown) => e !== null);
      const roster = await computeRoster(entries, v.group_id, c.leader);
      const members = Object.fromEntries([...roster.members].map(([a, m]) => [a, m.lastSeq]));
      expect(members, c.name).toEqual(c.members);
      expect(roster.name, c.name).toBe(c.name_of_group);
      const details = Object.fromEntries([...roster.members].map(([a, m]) => [a, { commander: m.commander, leader: m.leader }]));
      expect(details, c.name).toEqual(c.member_details);
    }
  });

  it('auth signature', async () => {
    const a = v.auth;
    expect(await verifySignature(a.author, raw(a.sig), utf8(`edmrn-crew-auth\n${a.group}\n${a.nonce}`))).toBe(true);
  });

  it('auth negatives', async () => {
    for (const c of v.auth_negative) {
      const msg = utf8(`edmrn-crew-auth\n${c.group}\n${c.nonce}`);
      expect(await verifySignature(c.author, raw(c.sig), msg), c.name).toBe(false);
    }
  });

  it('status signature and negatives', async () => {
    const s = v.status;
    expect(await verifySignature(s.author, raw(s.s), raw(s.e))).toBe(true);
    for (const c of v.status_negative) expect(await verifySignature(c.author, raw(c.s), raw(c.e)), c.name).toBe(false);
  });
});
