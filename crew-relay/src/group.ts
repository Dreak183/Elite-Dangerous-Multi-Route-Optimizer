// One Durable Object per crew group (spec B1 S7, S8, S13, S14, S18). SQLite storage, hibernatable
// WebSockets. Rows written are the Free plan's binding limit: the log is WITHOUT ROWID with one key,
// heads and quota are written once per frame and author, status once per closed socket.
//
// Any syntactically valid UUIDv7 reaches an instance, so an instance writes nothing at all, not even
// its tables, until a valid genesis from a CREW_LEADERS author arrives: random group ids must not
// burn the account's rows-per-day budget.
//
// Roster plausibility (`created_at` window) is judged once, at insert, with the relay's clock; the
// clients judge again each time they recompute. The relay is the stricter side by construction: a
// roster envelope it refuses never reaches a client, and one it stored is re-judged by clients
// only to the same or a stricter result, so the divergence can end in a 4003, never in an extra
// admission.
import { DurableObject } from 'cloudflare:workers';
import type { Env } from './index';
import { b64urlDecode, b64urlEncode } from './b64';
import { AUTHOR, MAX_ENVELOPE_BYTES, parseEnvelope, verifySignature, type Envelope } from './envelope';
import { ROSTER_KINDS, admits, computeRoster, realStamp, rosterEntry, type Roster, type RosterEntry } from './roster';

export const MAX_FRAME_CHARS = 1 << 20; // a limit in bytes of UTF-8 text, despite the name
export const MAX_EVS_ITEMS = 100;
export const MAX_EVS_CHARS = 512 * 1024;
export const MAX_STATUS_BYTES = 8 * 1024;
export const MEMBER_DAILY = 5000;
export const GROUP_DAILY = 80000;
export const STATUS_INTERVAL_MS = 30_000;
export const AUTH_GRACE_MS = 10_000;
export const CLOSE_REPLACED = 4000;
export const CLOSE_BAD_AUTH = 4001;
export const CLOSE_FORBIDDEN = 4003;
export const CLOSE_POLICY = 4008;
const ITEM_OVERHEAD = 16; // {"e":"","s":""}, per item, as the client counts it
const MAX_E_CHARS = Math.ceil((MAX_ENVELOPE_BYTES * 4) / 3);
const MAX_S_CHARS = 86; // 64 bytes
// An attachment holds at most 16,384 bytes (knowledge/group-sync-transport.md). Worst case here: 64 refusals
// of about 70 bytes (4.5 KB) plus a status of 8 KiB of canonical bytes, base64url about 11 KB: under the limit.
const MAX_REFUSALS = 64;
const DAY_MS = 86_400_000;

export function utcDay(ms: number): number {
  return Math.floor(ms / DAY_MS);
}

interface Item { e: string; s: string; }

interface Attachment {
  state: 'hello' | 'member';
  group: string;
  nonce: string;
  openedAt: number;
  author?: string;
  statusAt?: number;
  /** author -> [first refused seq, UTC day for a daily refusal or 0]: kept for the connection. */
  refused?: Record<string, [number, number]>;
  /** Set before the relay closes the socket: frames already queued behind the close are not processed. */
  closing?: boolean;
  status?: Item;
}

/** An envelope that passed the structure checks (the signature is checked separately). */
interface Checked {
  item: Item;
  e: Uint8Array;
  s: Uint8Array;
  env: Envelope;
  text: string;
}

const SCHEMA = [
  'CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL) WITHOUT ROWID',
  'CREATE TABLE log (author TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, env BLOB NOT NULL, sig BLOB NOT NULL, PRIMARY KEY (author, seq)) WITHOUT ROWID',
  'CREATE TABLE heads (author TEXT PRIMARY KEY, seq INTEGER NOT NULL) WITHOUT ROWID',
  'CREATE TABLE quota (day INTEGER NOT NULL, author TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (day, author)) WITHOUT ROWID',
  'CREATE TABLE roster (author TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (author, seq)) WITHOUT ROWID',
  'CREATE TABLE status (author TEXT PRIMARY KEY, e TEXT NOT NULL, s TEXT NOT NULL) WITHOUT ROWID',
];

const encoder = new TextEncoder();

/** A BLOB binding that owns exactly its bytes. */
function blob(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

/** Size in UTF-8 bytes, without encoding when the length alone decides. */
function utf8Size(text: string): number {
  return text.length * 3 <= MAX_FRAME_CHARS ? text.length : encoder.encode(text).length;
}

export class GroupRelay extends DurableObject<Env> {
  private sql: SqlStorage;
  private ready: boolean;
  private roster: Roster | null = null;
  private rosterLoaded = false;
  private cleanedDay = -1;
  private chain: Promise<unknown> = Promise.resolve();
  writes = 0;
  dailyTotals: string[] = [];

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.ready = this.sql.exec("SELECT 1 FROM sqlite_master WHERE name = 'meta'").toArray().length > 0;
  }

  exec<T = Record<string, SqlStorageValue>>(query: string, ...bindings: unknown[]): T[] {
    const cursor = this.sql.exec(query, ...(bindings as SqlStorageValue[]));
    const rows = cursor.toArray() as T[];
    this.writes += cursor.rowsWritten;
    return rows;
  }

  /** Tables exist from the first valid genesis on, not before (see the header). */
  private ensureSchema(): void {
    if (this.ready) return;
    for (const statement of SCHEMA) this.exec(statement);
    this.ready = true;
  }

  // --- WebSocket entry -----------------------------------------------------------------

  /** Does not look at the method: the runtime routes an upgrade request as GET whatever it was sent as. */
  async fetch(request: Request): Promise<Response> {
    if (request.headers.get('Upgrade')?.toLowerCase() !== 'websocket') {
      return new Response('expected a WebSocket upgrade\n', { status: 426 });
    }
    const group = new URL(request.url).pathname.split('/')[3] ?? '';
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.ctx.acceptWebSocket(server);
    const nonce = b64urlEncode(crypto.getRandomValues(new Uint8Array(32)));
    const attachment: Attachment = { state: 'hello', group, nonce, openedAt: Date.now() };
    server.serializeAttachment(attachment);
    server.send(JSON.stringify({ t: 'hello', v: 1, source: this.env.SOURCE_URL || '', nonce }));
    return new Response(null, { status: 101, webSocket: client });
  }

  /**
   * Handlers run one at a time. Signature checks are asynchronous and do not close the runtime's
   * input gate, so without this a `have` sent right behind `auth` would overtake it.
   */
  private serial(work: () => Promise<void>): Promise<void> {
    const run = this.chain.then(work);
    this.chain = run.catch(() => undefined);
    return run;
  }

  webSocketMessage(ws: WebSocket, message: string | ArrayBuffer): Promise<void> {
    return this.serial(async () => {
      try {
        await this.handle(ws, message);
      } catch (e) {
        console.error('group relay frame failed', e);
        try {
          this.shut(ws, 1011, 'internal error');
        } catch {
          // already closed
        }
      }
    });
  }

  /** Closes a socket and marks it first, so frames queued behind the close find the mark and stop. */
  private shut(ws: WebSocket, code: number, reason: string): void {
    try {
      const att = ws.deserializeAttachment() as Attachment | null;
      if (att) ws.serializeAttachment({ ...att, closing: true } satisfies Attachment);
    } catch {
      // already closed
    }
    try {
      ws.close(code, reason);
    } catch {
      // already closed
    }
  }

  private async handle(ws: WebSocket, message: string | ArrayBuffer): Promise<void> {
    this.sweep();
    const stale = ws.deserializeAttachment() as Attachment | null;
    if (stale?.closing) return;
    if (stale?.state === 'hello' && Date.now() - stale.openedAt > AUTH_GRACE_MS) return; // just closed by the sweep
    if (typeof message !== 'string') {
      this.shut(ws, CLOSE_POLICY, 'binary');
      return;
    }
    if (utf8Size(message) > MAX_FRAME_CHARS) {
      this.shut(ws, CLOSE_POLICY, 'too_big');
      return;
    }
    let frame: any;
    try {
      frame = JSON.parse(message);
    } catch {
      return this.error(ws, 'bad_frame');
    }
    if (typeof frame !== 'object' || frame === null || typeof frame.t !== 'string') return this.error(ws, 'bad_frame');
    const att = ws.deserializeAttachment() as Attachment | null;
    if (!att) return;
    if (att.state === 'hello') {
      if (frame.t === 'auth') await this.auth(ws, att, frame);
      else this.shut(ws, CLOSE_BAD_AUTH, 'auth first');
      return;
    }
    if (frame.t === 'have') await this.catchUp(ws, frame);
    else if (frame.t === 'evs') await this.receive(ws, frame);
    else if (frame.t === 'st') await this.status(ws, att, frame);
  }

  webSocketClose(ws: WebSocket, code: number, reason: string): Promise<void> {
    return this.serial(async () => {
      this.persistStatus(ws);
      try {
        ws.close(code === 1000 || (code >= 3000 && code <= 4999) ? code : 1000, reason);
      } catch {
        // already closed
      }
    });
  }

  webSocketError(ws: WebSocket): Promise<void> {
    return this.serial(async () => this.persistStatus(ws));
  }

  // --- helpers ---------------------------------------------------------------------------

  private error(ws: WebSocket, code: string, author?: string, seq?: number): void {
    const frame: Record<string, unknown> = { t: 'err', code };
    if (author !== undefined) frame.author = author;
    if (seq !== undefined) frame.seq = seq;
    try {
      ws.send(JSON.stringify(frame));
    } catch {
      // the socket closed meanwhile
    }
  }

  private members(): WebSocket[] {
    return this.ctx.getWebSockets().filter((ws) => (ws.deserializeAttachment() as Attachment | null)?.state === 'member');
  }

  private broadcast(except: WebSocket | null, frame: unknown): void {
    const text = JSON.stringify(frame);
    for (const ws of this.members()) {
      if (ws === except) continue;
      try {
        ws.send(text);
      } catch {
        // closing socket
      }
    }
  }

  /** `evs` frames of at most 100 items and 512 KiB of items. */
  private sendItems(send: (frame: unknown) => void, items: Item[]): void {
    let batch: Item[] = [];
    let size = 0;
    for (const item of items) {
      const itemSize = item.e.length + item.s.length + ITEM_OVERHEAD;
      if (batch.length && (batch.length >= MAX_EVS_ITEMS || size + itemSize > MAX_EVS_CHARS)) {
        send({ t: 'evs', items: batch });
        batch = [];
        size = 0;
      }
      batch.push(item);
      size += itemSize;
    }
    if (batch.length) send({ t: 'evs', items: batch });
  }

  private sweep(): void {
    const now = Date.now();
    for (const ws of this.ctx.getWebSockets()) {
      const att = ws.deserializeAttachment() as Attachment | null;
      if (att?.state === 'hello' && now - att.openedAt > AUTH_GRACE_MS) this.shut(ws, CLOSE_BAD_AUTH, 'auth timeout');
    }
  }

  private meta(): { group: string; leader: string } | null {
    if (!this.ready) return null;
    const rows = this.exec<{ k: string; v: string }>('SELECT k, v FROM meta');
    const map = Object.fromEntries(rows.map((r) => [r.k, r.v]));
    return map.group && map.leader ? { group: map.group, leader: map.leader } : null;
  }

  private leaders(): Set<string> {
    return new Set((this.env.CREW_LEADERS || '').split(/[\s,]+/).filter(Boolean));
  }

  private rosterEntries(): RosterEntry[] {
    if (!this.ready) return [];
    return this.exec<{ author: string; seq: number; kind: string; created_at: string; payload: string }>(
      'SELECT author, seq, kind, created_at, payload FROM roster',
    ).map((r) => ({ author: r.author, seq: r.seq, kind: r.kind, created_at: r.created_at, payload: JSON.parse(r.payload) }));
  }

  private async currentRoster(): Promise<Roster | null> {
    if (this.rosterLoaded) return this.roster;
    const meta = this.meta();
    this.roster = meta ? await computeRoster(this.rosterEntries(), meta.group, meta.leader) : null;
    this.rosterLoaded = true;
    return this.roster;
  }

  private insertRoster(entry: RosterEntry): boolean {
    const before = this.writes;
    this.exec('INSERT OR IGNORE INTO roster (author, seq, kind, created_at, payload) VALUES (?, ?, ?, ?, ?)',
      entry.author, entry.seq, entry.kind, entry.created_at, JSON.stringify(entry.payload));
    return this.writes > before;
  }

  private storeLog(c: Checked): void {
    this.exec('INSERT OR IGNORE INTO log (author, seq, kind, env, sig) VALUES (?, ?, ?, ?, ?)',
      c.env.author, c.env.seq, c.env.kind, blob(c.e), blob(c.s));
  }

  private heads(): Map<string, number> {
    if (!this.ready) return new Map();
    return new Map(this.exec<{ author: string; seq: number }>('SELECT author, seq FROM heads').map((r) => [r.author, r.seq]));
  }

  /** Decode and structure-check one item. 'size' marks an item over the size caps, null a malformed one. */
  private decode(item: any): Checked | 'size' | { bad: true; env?: Envelope } | null {
    if (typeof item !== 'object' || item === null || typeof item.e !== 'string' || typeof item.s !== 'string') return { bad: true };
    if (item.e.length > MAX_E_CHARS || item.s.length > MAX_S_CHARS) return 'size';
    const e = b64urlDecode(item.e, MAX_ENVELOPE_BYTES);
    const parsed = e ? parseEnvelope(e) : null;
    if (!e || !parsed) return { bad: true };
    const s = b64urlDecode(item.s, 64);
    // The envelope parses, so its author and seq are known and the err can name them.
    if (!s || s.length !== 64) return { bad: true, env: parsed.env };
    return { item: { e: item.e, s: item.s }, e, s, env: parsed.env, text: parsed.text };
  }

  // --- auth ------------------------------------------------------------------------------

  /** A genesis or join presented with auth, checked but not stored (S7). */
  private async checkAuthEnvelope(raw: any, author: string, kind: string, group: string): Promise<{ c: Checked; entry: RosterEntry } | null> {
    const c = this.decode(raw);
    if (c === null || c === 'size' || 'bad' in c || c.env.author !== author || c.env.kind !== kind) return null;
    const entry = rosterEntry(c.env, c.text, Date.now());
    if (!entry || entry.payload.group_id !== group) return null;
    if (!(await verifySignature(author, c.s, c.e))) return null;
    return { c, entry };
  }

  /** Stored in the log and the roster, without moving the head (S7). */
  private storeAuthEnvelope(c: Checked, entry: RosterEntry): void {
    this.storeLog(c);
    this.insertRoster(entry);
    this.rosterLoaded = false;
  }

  private async auth(ws: WebSocket, att: Attachment, frame: any): Promise<void> {
    const { group, author } = frame;
    if (group !== att.group || typeof author !== 'string' || !AUTHOR.test(author)) return this.shut(ws, CLOSE_BAD_AUTH, 'bad auth');
    const sig = b64urlDecode(frame.sig, 64);
    const message = encoder.encode(`edmrn-crew-auth\n${group}\n${att.nonce}`);
    if (!sig || sig.length !== 64 || !(await verifySignature(author, sig, message))) return this.shut(ws, CLOSE_BAD_AUTH, 'bad signature');
    let meta = this.meta();
    if (!meta) {
      // An empty group: only a valid genesis from a listed leader creates it, and nothing is written before.
      const genesis = this.leaders().has(author) ? await this.checkAuthEnvelope(frame.genesis, author, 'group', group) : null;
      if (!genesis) return this.shut(ws, CLOSE_FORBIDDEN, 'no group');
      this.ensureSchema();
      this.exec('INSERT INTO meta (k, v) VALUES (?, ?), (?, ?)', 'group', group, 'leader', author);
      this.storeAuthEnvelope(genesis.c, genesis.entry);
      meta = { group, leader: author };
    }
    const before = new Set((await this.currentRoster())?.members.keys() ?? []);
    let joined = false;
    if (frame.join && meta.leader !== author && !before.has(author)) {
      joined = await this.admitJoin(frame.join, author, group, meta.leader);
    }
    const roster = await this.currentRoster();
    const member = roster?.members.get(author);
    if (!roster || !member) return this.shut(ws, CLOSE_FORBIDDEN, 'not_member');
    if (member.lastSeq !== null) return this.shut(ws, CLOSE_FORBIDDEN, 'removed');
    for (const other of this.members()) {
      if (other !== ws && (other.deserializeAttachment() as Attachment).author === author) this.shut(other, CLOSE_REPLACED, 'replaced');
    }
    ws.serializeAttachment({ ...att, state: 'member', author, statusAt: 0 } satisfies Attachment);
    if (joined) this.broadcast(ws, { t: 'have', heads: this.listedHeads(roster) });
    ws.send(JSON.stringify({ t: 'have', heads: this.listedHeads(roster) }));
    this.sendStatuses(ws, author);
  }

  /** A join at auth: it must admit its author under S2 and the invite must not be expired on the relay clock. */
  private async admitJoin(raw: any, author: string, group: string, leader: string): Promise<boolean> {
    const checked = await this.checkAuthEnvelope(raw, author, 'join', group);
    if (!checked) return false;
    const invite = this.rosterEntries().find((r) => r.kind === 'invite' && r.author === leader && r.payload.invite_id === checked.entry.payload.invite_id);
    const expires = invite ? realStamp(invite.payload.expires_at) : null;
    if (expires === null || Date.now() > expires) return false;
    const candidate = await computeRoster([...this.rosterEntries(), checked.entry], group, leader);
    if (!candidate.members.has(author)) return false;
    this.storeAuthEnvelope(checked.c, checked.entry);
    return true;
  }

  /** Heads of every member, 0 for a member the relay holds nothing of (S7). */
  private listedHeads(roster: Roster): Record<string, number> {
    const heads = this.heads();
    const listed: Record<string, number> = {};
    // The stored head, uncapped: a client holding a removed member's envelopes above last_seq would otherwise
    // upload them on every connection and draw `removed` errs. Catch-up still serves only up to the cap.
    for (const a of roster.members.keys()) listed[a] = heads.get(a) ?? 0;
    return listed;
  }

  /** The roster with a non-member's `join` added, if that join admits its author under S2 (S7). */
  private async withJoin(c: Checked, now: number, roster: Roster, pending: RosterEntry[]): Promise<{ roster: Roster; entry: RosterEntry } | null> {
    const entry = rosterEntry(c.env, c.text, now);
    if (!entry || entry.payload.group_id !== roster.group) return null;
    const candidate = await computeRoster([...this.rosterEntries(), ...pending, entry], roster.group, roster.leader);
    return candidate.members.has(c.env.author) ? { roster: candidate, entry } : null;
  }

  // --- catch-up ----------------------------------------------------------------------------

  private async catchUp(ws: WebSocket, frame: any): Promise<void> {
    const theirs = frame.heads;
    if (typeof theirs !== 'object' || theirs === null || Array.isArray(theirs) || Object.keys(theirs).length > 1000) {
      return this.error(ws, 'bad_frame');
    }
    const roster = await this.currentRoster();
    const send = (f: unknown) => ws.send(JSON.stringify(f));
    for (const [author, relayHead] of this.heads()) {
      const cap = roster?.members.get(author)?.lastSeq;
      const until = cap === null || cap === undefined ? relayHead : Math.min(relayHead, cap);
      let after = Number.isInteger(theirs[author]) && theirs[author] >= 0 ? theirs[author] : 0;
      while (after < until) {
        const rows = this.exec<{ seq: number; env: ArrayBuffer; sig: ArrayBuffer }>(
          'SELECT seq, env, sig FROM log WHERE author = ? AND seq > ? AND seq <= ? ORDER BY seq LIMIT ?',
          author, after, until, MAX_EVS_ITEMS);
        if (!rows.length) break;
        this.sendItems(send, rows.map((r) => ({ e: b64urlEncode(new Uint8Array(r.env)), s: b64urlEncode(new Uint8Array(r.sig)) })));
        after = rows[rows.length - 1].seq;
      }
    }
    send({ t: 'synced' });
  }

  // --- receiving envelopes ----------------------------------------------------------------

  private cleanup(day: number): void {
    if (this.cleanedDay === day) return;
    this.cleanedDay = day;
    const past = this.exec<{ day: number; n: number }>('SELECT day, SUM(n) AS n FROM quota WHERE day < ? GROUP BY day', day);
    if (!past.length) return;
    const group = this.meta()?.group ?? '';
    for (const row of past) {
      const line = JSON.stringify({ event: 'crew-daily-total', group, day: new Date(row.day * DAY_MS).toISOString().slice(0, 10), envelopes: row.n });
      console.log(line);
      this.dailyTotals.push(line);
    }
    this.exec('DELETE FROM quota WHERE day < ?', day);
  }

  private async receive(ws: WebSocket, frame: any): Promise<void> {
    const items = frame.items;
    if (!Array.isArray(items) || items.length > MAX_EVS_ITEMS) return this.error(ws, 'bad_frame');
    let total = 0;
    for (const item of items) {
      if (typeof item?.e === 'string' && typeof item?.s === 'string') total += item.e.length + item.s.length + ITEM_OVERHEAD;
    }
    if (total > MAX_EVS_CHARS) {
      // No author is known, so the client cannot roll its head back: end the session, the next `have` is authoritative.
      this.error(ws, 'too_big');
      return this.shut(ws, CLOSE_POLICY, 'too_big');
    }
    let roster = await this.currentRoster();
    if (!roster) return;
    const att = ws.deserializeAttachment() as Attachment;
    const membersBefore = new Set(roster.members.keys());
    const now = Date.now();
    const day = utcDay(now);
    this.cleanup(day);
    let groupLeft = GROUP_DAILY - (this.exec<{ n: number }>('SELECT COALESCE(SUM(n), 0) AS n FROM quota WHERE day = ?', day)[0]?.n ?? 0);
    const heads = this.heads();

    // Refusals last for the connection (S7): a client sends all of an author's frames before it reads
    // an err, so a refusal at seq 3 must also refuse a roster envelope at seq 5 in a later frame, or the
    // head would pass the hole. Daily refusals (quota) end with the UTC day.
    const refused: Record<string, [number, number]> = {};
    for (const [a, r] of Object.entries(att.refused ?? {})) if (r[1] === 0 || r[1] === day) refused[a] = r; // expired daily entries drop out
    const isRefused = (author: string, seq: number): boolean => {
      const r = refused[author];
      return r !== undefined && (r[1] === 0 || r[1] === day) && seq >= r[0];
    };
    const refuse = (author: string, seq: number, code: string, daily = false): void => {
      this.error(ws, code, author, seq);
      const held = refused[author];
      if (!held || (held[1] !== 0 && held[1] !== day) || seq < held[0]) refused[author] = [seq, daily ? day : 0];
    };

    // Phase 1: judge every item (asynchronous signature checks), writing nothing.
    interface Op { c: Checked; entry: RosterEntry | null; headless: boolean }
    const ops: Op[] = [];
    const advanced = new Map<string, number>();
    const counts = new Map<string, number>();
    const used = new Map<string, number>();
    const pending: RosterEntry[] = [];
    let closeAfter: string | null = null;
    for (const item of items) {
      const c = this.decode(item);
      if (c === 'size' || (c !== null && 'bad' in c && !c.env)) {
        this.error(ws, c === 'size' ? 'too_big' : 'bad_frame');
        closeAfter = c === 'size' ? 'too_big' : 'bad item';
        break; // what came before is kept; nothing after an unattributable item is
      }
      if (c === null) break;
      if ('bad' in c) {
        refuse(c.env!.author, c.env!.seq, 'bad_sig'); // the client stops an author on bad_sig, not on bad_frame
        continue;
      }
      const { env, text } = c;
      const author = env.author;
      if (isRefused(author, env.seq)) continue;
      const member = roster.members.get(author);
      if (!member && env.kind === 'join') {
        // Re-seed (S7): a non-member's join that admits its author is stored and changes the roster,
        // but, as at auth, it does not move the head and is not forwarded: the member's earlier
        // envelopes, with lower seq, still have to arrive first. The invite's expiry on the relay
        // clock is an auth-time rule only: an emptied relay must learn every member from any replica.
        if (!(await verifySignature(author, c.s, c.e))) { refuse(author, env.seq, 'bad_sig'); continue; }
        const admitted = await this.withJoin(c, now, roster, pending);
        if (admitted) {
          roster = admitted.roster;
          pending.push(admitted.entry);
          ops.push({ c, entry: admitted.entry, headless: true });
          continue;
        }
      }
      if (!member) { refuse(author, env.seq, 'not_member'); continue; }
      if (!admits(roster, author, env.seq)) { refuse(author, env.seq, 'removed'); continue; }
      const head = advanced.get(author) ?? heads.get(author) ?? 0;
      if (env.seq <= head) continue;
      if (!(await verifySignature(author, c.s, c.e))) { refuse(author, env.seq, 'bad_sig'); continue; }
      // Only roster envelopes that can change membership are exempt from the quotas (S14): the
      // leader's group, invite and remove. Every other envelope counts, a member's own join included.
      const entry = ROSTER_KINDS.has(env.kind) ? rosterEntry(env, text, now) : null;
      const exempt = entry !== null && author === roster.leader;
      if (!exempt) {
        if (!used.has(author)) {
          used.set(author, this.exec<{ n: number }>('SELECT n FROM quota WHERE day = ? AND author = ?', day, author)[0]?.n ?? 0);
        }
        if (used.get(author)! + (counts.get(author) ?? 0) >= MEMBER_DAILY) { refuse(author, env.seq, 'quota', true); continue; }
        if (groupLeft <= 0) { refuse(author, env.seq, 'group_quota', true); continue; }
        counts.set(author, (counts.get(author) ?? 0) + 1);
        groupLeft -= 1;
      }
      advanced.set(author, env.seq);
      // The roster table holds what computeRoster can use: the leader's group/invite/remove, and a join only when it
      // admits a non-member (the headless path above, or auth). A member's own join is already in the table.
      ops.push({ c, entry: entry !== null && author === roster.leader && entry.kind !== 'join' ? entry : null, headless: false });
    }

    // Phase 2: every write of the frame in one synchronous transaction, after all checks.
    let rosterChanged = false;
    const forward: Item[] = [];
    this.ctx.storage.transactionSync(() => {
      for (const op of ops) {
        this.storeLog(op.c);
        if (op.entry && this.insertRoster(op.entry)) rosterChanged = true;
        if (!op.headless) forward.push(op.c.item);
      }
      for (const [author, seq] of advanced) {
        this.exec('INSERT INTO heads (author, seq) VALUES (?, ?) ON CONFLICT (author) DO UPDATE SET seq = excluded.seq', author, seq);
      }
      for (const [author, n] of counts) {
        this.exec('INSERT INTO quota (day, author, n) VALUES (?, ?, ?) ON CONFLICT (day, author) DO UPDATE SET n = n + excluded.n', day, author, n);
      }
    });
    if (Object.keys(refused).length > MAX_REFUSALS) closeAfter = closeAfter ?? 'too many refusals';
    // Always saved, and marked closing when the socket is about to close, so a queued frame is judged with this map.
    ws.serializeAttachment({ ...att, refused: closeAfter ? Object.fromEntries(Object.entries(refused).slice(0, MAX_REFUSALS)) : refused, closing: closeAfter ? true : undefined } satisfies Attachment);
    if (forward.length) this.sendItems((f) => this.broadcast(ws, f), forward);
    if (rosterChanged) {
      this.rosterLoaded = false;
      const updated = await this.currentRoster();
      for (const other of this.members()) {
        const a = (other.deserializeAttachment() as Attachment).author!;
        const m = updated?.members.get(a);
        if (!m || m.lastSeq !== null) this.shut(other, CLOSE_FORBIDDEN, 'removed');
      }
      if (updated && [...updated.members.keys()].some((a) => !membersBefore.has(a))) {
        this.broadcast(null, { t: 'have', heads: this.listedHeads(updated) });
      }
    }
    if (closeAfter) this.shut(ws, CLOSE_POLICY, closeAfter);
  }

  // --- status (Task 14) ---------------------------------------------------------------------

  private async status(ws: WebSocket, att: Attachment, frame: any): Promise<void> {
    void ws; void att; void frame;
  }

  private sendStatuses(ws: WebSocket, author: string): void {
    void ws; void author;
  }

  private persistStatus(ws: WebSocket): void {
    void ws;
  }
}
