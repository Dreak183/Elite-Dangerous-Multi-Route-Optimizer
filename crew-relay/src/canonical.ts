/** Sorted keys, no spaces; equal to Python's json.dumps(sort_keys, separators=(',', ':'),
 *  ensure_ascii=False) for the ASCII strings and safe integers of roster envelopes. */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(canonicalJson).join(',') + ']';
  const record = value as Record<string, unknown>;
  return '{' + Object.keys(record).sort().map((k) => JSON.stringify(k) + ':' + canonicalJson(record[k])).join(',') + '}';
}
