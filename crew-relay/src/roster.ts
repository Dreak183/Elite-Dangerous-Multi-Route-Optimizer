// Group membership (crew sync core S1, S2, ADR-0008), mirroring edmrn/ext/crew_roster.py.
// The shared vectors in test/vectors.json pin both implementations to the same results.
import { canonicalJson } from './canonical';
import { AUTHOR, UUID7, isInt, type Envelope } from './envelope';

export const ROSTER_KINDS = new Set(['group', 'invite', 'join', 'remove']);
export const FLOOR_MS = Date.UTC(2014, 11, 16);
const SLACK_MS = 24 * 3600 * 1000;
const NAME = /^[\x20-\x7e]{1,64}$/;
const HEX64 = /^[0-9a-f]{64}$/;
const STAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z$/;

export interface RosterEntry {
  author: string;
  seq: number;
  kind: string;
  created_at: string;
  payload: Record<string, any>;
}

export interface Member {
  author: string;
  commander: string | null;
  leader: boolean;
  lastSeq: number | null;
}

export interface Roster {
  group: string;
  leader: string;
  name: string | null;
  members: Map<string, Member>;
}

/** Milliseconds of a real calendar time in the envelope's stamp shape, else null. */
export function realStamp(value: unknown): number | null {
  if (typeof value !== 'string') return null;
  const m = STAMP.exec(value);
  if (!m) return null;
  const [y, mo, d, h, mi, s] = m.slice(1).map(Number);
  if (y < 1) return null;
  const date = new Date(0);
  date.setUTCFullYear(y, mo - 1, d);
  date.setUTCHours(h, mi, s, 0);
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== mo - 1 || date.getUTCDate() !== d ||
      date.getUTCHours() !== h || date.getUTCMinutes() !== mi || date.getUTCSeconds() !== s) {
    return null;
  }
  return date.getTime();
}

export function plausible(createdAt: unknown, nowMs: number): boolean {
  const t = realStamp(createdAt);
  return t !== null && t >= FLOOR_MS && t <= nowMs + SLACK_MS;
}

function exactKeys(p: Record<string, unknown>, keys: string): boolean {
  return Object.keys(p).sort().join(',') === keys;
}

const isName = (v: unknown) => typeof v === 'string' && NAME.test(v);
const isUuid = (v: unknown) => typeof v === 'string' && UUID7.test(v);
const isHex64 = (v: unknown) => typeof v === 'string' && HEX64.test(v);

export function validRosterPayload(kind: string, payload: unknown): boolean {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return false;
  const p = payload as Record<string, unknown>;
  switch (kind) {
    case 'group':
      return exactKeys(p, 'commander,group_id,name') && isUuid(p.group_id) && isName(p.name) && isName(p.commander);
    case 'invite':
      return exactKeys(p, 'expires_at,group_id,invite_id,label,token_hash') && isUuid(p.group_id) &&
        isUuid(p.invite_id) && isHex64(p.token_hash) && realStamp(p.expires_at) !== null &&
        (p.label === null || isName(p.label));
    case 'join':
      return exactKeys(p, 'commander,group_id,invite_id,token') && isUuid(p.group_id) &&
        isUuid(p.invite_id) && isHex64(p.token) && isName(p.commander);
    case 'remove':
      return exactKeys(p, 'group_id,last_seq,member') && isUuid(p.group_id) &&
        typeof p.member === 'string' && AUTHOR.test(p.member) && isInt(p.last_seq, 0, Number.MAX_SAFE_INTEGER);
    default:
      return false;
  }
}

/** A roster envelope that may count toward membership, else null (S1, S18). */
export function rosterEntry(env: Envelope, text: string, nowMs: number): RosterEntry | null {
  if (!ROSTER_KINDS.has(env.kind) || env.v !== 1 || !UUID7.test(env.event_id)) return null;
  if (canonicalJson(env) !== text) return null;
  if (!validRosterPayload(env.kind, env.payload)) return null;
  if (!plausible(env.created_at, nowMs)) return null;
  return { author: env.author, seq: env.seq, kind: env.kind, created_at: env.created_at, payload: env.payload };
}

export async function sha256hex(hex: string): Promise<string> {
  const bytes = new Uint8Array(hex.match(/../g)!.map((h) => parseInt(h, 16)));
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  return [...digest].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function byOrder(a: RosterEntry, b: RosterEntry): number {
  if (a.created_at !== b.created_at) return a.created_at < b.created_at ? -1 : 1;
  if (a.author !== b.author) return a.author < b.author ? -1 : 1;
  return a.seq - b.seq;
}

export async function computeRoster(entries: RosterEntry[], group: string, leader: string): Promise<Roster> {
  const mine = entries.filter((e) => e.payload.group_id === group).sort(byOrder);
  let name: string | null = null;
  let leaderCommander: string | null = null;
  const invites = new Map<string, RosterEntry>();
  for (const e of mine) {
    if (e.author !== leader) continue;
    if (e.kind === 'group' && name === null) {
      name = e.payload.name;
      leaderCommander = e.payload.commander;
    } else if (e.kind === 'invite' && !invites.has(e.payload.invite_id)) {
      invites.set(e.payload.invite_id, e);
    }
  }
  const admitted = new Map<string, string>();
  const redeemed = new Set<string>();
  for (const e of mine) {
    if (e.kind !== 'join' || e.author === leader) continue;
    const invite = invites.get(e.payload.invite_id);
    if (!invite || redeemed.has(e.payload.invite_id)) continue;
    if ((await sha256hex(e.payload.token)) !== invite.payload.token_hash) continue;
    if (e.created_at > invite.payload.expires_at) continue;
    redeemed.add(e.payload.invite_id);
    if (!admitted.has(e.author)) admitted.set(e.author, e.payload.commander);
  }
  const caps = new Map<string, number>();
  for (const e of mine) {
    if (e.kind === 'remove' && e.author === leader && e.payload.member !== leader) {
      const last = e.payload.last_seq as number;
      caps.set(e.payload.member, Math.min(caps.get(e.payload.member) ?? last, last));
    }
  }
  const members = new Map<string, Member>([[leader, { author: leader, commander: leaderCommander, leader: true, lastSeq: null }]]);
  for (const [author, commander] of admitted) {
    members.set(author, { author, commander, leader: false, lastSeq: caps.get(author) ?? null });
  }
  return { group, leader, name, members };
}

export function admits(roster: Roster | null, author: string, seq: number): boolean {
  const member = roster?.members.get(author);
  return member !== undefined && (member.lastSeq === null || seq <= member.lastSeq);
}
