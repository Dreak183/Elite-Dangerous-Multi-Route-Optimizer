import { evictDurableObject, runInDurableObject } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';
import { b64urlDecode, b64urlEncode } from '../src/b64';
import { GROUP_DAILY, MEMBER_DAILY, utcDay, type GroupRelay } from '../src/group';
import {
  authed, createGroupAndUpload, envelope, invite, keyFor, newGroup, open, relayFor, sha256hex, stamp, type Key,
} from './helpers';

const TOKEN = '11'.repeat(32);
const decode = (item: { e: string }) => JSON.parse(new TextDecoder().decode(b64urlDecode(item.e, 1 << 20)!));
const items = (messages: any[]) => messages.filter((m) => m.t === 'evs').flatMap((m) => m.items);
const visit = (key: Key, seq: number) =>
  envelope(key, seq, 'visit', { commander: 'A', system: 'Sol', address: String(seq), coords: null });
const headOf = (instance: GroupRelay, author: string) =>
  instance.exec<{ seq: number }>('SELECT seq FROM heads WHERE author = ?', author)[0]?.seq;

async function groupWithInvite(group: string) {
  const leader = await keyFor('leader');
  const alice = await keyFor('alice');
  const { client: leaderClient } = await createGroupAndUpload(group, leader);
  const inviteId = await invite(group, leaderClient, leader, 2, TOKEN);
  await leaderClient.drain();
  return { leader, alice, leaderClient, inviteId };
}

async function joinEnvelope(group: string, alice: Key, inviteId: string, seq: number) {
  return envelope(alice, seq, 'join', { group_id: group, invite_id: inviteId, token: TOKEN, commander: 'Alice' });
}

describe('hibernatable WebSockets in the pool', () => {
  it('hello arrives and a frame round-trips through the Durable Object', async () => {
    const client = await open(newGroup());
    const hello = await client.nextOf('hello');
    expect(hello).toMatchObject({ t: 'hello', v: 1, source: 'https://example.invalid/edmrn' });
    expect(b64urlDecode(hello.nonce, 32)!.length).toBe(32);
    client.ws.send('not json');
    expect((await client.nextOf('err')).code).toBe('bad_frame');
  });

  it('a socket survives the eviction of its object and is served from storage', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    client.send({ t: 'evs', items: [await visit(leader, 2)] });
    await client.drain();
    await evictDurableObject(relayFor(group));
    client.send({ t: 'have', heads: {} });
    expect(await client.nextOf('synced')).toEqual({ t: 'synced' });
    client.send({ t: 'evs', items: [await visit(leader, 3)] });
    const other = await authed(group, leader);
    await other.nextOf('have');
    other.send({ t: 'have', heads: { [leader.author]: 1 } });
    expect(items(await other.drain(300)).map((i: any) => decode(i).seq)).toEqual([2, 3]);
  });
});

describe('auth', () => {
  it('an empty group refuses auth without a genesis', async () => {
    const client = await authed(newGroup(), await keyFor('leader'));
    expect((await client.closed).code).toBe(4003);
  });

  it('a genesis by an author not in CREW_LEADERS is refused', async () => {
    const group = newGroup();
    const alice = await keyFor('alice');
    const genesis = await envelope(alice, 1, 'group', { group_id: group, name: 'Crew', commander: 'A' });
    expect((await (await authed(group, alice, { genesis })).closed).code).toBe(4003);
  });

  it('a genesis for another group id is refused', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const genesis = await envelope(leader, 1, 'group', { group_id: newGroup(), name: 'Crew', commander: 'L' });
    expect((await (await authed(group, leader, { genesis })).closed).code).toBe(4003);
  });

  it('writes no row, not even the tables, before a valid genesis', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const alice = await keyFor('alice');
    const wrongGroup = await envelope(leader, 1, 'group', { group_id: newGroup(), name: 'Crew', commander: 'L' });
    const stranger = await envelope(alice, 1, 'group', { group_id: group, name: 'Crew', commander: 'A' });
    await (await authed(group, leader)).closed;
    await (await authed(group, leader, { genesis: wrongGroup })).closed;
    await (await authed(group, alice, { genesis: stranger })).closed;
    const bad = await open(group);
    await bad.nextOf('hello');
    bad.send({ t: 'auth', group, author: leader.author, sig: 'A'.repeat(86) });
    await bad.closed;
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(instance.writes).toBe(0);
      expect(instance.exec('SELECT name FROM sqlite_master').length).toBe(0);
    });
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(instance.exec('SELECT 1 FROM roster').length).toBe(1);
    });
  });

  it('does not depend on the HTTP method of the upgrade request', async () => {
    const group = newGroup();
    const res = await relayFor(group).fetch(`https://relay.test/v1/groups/${group}/ws`, {
      method: 'POST', headers: { Upgrade: 'websocket' },
    });
    expect(res.status).toBe(101);
    res.webSocket!.accept();
    res.webSocket!.close();
    const plain = await relayFor(group).fetch(`https://relay.test/v1/groups/${group}/ws`, { method: 'POST' });
    expect(plain.status).toBe(426);
  });

  it('a bad signature closes with 4001', async () => {
    const group = newGroup();
    const client = await open(group);
    await client.nextOf('hello');
    client.send({ t: 'auth', group, author: (await keyFor('leader')).author, sig: 'A'.repeat(86) });
    expect((await client.closed).code).toBe(4001);
  });

  it('a signature over another nonce or group closes with 4001', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const client = await open(group);
    await client.nextOf('hello');
    const sig = await leader.sign(new TextEncoder().encode(`edmrn-crew-auth\n${group}\n${b64urlEncode(new Uint8Array(32))}`));
    client.send({ t: 'auth', group, author: leader.author, sig: b64urlEncode(sig) });
    expect((await client.closed).code).toBe(4001);
  });

  it('a first frame other than auth closes with 4001', async () => {
    const client = await open(newGroup());
    await client.nextOf('hello');
    client.send({ t: 'have', heads: {} });
    expect((await client.closed).code).toBe(4001);
  });

  it('a have sent right behind auth is not overtaken by the signature check', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const genesis = await envelope(leader, 1, 'group', { group_id: group, name: 'Crew', commander: 'L' });
    const client = await open(group);
    const hello = await client.nextOf('hello');
    const sig = await leader.sign(new TextEncoder().encode(`edmrn-crew-auth\n${group}\n${hello.nonce}`));
    client.send({ t: 'auth', group, author: leader.author, sig: b64urlEncode(sig), genesis });
    client.send({ t: 'have', heads: {} });
    expect((await client.nextOf('have')).heads[leader.author]).toBe(0);
    expect(await client.nextOf('synced')).toEqual({ t: 'synced' });
  });

  it('a join admits; a non-member without a join is refused', async () => {
    const group = newGroup();
    const { alice, inviteId } = await groupWithInvite(group);
    expect((await (await authed(group, await keyFor('bob'))).closed).code).toBe(4003);
    const client = await authed(group, alice, { join: await joinEnvelope(group, alice, inviteId, 1) });
    expect((await client.nextOf('have')).heads[alice.author]).toBe(0);
  });

  it('an invite past its expiry on the relay clock does not admit', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const alice = await keyFor('alice');
    const { client: leaderClient } = await createGroupAndUpload(group, leader);
    const inviteId = await invite(group, leaderClient, leader, 2, TOKEN, -1000);
    await leaderClient.drain();
    const join = await envelope(alice, 1, 'join', { group_id: group, invite_id: inviteId, token: TOKEN, commander: 'Alice' }, stamp(-120_000));
    expect((await (await authed(group, alice, { join })).closed).code).toBe(4003);
  });

  it('a second connection of the same author replaces the first', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client: first } = await createGroupAndUpload(group, leader);
    const second = await authed(group, leader);
    await second.nextOf('have');
    expect((await first.closed).code).toBe(4000);
  });

  it('an unauthenticated socket older than 10 s is closed on the next activity', async () => {
    const group = newGroup();
    const idle = await open(group);
    await idle.nextOf('hello');
    await runInDurableObject(relayFor(group), async (_instance: GroupRelay, state) => {
      for (const ws of state.getWebSockets()) {
        const att = ws.deserializeAttachment();
        if (att.state === 'hello') ws.serializeAttachment({ ...att, openedAt: Date.now() - 11_000 });
      }
    });
    await createGroupAndUpload(group, await keyFor('leader'));
    expect((await idle.closed).code).toBe(4001);
  });
});

describe('heads and catch-up', () => {
  it('a join at auth does not move the head; earlier envelopes still upload; nothing above the head is served', async () => {
    const group = newGroup();
    const { leader, alice, inviteId } = await groupWithInvite(group);
    const join = await joinEnvelope(group, alice, inviteId, 3);
    const client = await authed(group, alice, { join });
    expect((await client.nextOf('have')).heads[alice.author]).toBe(0);
    const watcher = await authed(group, leader);
    await watcher.nextOf('have');
    watcher.send({ t: 'have', heads: {} });
    expect(items(await watcher.drain(300)).some((i: any) => decode(i).author === alice.author)).toBe(false);
    client.send({ t: 'evs', items: [await visit(alice, 1), await visit(alice, 2), join] });
    expect(items(await watcher.drain(300)).map((i: any) => decode(i).seq)).toEqual([1, 2, 3]);
  });

  it('a gap in seq is accepted and duplicates are ignored', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    const item = await visit(leader, 5);
    client.send({ t: 'evs', items: [item] });
    client.send({ t: 'evs', items: [item] });
    await client.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(headOf(instance, leader.author)).toBe(5);
      expect(instance.exec('SELECT 1 FROM log WHERE author = ?', leader.author).length).toBe(2);
    });
  });

  it('catch-up sends exactly what the client lacks, in ascending seq', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    const all = [];
    for (let seq = 2; seq <= 150; seq++) all.push(await visit(leader, seq));
    client.send({ t: 'evs', items: all.slice(0, 100) });
    client.send({ t: 'evs', items: all.slice(100) });
    await client.drain();
    const other = await authed(group, leader);
    await other.nextOf('have');
    other.send({ t: 'have', heads: { [leader.author]: 120 } });
    expect(items(await other.drain(300)).map((i: any) => decode(i).seq)).toEqual(Array.from({ length: 30 }, (_, i) => 121 + i));
  });

  it('forwards stored envelopes to the other members but not back to the sender', async () => {
    const group = newGroup();
    const { leader, alice, leaderClient, inviteId } = await groupWithInvite(group);
    const aliceClient = await authed(group, alice, { join: await joinEnvelope(group, alice, inviteId, 1) });
    await aliceClient.nextOf('have');
    await leaderClient.drain();
    aliceClient.send({ t: 'evs', items: [await visit(alice, 2)] });
    expect(items(await leaderClient.drain(300)).map((i: any) => decode(i).seq)).toEqual([2]);
    expect(items(await aliceClient.drain(100))).toEqual([]);
    void leader;
  });

  it('a re-seed by the leader admits the members again from their stored joins, without moving their heads', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const alice = await keyFor('alice');
    const inviteId = '01920000-0000-7000-8000-00000000beef';
    const genesis = await envelope(leader, 1, 'group', { group_id: group, name: 'Crew', commander: 'Leader' });
    const inviteEnv = await envelope(leader, 2, 'invite', {
      group_id: group, invite_id: inviteId, token_hash: await sha256hex(TOKEN), expires_at: stamp(3_600_000), label: null,
    });
    const join = await joinEnvelope(group, alice, inviteId, 1);
    const leaderClient = await authed(group, leader, { genesis });
    await leaderClient.nextOf('have');
    leaderClient.send({ t: 'evs', items: [genesis, inviteEnv] });
    leaderClient.send({ t: 'evs', items: [join] }); // the leader offers alice's join from its replica
    const fresh = await leaderClient.nextOf('have');
    expect(fresh.heads[alice.author]).toBe(0);
    const aliceClient = await authed(group, alice);
    expect((await aliceClient.nextOf('have')).heads[alice.author]).toBe(0);
  });

  it('a re-seed admits a join whose invite has expired on the relay clock (S7: expiry is an auth-time rule)', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const alice = await keyFor('alice');
    const inviteId = '01920000-0000-7000-8000-00000000bee0';
    const genesis = await envelope(leader, 1, 'group', { group_id: group, name: 'Crew', commander: 'Leader' });
    const inviteEnv = await envelope(leader, 2, 'invite', {
      group_id: group, invite_id: inviteId, token_hash: await sha256hex(TOKEN), expires_at: stamp(-1000), label: null,
    });
    const join = await envelope(alice, 1, 'join', { group_id: group, invite_id: inviteId, token: TOKEN, commander: 'Alice' }, stamp(-120_000));
    const leaderClient = await authed(group, leader, { genesis });
    await leaderClient.nextOf('have');
    leaderClient.send({ t: 'evs', items: [genesis, inviteEnv] });
    leaderClient.send({ t: 'evs', items: [join] });
    expect((await leaderClient.nextOf('have')).heads[alice.author]).toBe(0);
    expect((await (await authed(group, alice)).nextOf('have')).heads[alice.author]).toBe(0);
  });
});

describe('quotas and removal', () => {
  it('the member quota refuses the rest of that author in the frame', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      instance.exec('INSERT INTO quota (day, author, n) VALUES (?, ?, ?) ON CONFLICT (day, author) DO UPDATE SET n = excluded.n',
        utcDay(Date.now()), leader.author, MEMBER_DAILY - 1);
    });
    client.send({ t: 'evs', items: [await visit(leader, 2), await visit(leader, 3), await visit(leader, 4)] });
    expect(await client.nextOf('err')).toMatchObject({ code: 'quota', author: leader.author, seq: 3 });
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(headOf(instance, leader.author)).toBe(2);
    });
  });

  it('the group quota refuses, the head stays before the hole; roster envelopes are exempt', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      instance.exec('INSERT INTO quota (day, author, n) VALUES (?, ?, ?)', utcDay(Date.now()), 'X'.repeat(43), GROUP_DAILY);
    });
    client.send({ t: 'evs', items: [await visit(leader, 2)] });
    expect(await client.nextOf('err')).toMatchObject({ code: 'group_quota', author: leader.author, seq: 2 });
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(headOf(instance, leader.author)).toBe(1); // the genesis; nothing past the refused seq 2
    });
    // The refusal lasts for the connection; a new one starts clean, and the leader's invite is exempt.
    const second = await authed(group, leader);
    await second.nextOf('have');
    await invite(group, second, leader, 2, TOKEN);
    await second.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(instance.exec('SELECT 1 FROM roster WHERE kind = ?', 'invite').length).toBe(1);
      expect(headOf(instance, leader.author)).toBe(2);
    });
  });

  it('quota rows of past days are logged once as daily totals, then deleted', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      instance.exec('INSERT INTO quota (day, author, n) VALUES (?, ?, ?), (?, ?, ?)',
        utcDay(Date.now()) - 2, leader.author, 7, utcDay(Date.now()) - 2, 'Y'.repeat(43), 5);
    });
    await evictDurableObject(relayFor(group)); // a woken object has not yet seen a frame today
    client.send({ t: 'evs', items: [await visit(leader, 2)] });
    await client.drain();
    client.send({ t: 'evs', items: [await visit(leader, 3)] });
    await client.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(instance.dailyTotals.map((l) => JSON.parse(l))).toEqual([
        { event: 'crew-daily-total', group, day: new Date((utcDay(Date.now()) - 2) * 86_400_000).toISOString().slice(0, 10), envelopes: 12 },
      ]);
      expect(instance.exec('SELECT 1 FROM quota WHERE day < ?', utcDay(Date.now())).length).toBe(0);
    });
  });

  it('removal closes the member and caps what is accepted and served', async () => {
    const group = newGroup();
    const { leader, alice, leaderClient, inviteId } = await groupWithInvite(group);
    const join = await joinEnvelope(group, alice, inviteId, 1);
    const aliceClient = await authed(group, alice, { join });
    await aliceClient.nextOf('have');
    aliceClient.send({ t: 'evs', items: [join, await visit(alice, 2), await visit(alice, 3)] });
    await aliceClient.drain();
    leaderClient.send({ t: 'evs', items: [await envelope(leader, 3, 'remove', { group_id: group, member: alice.author, last_seq: 2 })] });
    expect((await aliceClient.closed).code).toBe(4003);
    const watcher = await authed(group, leader);
    await watcher.nextOf('have');
    watcher.send({ t: 'have', heads: {} });
    const served = items(await watcher.drain(300)).map(decode).filter((e: any) => e.author === alice.author).map((e: any) => e.seq);
    expect(served).toEqual([1, 2]);
    expect((await (await authed(group, alice)).closed).code).toBe(4003);
  });

  it('a removed member cannot upload above the cap and is told so', async () => {
    const group = newGroup();
    const { leader, alice, leaderClient, inviteId } = await groupWithInvite(group);
    const join = await joinEnvelope(group, alice, inviteId, 1);
    const aliceClient = await authed(group, alice, { join });
    await aliceClient.nextOf('have');
    leaderClient.send({ t: 'evs', items: [await envelope(leader, 3, 'remove', { group_id: group, member: alice.author, last_seq: 1 })] });
    await aliceClient.closed;
    const watcher = await authed(group, leader);
    await watcher.nextOf('have');
    watcher.send({ t: 'evs', items: [await visit(alice, 2)] });
    expect(await watcher.nextOf('err')).toMatchObject({ code: 'removed', author: alice.author, seq: 2 });
  });

  it('a non-member envelope is refused with not_member naming author and seq', async () => {
    const group = newGroup();
    const { client } = await createGroupAndUpload(group, await keyFor('leader'));
    const bob = await keyFor('bob');
    await client.drain();
    client.send({ t: 'evs', items: [await visit(bob, 4)] });
    expect(await client.nextOf('err')).toMatchObject({ code: 'not_member', author: bob.author, seq: 4 });
  });

  it('a bad signature is refused and stops that author in the frame', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    const good = await visit(leader, 2);
    const forged = await visit(leader, 3);
    forged.s = good.s;
    client.send({ t: 'evs', items: [forged, await visit(leader, 4)] });
    expect(await client.nextOf('err')).toMatchObject({ code: 'bad_sig', author: leader.author, seq: 3 });
    await client.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(headOf(instance, leader.author)).toBe(1);
    });
  });
});

describe('frames', () => {
  it('bad frames answer bad_frame; oversize frames close with 4008', async () => {
    const group = newGroup();
    const { client } = await createGroupAndUpload(group, await keyFor('leader'));
    await client.drain();
    client.ws.send('not json');
    expect((await client.nextOf('err')).code).toBe('bad_frame');
    client.send({ t: 'evs', items: new Array(101).fill({ e: 'A', s: 'A' }) });
    expect((await client.nextOf('err')).code).toBe('bad_frame');
    client.ws.send('x'.repeat((1 << 20) + 1));
    expect((await client.closed).code).toBe(4008);
  });

  it('the frame size counts UTF-8 bytes, not characters', async () => {
    const { client } = await createGroupAndUpload(newGroup(), await keyFor('leader'));
    await client.drain();
    client.ws.send('é'.repeat(600_000)); // 600,000 characters, 1,200,000 bytes
    expect((await client.closed).code).toBe(4008);
  });

  it('an unknown frame type is ignored', async () => {
    const { client } = await createGroupAndUpload(newGroup(), await keyFor('leader'));
    await client.drain();
    client.send({ t: 'from-the-future' });
    client.send({ t: 'have', heads: {} });
    expect(await client.nextOf('synced')).toEqual({ t: 'synced' });
  });

  it('an oversize item keeps what precedes it, answers too_big and ends the session with 4008', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    client.send({ t: 'evs', items: [await visit(leader, 2), { e: 'A'.repeat(21_847), s: 'A'.repeat(86) }, await visit(leader, 3)] });
    expect((await client.nextOf('err')).code).toBe('too_big');
    expect((await client.closed).code).toBe(4008);
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(headOf(instance, leader.author)).toBe(2); // P2: nothing after the bad item
    });
  });

  it('an undecodable item ends the session with 4008 after the items before it are kept', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    client.send({ t: 'evs', items: [await visit(leader, 2), { e: 'A', s: 'A' }] });
    expect((await client.nextOf('err')).code).toBe('bad_frame');
    expect((await client.closed).code).toBe(4008);
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(headOf(instance, leader.author)).toBe(2);
    });
  });

  it('an item with a good envelope and a bad signature field names author and seq and refuses the author onward', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    const broken = { ...(await visit(leader, 3)), s: 'A' };
    client.send({ t: 'evs', items: [await visit(leader, 2), broken] });
    expect(await client.nextOf('err')).toMatchObject({ code: 'bad_frame', author: leader.author, seq: 3 });
    client.send({ t: 'evs', items: [await visit(leader, 4)] });
    await client.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(headOf(instance, leader.author)).toBe(2);
    });
  });

  it('binary frames close with the reason binary', async () => {
    const { client } = await createGroupAndUpload(newGroup(), await keyFor('leader'));
    await client.drain();
    client.ws.send(new Uint8Array([1, 2, 3]));
    expect(await client.closed).toEqual({ code: 4008, reason: 'binary' });
  });

  it('evs items totalling more than 512 KiB answer too_big and end the session with 4008', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    // 25 items of about 21.9k characters each: every item is within its cap, the sum is not.
    const big = { e: 'A'.repeat(21_846), s: 'A'.repeat(86) };
    client.send({ t: 'evs', items: new Array(25).fill(big) });
    expect((await client.nextOf('err')).code).toBe('too_big');
    expect((await client.closed).code).toBe(4008);
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(headOf(instance, leader.author)).toBe(1);
    });
  });
});

describe('refusals last for the connection (S7, S14)', () => {
  async function exhausted(group: string) {
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      instance.exec('INSERT INTO quota (day, author, n) VALUES (?, ?, ?)', utcDay(Date.now()), leader.author, MEMBER_DAILY - 1);
    });
    return { leader, client };
  }
  const logSeqs = (instance: GroupRelay, author: string) =>
    instance.exec<{ seq: number }>('SELECT seq FROM log WHERE author = ? ORDER BY seq', author).map((r) => r.seq);

  it('P1: a quota refusal in one frame also refuses the leader\'s roster envelope in the next', async () => {
    const group = newGroup();
    const { leader, client } = await exhausted(group);
    client.send({ t: 'evs', items: [await visit(leader, 2), await visit(leader, 3), await visit(leader, 4)] });
    client.send({ t: 'evs', items: [await envelope(leader, 5, 'invite', {
      group_id: group, invite_id: '01920000-0000-7000-8000-00000000cc01', token_hash: await sha256hex(TOKEN),
      expires_at: stamp(3_600_000), label: null,
    }), await visit(leader, 6)] });
    await client.drain(300);
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(logSeqs(instance, leader.author)).toEqual([1, 2]);
      expect(headOf(instance, leader.author)).toBe(2);
    });
  });

  it('P5: a bad signature refuses the author\'s later frames as well', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    const forged = await visit(leader, 3);
    forged.s = (await visit(leader, 2)).s;
    client.send({ t: 'evs', items: [await visit(leader, 2), forged] });
    client.send({ t: 'evs', items: [await envelope(leader, 5, 'invite', {
      group_id: group, invite_id: '01920000-0000-7000-8000-00000000cc02', token_hash: await sha256hex(TOKEN),
      expires_at: stamp(3_600_000), label: null,
    }), await visit(leader, 6)] });
    await client.drain(300);
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(logSeqs(instance, leader.author)).toEqual([1, 2]);
    });
  });

  it('a daily refusal ends with the UTC day', async () => {
    const group = newGroup();
    const { leader, client } = await exhausted(group);
    client.send({ t: 'evs', items: [await visit(leader, 2), await visit(leader, 3)] });
    expect((await client.nextOf('err')).code).toBe('quota');
    await runInDurableObject(relayFor(group), async (_i: GroupRelay, state) => {
      for (const ws of state.getWebSockets()) {
        const att = ws.deserializeAttachment();
        if (att.refused) {
          for (const k of Object.keys(att.refused)) att.refused[k][1] = utcDay(Date.now()) - 1;
          ws.serializeAttachment(att);
        }
      }
    });
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      instance.exec('DELETE FROM quota');
    });
    client.send({ t: 'evs', items: [await visit(leader, 3)] });
    await client.drain(300);
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(logSeqs(instance, leader.author)).toEqual([1, 2, 3]);
    });
  });

  it('P6: a member\'s self-signed invite-kind envelopes count toward the quota and write no roster row', async () => {
    const group = newGroup();
    const { alice, leaderClient, inviteId } = await groupWithInvite(group);
    const join = await joinEnvelope(group, alice, inviteId, 1);
    const aliceClient = await authed(group, alice, { join });
    await aliceClient.nextOf('have');
    await leaderClient.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      instance.exec('INSERT INTO quota (day, author, n) VALUES (?, ?, ?)', utcDay(Date.now()), alice.author, MEMBER_DAILY);
    });
    const spam = [];
    for (let seq = 2; seq < 101; seq++) {
      spam.push(await envelope(alice, seq, 'invite', {
        group_id: group, invite_id: '01920000-0000-7000-8000-00000000dd01', token_hash: await sha256hex(TOKEN),
        expires_at: stamp(3_600_000), label: null,
      }));
    }
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => { instance.writes = 0; });
    aliceClient.send({ t: 'evs', items: spam });
    expect(await aliceClient.nextOf('err')).toMatchObject({ code: 'quota', author: alice.author, seq: 2 });
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(instance.writes).toBe(0);
      expect(instance.exec('SELECT 1 FROM roster WHERE author = ?', alice.author).length).toBe(1); // the join only
    });
  });
});

describe('connection handling', () => {
  it('P4: a stale unauthenticated socket that sends auth is closed and does not replace the live connection', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client: live } = await createGroupAndUpload(group, leader);
    await live.drain();
    const stale = await open(group);
    const hello = await stale.nextOf('hello');
    await runInDurableObject(relayFor(group), async (_i: GroupRelay, state) => {
      for (const ws of state.getWebSockets()) {
        const att = ws.deserializeAttachment();
        if (att.state === 'hello') ws.serializeAttachment({ ...att, openedAt: Date.now() - 11_000 });
      }
    });
    const sig = await leader.sign(new TextEncoder().encode(`edmrn-crew-auth\n${group}\n${hello.nonce}`));
    stale.send({ t: 'auth', group, author: leader.author, sig: b64urlEncode(sig) });
    expect((await stale.closed).code).toBe(4001);
    const outcome = await Promise.race([live.closed.then(() => 'closed'), new Promise((r) => setTimeout(() => r('open'), 300))]);
    expect(outcome).toBe('open');
  });

  it('the serial chain keeps a have behind a slow auth (the signature check yields)', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const genesis = await envelope(leader, 1, 'group', { group_id: group, name: 'Crew', commander: 'L' });
    const client = await open(group);
    const hello = await client.nextOf('hello');
    const sig = await leader.sign(new TextEncoder().encode(`edmrn-crew-auth\n${group}\n${hello.nonce}`));
    const real = crypto.subtle.verify.bind(crypto.subtle);
    Object.defineProperty(crypto.subtle, 'verify', {
      configurable: true,
      value: async (...args: Parameters<typeof real>) => {
        await new Promise((r) => setTimeout(r, 50));
        return real(...args);
      },
    });
    try {
      client.send({ t: 'auth', group, author: leader.author, sig: b64urlEncode(sig), genesis });
      client.send({ t: 'have', heads: {} });
      expect((await client.next()).t).toBe('have');
      expect(await client.nextOf('synced')).toEqual({ t: 'synced' });
    } finally {
      Object.defineProperty(crypto.subtle, 'verify', { configurable: true, value: real });
    }
  });

  it('the relay\'s have caps a removed member\'s head at last_seq', async () => {
    const group = newGroup();
    const { leader, alice, leaderClient, inviteId } = await groupWithInvite(group);
    const join = await joinEnvelope(group, alice, inviteId, 1);
    const aliceClient = await authed(group, alice, { join });
    await aliceClient.nextOf('have');
    aliceClient.send({ t: 'evs', items: [join, await visit(alice, 2), await visit(alice, 3)] });
    await aliceClient.drain();
    leaderClient.send({ t: 'evs', items: [await envelope(leader, 3, 'remove', { group_id: group, member: alice.author, last_seq: 2 })] });
    await aliceClient.closed;
    const again = await authed(group, leader);
    expect((await again.nextOf('have')).heads[alice.author]).toBe(2);
  });
});
