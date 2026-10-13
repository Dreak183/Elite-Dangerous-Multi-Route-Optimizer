import { env } from 'cloudflare:workers';
import { SELF } from 'cloudflare:test';
import raw_vectors from './vectors.json?raw';
import { b64urlEncode } from '../src/b64';
import { canonicalJson } from '../src/canonical';
import type { GroupRelay } from '../src/group';
import { sha256hex } from '../src/roster';

// Not a JSON import: the file carries a lone-surrogate escape the bundler refuses.
const v = JSON.parse(raw_vectors) as any;
const PKCS8_ED25519_PREFIX = [0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20];

export { sha256hex };

export interface Key {
  author: string;
  sign(data: Uint8Array): Promise<Uint8Array>;
}

export async function keyFor(name: 'leader' | 'alice' | 'bob' | 'carol' | 'mallory'): Promise<Key> {
  const seed = new Uint8Array(v.keys[name].seed.match(/../g).map((h: string) => parseInt(h, 16)));
  const pkcs8 = new Uint8Array([...PKCS8_ED25519_PREFIX, ...seed]);
  const key = await crypto.subtle.importKey('pkcs8', pkcs8, { name: 'Ed25519' }, false, ['sign']);
  return {
    author: v.keys[name].author,
    sign: async (data) => new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, key, data)),
  };
}

let counter = 0x1000;
export function uuid7(): string {
  counter += 1;
  return `01920000-0000-7000-8000-${counter.toString(16).padStart(12, '0')}`;
}

export function newGroup(): string {
  return uuid7();
}

/** A stamp `offsetMs` from now, in the envelope shape. */
export function stamp(offsetMs = 0): string {
  return new Date(Date.now() + offsetMs).toISOString().replace(/\.\d{3}Z$/, 'Z');
}

export interface Item { e: string; s: string; }

export async function envelope(key: Key, seq: number, kind: string, payload: Record<string, unknown>,
                               createdAt = stamp(-60_000), eventId = uuid7()): Promise<Item> {
  const body = { v: 1, event_id: eventId, author: key.author, seq, kind, created_at: createdAt, payload };
  const bytes = new TextEncoder().encode(canonicalJson(body));
  return { e: b64urlEncode(bytes), s: b64urlEncode(await key.sign(bytes)) };
}

export class Client {
  private queue: any[] = [];
  private waiters: ((m: any) => void)[] = [];
  readonly closed: Promise<{ code: number; reason: string }>;

  constructor(readonly ws: WebSocket) {
    ws.addEventListener('message', (event) => {
      const message = JSON.parse(event.data as string);
      const waiter = this.waiters.shift();
      if (waiter) waiter(message);
      else this.queue.push(message);
    });
    this.closed = new Promise((resolve) =>
      ws.addEventListener('close', (event) => resolve({ code: event.code, reason: event.reason })));
  }

  next(timeoutMs = 2000): Promise<any> {
    const queued = this.queue.shift();
    if (queued) return Promise.resolve(queued);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('no message')), timeoutMs);
      this.waiters.push((m) => { clearTimeout(timer); resolve(m); });
    });
  }

  async nextOf(t: string, timeoutMs = 2000): Promise<any> {
    for (;;) {
      const message = await this.next(timeoutMs);
      if (message.t === t) return message;
    }
  }

  /** Every message that arrives within `ms`. */
  async drain(ms = 200): Promise<any[]> {
    await new Promise((r) => setTimeout(r, ms));
    const out = this.queue;
    this.queue = [];
    return out;
  }

  send(frame: unknown): void {
    this.ws.send(JSON.stringify(frame));
  }
}

export async function open(group: string): Promise<Client> {
  const res = await SELF.fetch(`https://relay.test/v1/groups/${group}/ws`, { headers: { Upgrade: 'websocket' } });
  const ws = res.webSocket!;
  ws.accept();
  return new Client(ws);
}

export async function authed(group: string, key: Key, extra: Record<string, unknown> = {}): Promise<Client> {
  const client = await open(group);
  const hello = await client.nextOf('hello');
  const sig = await key.sign(new TextEncoder().encode(`edmrn-crew-auth\n${group}\n${hello.nonce}`));
  client.send({ t: 'auth', group, author: key.author, sig: b64urlEncode(sig), ...extra });
  return client;
}

export function relayFor(group: string): DurableObjectStub<GroupRelay> {
  const groups = (env as any).GROUPS as DurableObjectNamespace<GroupRelay>;
  return groups.get(groups.idFromName(group));
}

export async function invite(group: string, leader: Client, leaderKey: Key, seq: number, token: string,
                             expiresInMs = 3_600_000): Promise<string> {
  const inviteId = uuid7();
  leader.send({ t: 'evs', items: [await envelope(leaderKey, seq, 'invite', {
    group_id: group, invite_id: inviteId, token_hash: await sha256hex(token),
    expires_at: stamp(expiresInMs), label: null,
  })] });
  return inviteId;
}

/**
 * Stores the genesis at auth, which leaves the leader's head at 0 (S7), then uploads it through `evs`,
 * as the real client does: Task 6 uploads every envelope above the relay's head.
 */
export async function createGroupAndUpload(group: string, leader: Key): Promise<{ client: Client; genesis: Item }> {
  const genesis = await envelope(leader, 1, 'group', { group_id: group, name: 'Crew', commander: 'Leader' });
  const client = await authed(group, leader, { genesis });
  await client.nextOf('have');
  client.send({ t: 'have', heads: {} });
  await client.nextOf('synced');
  client.send({ t: 'evs', items: [genesis] });
  return { client, genesis };
}
