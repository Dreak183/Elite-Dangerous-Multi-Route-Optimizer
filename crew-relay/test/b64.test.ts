import { describe, expect, it } from 'vitest';
import { b64urlDecode, b64urlEncode } from '../src/b64';

describe('b64url', () => {
  it('round-trips and rejects non-canonical or oversize input', () => {
    expect(b64urlDecode(b64urlEncode(new Uint8Array([0, 255, 7])), 3)).toEqual(new Uint8Array([0, 255, 7]));
    for (const bad of ['a=', 'a+b', 'AB', 'A'.repeat(10)]) expect(b64urlDecode(bad, 2)).toBeNull();
    expect(b64urlDecode(42, 2)).toBeNull();
  });
});
