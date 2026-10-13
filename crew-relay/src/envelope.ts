// Envelope structure and signatures (Spec A C2, crew sync core S18), mirroring
// edmrn/ext/crew_events.py check_structure and crew_identity.verify.
import { b64urlDecode } from './b64';

export const MAX_ENVELOPE_BYTES = 16 * 1024;
export const UUID7 = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const AUTHOR = /^[A-Za-z0-9_-]{43}$/;
export const STAMP_SHAPE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$/;
const HEX_ID = /^[0-9a-f]{32}$/;
const KIND = /^[a-z_]{1,32}$/;
const KEYS = 'author,created_at,event_id,kind,payload,seq,v';

export interface Envelope {
  v: number;
  event_id: string;
  author: string;
  seq: number;
  kind: string;
  created_at: string;
  payload: Record<string, unknown>;
}

export function isInt(v: unknown, lo: number, hi: number): v is number {
  return typeof v === 'number' && Number.isInteger(v) && v >= lo && v <= hi;
}

export function checkStructure(value: unknown): Envelope | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const e = value as Record<string, unknown>;
  if (Object.keys(e).sort().join(',') !== KEYS) return null;
  if (!isInt(e.v, 1, 1000)) return null;
  if (typeof e.event_id !== 'string' || !(HEX_ID.test(e.event_id) || UUID7.test(e.event_id))) return null;
  if (typeof e.author !== 'string' || !AUTHOR.test(e.author)) return null;
  if (!isInt(e.seq, 1, Number.MAX_SAFE_INTEGER)) return null;
  if (typeof e.kind !== 'string' || !KIND.test(e.kind)) return null;
  if (typeof e.created_at !== 'string' || !STAMP_SHAPE.test(e.created_at)) return null;
  if (typeof e.payload !== 'object' || e.payload === null || Array.isArray(e.payload)) return null;
  return e as unknown as Envelope;
}

// ignoreBOM keeps a leading U+FEFF, which JSON.parse then refuses, as Python does.
const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });

export function parseEnvelope(bytes: Uint8Array): { env: Envelope; text: string } | null {
  if (bytes.length > MAX_ENVELOPE_BYTES) return null;
  let text: string;
  let value: unknown;
  try {
    text = decoder.decode(bytes);
    value = JSON.parse(text);
  } catch {
    return null;
  }
  const env = checkStructure(value);
  return env ? { env, text } : null;
}

// libsodium's small-order blocklist, compared with the top bit of the last byte cleared
// (edmrn/ext/crew_identity.py _SMALL_ORDER).
const SMALL_ORDER = [
  '0000000000000000000000000000000000000000000000000000000000000000',
  '0100000000000000000000000000000000000000000000000000000000000000',
  '26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05',
  'c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a',
  'ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f',
  'edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f',
  'eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f',
];

export function isSmallOrder(raw: Uint8Array): boolean {
  const key = raw.slice();
  key[31] &= 0x7f;
  const hex = [...key].map((b) => b.toString(16).padStart(2, '0')).join('');
  return SMALL_ORDER.includes(hex);
}

const keyCache = new Map<string, CryptoKey>();

export async function verifySignature(author: string, sig: Uint8Array, data: Uint8Array): Promise<boolean> {
  const raw = b64urlDecode(author, 32);
  if (!raw || raw.length !== 32 || sig.length !== 64 || isSmallOrder(raw)) return false;
  try {
    let key = keyCache.get(author);
    if (!key) {
      key = await crypto.subtle.importKey('raw', raw, { name: 'Ed25519' }, false, ['verify']);
      if (keyCache.size > 1000) keyCache.clear();
      keyCache.set(author, key);
    }
    return await crypto.subtle.verify({ name: 'Ed25519' }, key, sig, data);
  } catch {
    return false;
  }
}
