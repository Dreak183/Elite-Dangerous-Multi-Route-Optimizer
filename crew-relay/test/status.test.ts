import { describe, expect, it } from 'vitest';
import { b64urlEncode } from '../src/b64';
import { canonicalJson } from '../src/canonical';
import { MAX_STATUS_BYTES } from '../src/group';
import { authed, createGroupAndUpload, envelope, invite, keyFor, newGroup, stamp, type Key } from './helpers';

const TOKEN = '22'.repeat(32);

async function statusFrame(key: Key, hull: number, author = key.author) {
  const doc = { author, at: stamp(), status: { commander: 'X', system: 'Sol', address: '1', coords: null, ship: null, ship_name: null, ship_ident: null, edsy_url: null, hull, fuel_main: null, fuel_reserve: null, fuel_capacity_main: null, fuel_capacity_reserve: null, paused: false } };
  const bytes = new TextEncoder().encode(canonicalJson(doc));
  return { t: 'st', e: b64urlEncode(bytes), s: b64urlEncode(await key.sign(bytes)) };
}

async function twoMembers() {
  const group = newGroup();
  const leader = await keyFor('leader');
  const alice = await keyFor('alice');
  const { client: lc } = await createGroupAndUpload(group, leader);
  const inviteId = await invite(group, lc, leader, 2, TOKEN);
  await lc.drain();
  const join = await envelope(alice, 1, 'join', { group_id: group, invite_id: inviteId, token: TOKEN, commander: 'Alice' });
  const ac = await authed(group, alice, { join });
  await ac.nextOf('have');
  return { group, leader, alice, lc, ac };
}

describe('status', () => {
  it('is forwarded, rate-limited, and checked for author and signature', async () => {
    const { leader, alice, lc, ac } = await twoMembers();
    ac.send(await statusFrame(alice, 0.5));
    const got = await lc.nextOf('st');
    expect(JSON.parse(atob(got.e.replace(/-/g, '+').replace(/_/g, '/'))).status.hull).toBe(0.5);
    ac.send(await statusFrame(alice, 0.4));
    expect((await ac.nextOf('err')).code).toBe('rate');
    lc.send(await statusFrame(leader, 1, alice.author));
    expect((await lc.nextOf('err')).code).toBe('bad_frame');
  });

  it('an oversize status answers too_big and a bad signature answers bad_sig (C10, S8)', async () => {
    const { leader, alice, lc, ac } = await twoMembers();
    const big = new Uint8Array(MAX_STATUS_BYTES + 1);
    ac.send({ t: 'st', e: b64urlEncode(big), s: 'A'.repeat(86) });
    expect((await ac.nextOf('err')).code).toBe('too_big');
    const forged = await statusFrame(leader, 0.8);
    forged.s = (await statusFrame(alice, 0.8, leader.author)).s;
    lc.send(forged);
    expect((await lc.nextOf('err')).code).toBe('bad_sig');
  });

  it('the last status of a member who left reaches a new connection', async () => {
    const { group, leader, alice, lc, ac } = await twoMembers();
    ac.send(await statusFrame(alice, 0.3));
    await lc.nextOf('st');
    ac.ws.close(1000, 'bye');
    await ac.closed;
    await new Promise((r) => setTimeout(r, 100));
    const again = await authed(group, leader);
    const st = await again.nextOf('st');
    expect(JSON.parse(atob(st.e.replace(/-/g, '+').replace(/_/g, '/'))).author).toBe(alice.author);
  });

  it('the stored status of a removed member is not sent to a new connection (S12)', async () => {
    const { group, leader, alice, lc, ac } = await twoMembers();
    ac.send(await statusFrame(alice, 0.3));
    await lc.nextOf('st');
    ac.ws.close(1000, 'bye');
    await ac.closed;
    await new Promise((r) => setTimeout(r, 100));
    lc.send({ t: 'evs', items: [await envelope(leader, 3, 'remove', { group_id: group, member: alice.author, last_seq: 1 })] });
    await lc.drain();
    const again = await authed(group, leader);
    await again.nextOf('have');
    const messages = await again.drain(200);
    expect(messages.filter((m) => m.t === 'st')).toEqual([]);
  });
});
