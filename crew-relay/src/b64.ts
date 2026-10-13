export function b64urlEncode(bytes: Uint8Array): string {
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** Bytes from canonical unpadded base64url of at most maxBytes bytes, else null. */
export function b64urlDecode(text: unknown, maxBytes: number): Uint8Array | null {
  if (typeof text !== 'string' || !/^[A-Za-z0-9_-]*$/.test(text) || text.length > Math.ceil((maxBytes * 4) / 3)) {
    return null;
  }
  let binary: string;
  try {
    binary = atob(text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (text.length % 4)) % 4));
  } catch {
    return null;
  }
  const out = Uint8Array.from(binary, (c) => c.charCodeAt(0));
  if (out.length > maxBytes || b64urlEncode(out) !== text) return null;
  return out;
}
