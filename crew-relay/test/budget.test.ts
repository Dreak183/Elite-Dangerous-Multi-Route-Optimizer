import { runInDurableObject } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';
import { utcDay, type GroupRelay } from '../src/group';
import { createGroupAndUpload, envelope, keyFor, newGroup, relayFor } from './helpers';

async function writes(group: string): Promise<number> {
  let n = 0;
  await runInDurableObject(relayFor(group), async (instance: GroupRelay) => { n = instance.writes; });
  return n;
}

const visit = (key: any, seq: number) => envelope(key, seq, 'visit', { commander: 'A', system: 'Sol', address: String(seq), coords: null });

describe('row-write budget (spec B1 S13)', () => {
  it('one envelope in a frame costs log + heads + quota = 3 rows', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    const before = await writes(group);
    client.send({ t: 'evs', items: [await visit(leader, 2)] });
    await client.drain();
    expect((await writes(group)) - before).toBe(3);
  });

  it('a frame of 100 envelopes costs 102 rows', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    const items = [];
    for (let seq = 2; seq < 102; seq++) items.push(await visit(leader, seq));
    const before = await writes(group);
    client.send({ t: 'evs', items });
    await client.drain(400);
    expect((await writes(group)) - before).toBe(102);
  });

  it('a duplicate envelope costs nothing', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    const item = await visit(leader, 2);
    client.send({ t: 'evs', items: [item] });
    await client.drain();
    const before = await writes(group);
    client.send({ t: 'evs', items: [item] });
    await client.drain();
    expect((await writes(group)) - before).toBe(0);
  });

  it('a status costs no SQLite row until its socket closes, then one', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    const { b64urlEncode } = await import('../src/b64');
    const { canonicalJson } = await import('../src/canonical');
    const doc = { author: leader.author, at: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'), status: { commander: null, system: null, address: null, coords: null, ship: null, ship_name: null, ship_ident: null, edsy_url: null, hull: null, fuel_main: null, fuel_reserve: null, fuel_capacity_main: null, fuel_capacity_reserve: null, paused: true } };
    const bytes = new TextEncoder().encode(canonicalJson(doc));
    const before = await writes(group);
    client.send({ t: 'st', e: b64urlEncode(bytes), s: b64urlEncode(await leader.sign(bytes)) });
    await client.drain();
    expect((await writes(group)) - before).toBe(0);
    client.ws.close(1000, 'bye');
    await client.closed;
    await new Promise((r) => setTimeout(r, 100));
    expect((await writes(group)) - before).toBe(1);
  });

  it('past quota days are logged once as daily totals and deleted', async () => {
    const group = newGroup();
    const leader = await keyFor('leader');
    const { client } = await createGroupAndUpload(group, leader);
    await client.drain();
    const today = utcDay(Date.now());
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      instance.exec('INSERT INTO quota (day, author, n) VALUES (?, ?, ?), (?, ?, ?)', today - 1, leader.author, 7, today - 2, leader.author, 3);
      (instance as any).cleanedDay = -1;
    });
    client.send({ t: 'evs', items: [await visit(leader, 2)] });
    await client.drain();
    await runInDurableObject(relayFor(group), async (instance: GroupRelay) => {
      expect(instance.dailyTotals.length).toBe(2);
      expect(instance.exec('SELECT 1 FROM quota WHERE day < ?', today).length).toBe(0);
    });
  });
});
