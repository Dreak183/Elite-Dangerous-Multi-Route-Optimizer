"""Generate crew-relay/test/vectors.json, the cases client and relay must agree on (spec B1, Testing).

Deterministic: fixed seeds, ids and times; Ed25519 signatures are deterministic (RFC 8032).
Independent of edmrn: it builds bytes with the standard library and `cryptography` only.

    .venv/Scripts/python.exe tests/vectors/make_crew_vectors.py        # rewrite the file
tests/test_crew_vectors.py fails when the committed file differs from a fresh generation.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

OUT = Path(__file__).resolve().parents[2] / 'crew-relay' / 'test' / 'vectors.json'
NOW = '2026-10-13T12:00:00Z'
G = '01920000-0000-7000-8000-0000000000a1'
OTHER_G = '01920000-0000-7000-8000-0000000000ff'
TOKENS = {1: '11' * 32, 2: '22' * 32, 3: '33' * 32}


def uid(n: int) -> str:
    return f'01920000-0000-7000-8000-{n:012x}'


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def sha(hex_token: str) -> str:
    return hashlib.sha256(bytes.fromhex(hex_token)).hexdigest()


class Key:
    def __init__(self, name: str) -> None:
        self.seed = hashlib.sha256(b'edmrn-vector-' + name.encode()).digest()
        self.key = Ed25519PrivateKey.from_private_bytes(self.seed)
        self.author = b64(self.key.public_key().public_bytes(serialization.Encoding.Raw,
                                                             serialization.PublicFormat.Raw))

    def sign(self, data: bytes) -> bytes:
        return self.key.sign(data)


KEYS = {name: Key(name) for name in ('leader', 'alice', 'bob', 'carol', 'mallory')}
L, A, B, C, M = (KEYS[n] for n in ('leader', 'alice', 'bob', 'carol', 'mallory'))


def envelope(key: Key, seq: int, kind: str, at: str, payload: dict, event_id: str) -> dict:
    return {'v': 1, 'event_id': event_id, 'author': key.author, 'seq': seq, 'kind': kind,
            'created_at': at, 'payload': payload}


def roster(group=G, **payload) -> dict:
    return {'group_id': group, **payload}


SPECS = {
    'group': (L, 1, 'group', '2026-10-13T10:00:00Z', roster(name='Crew', commander='Leader'), uid(1)),
    'invite1': (L, 2, 'invite', '2026-10-13T10:01:00Z',
                roster(invite_id=uid(101), token_hash=sha(TOKENS[1]),
                       expires_at='2026-10-14T10:00:00Z', label='Alice'), uid(2)),
    'invite2': (L, 3, 'invite', '2026-10-13T10:01:30Z',
                roster(invite_id=uid(102), token_hash=sha(TOKENS[2]),
                       expires_at='2026-10-14T10:00:00Z', label=None), uid(3)),
    'invite3_expiring': (L, 4, 'invite', '2026-10-13T10:02:00Z',
                         roster(invite_id=uid(103), token_hash=sha(TOKENS[3]),
                                expires_at='2026-10-13T10:05:00Z', label=None), uid(4)),
    'remove_alice_5': (L, 5, 'remove', '2026-10-13T11:00:00Z',
                       roster(member=A.author, last_seq=5), uid(5)),
    'remove_alice_3': (L, 6, 'remove', '2026-10-13T11:01:00Z',
                       roster(member=A.author, last_seq=3), uid(6)),
    'remove_leader': (L, 7, 'remove', '2026-10-13T11:02:00Z',
                      roster(member=L.author, last_seq=0), uid(7)),
    'join_leader_own': (L, 8, 'join', '2026-10-13T10:01:45Z',
                        roster(invite_id=uid(102), token=TOKENS[2], commander='Leader'), uid(8)),
    'join_alice': (A, 1, 'join', '2026-10-13T10:02:00Z',
                   roster(invite_id=uid(101), token=TOKENS[1], commander='Alice'), uid(10)),
    'invite_by_alice': (A, 2, 'invite', '2026-10-13T10:02:30Z',
                        roster(invite_id=uid(120), token_hash=sha(TOKENS[1]),
                               expires_at='2026-10-14T10:00:00Z', label=None), uid(20)),
    'join_bob_same_invite': (B, 1, 'join', '2026-10-13T10:03:00Z',
                             roster(invite_id=uid(101), token=TOKENS[1], commander='Bob'), uid(11)),
    'join_bob_wrong_token': (B, 2, 'join', '2026-10-13T10:03:30Z',
                             roster(invite_id=uid(102), token='44' * 32, commander='Bob'), uid(12)),
    'join_bob_invite2': (B, 3, 'join', '2026-10-13T10:04:00Z',
                         roster(invite_id=uid(102), token=TOKENS[2], commander='Bob'), uid(13)),
    'join_bob_alice_invite': (B, 4, 'join', '2026-10-13T10:05:00Z',
                              roster(invite_id=uid(120), token=TOKENS[1], commander='Bob'), uid(21)),
    'join_bob_other_group': (B, 5, 'join', '2026-10-13T10:02:30Z',
                             roster(group=OTHER_G, invite_id=uid(101), token=TOKENS[1],
                                    commander='Bob'), uid(31)),
    'join_carol_expired': (C, 1, 'join', '2026-10-13T10:06:00Z',
                           roster(invite_id=uid(103), token=TOKENS[3], commander='Carol'), uid(14)),
    'join_carol_implausible': (C, 2, 'join', '2010-01-01T00:00:00Z',
                               roster(invite_id=uid(102), token=TOKENS[2], commander='Carol'), uid(15)),
    'group_by_mallory': (M, 1, 'group', '2014-12-16T00:00:00Z',
                         roster(name='Pwned', commander='M'), uid(30)),
    'remove_bob_by_alice': (A, 4, 'remove', '2026-10-13T11:05:00Z',
                            roster(member=B.author, last_seq=0), uid(22)),
    'join_tie_alice': (A, 6, 'join', '2026-10-13T10:03:00Z',
                       roster(invite_id=uid(101), token=TOKENS[1], commander='Alice'), uid(40)),
    'join_tie_bob': (B, 6, 'join', '2026-10-13T10:03:00Z',
                     roster(invite_id=uid(101), token=TOKENS[1], commander='Bob'), uid(41)),
    'join_carol_at_expiry': (C, 3, 'join', '2026-10-13T10:05:00Z',
                             roster(invite_id=uid(103), token=TOKENS[3], commander='Carol'), uid(42)),
    'invite1_duplicate_id': (L, 9, 'invite', '2026-10-13T10:01:20Z',
                             roster(invite_id=uid(101), token_hash=sha(TOKENS[2]),
                                    expires_at='2026-10-14T10:00:00Z', label='Second'), uid(43)),
    'join_bob_second_token': (B, 7, 'join', '2026-10-13T10:03:30Z',
                              roster(invite_id=uid(101), token=TOKENS[2], commander='Bob'), uid(44)),
    'visit_alice': (A, 3, 'visit', '2026-10-13T10:10:00Z',
                    {'commander': 'Alice', 'system': 'Sol', 'address': '10477373803',
                     'coords': [0.0, 0.0, 0.0]}, 'ab' * 16),
}


def sealed(name: str) -> tuple:
    key, seq, kind, at, payload, event_id = SPECS[name]
    data = canonical(envelope(key, seq, kind, at, payload, event_id))
    return key, seq, kind, data, key.sign(data)


def build() -> dict:
    envs = {}
    for name in SPECS:
        key, seq, kind, data, sig = sealed(name)
        envs[name] = {'e': b64(data), 's': b64(sig), 'author': key.author, 'seq': seq, 'kind': kind}

    group_obj = envelope(*SPECS['group'][:5], SPECS['group'][5])

    def signed_text(text: bytes, key: Key = L) -> dict:
        return {'e': b64(text), 's': b64(key.sign(text))}

    structure = []
    for name, mutate in [
        ('valid', lambda e: e),
        ('missing_payload', lambda e: {k: v for k, v in e.items() if k != 'payload'}),
        ('extra_key', lambda e: {**e, 'x': 1}),
        ('seq_zero', lambda e: {**e, 'seq': 0}),
        ('seq_too_big', lambda e: {**e, 'seq': 2 ** 53}),
        ('v_1001', lambda e: {**e, 'v': 1001}),
        ('author_42', lambda e: {**e, 'author': e['author'][:42]}),
        ('stamp_shape', lambda e: {**e, 'created_at': '2026-10-13 12:00:00'}),
        ('payload_list', lambda e: {**e, 'payload': []}),
        ('event_id_upper', lambda e: {**e, 'event_id': 'AB' * 16}),
        ('kind_upper', lambda e: {**e, 'kind': 'Visit'}),
    ]:
        structure.append({'name': name, 'e': b64(canonical(mutate(dict(group_obj)))),
                          'valid': name == 'valid'})

    _, _, _, group_data, group_sig = sealed('group')
    _, _, _, visit_data, _ = sealed('visit_alice')

    def padded(n: int) -> bytes:
        return canonical(envelope(A, 1, 'visit', '2026-10-13T10:00:00Z', {'pad': 'x' * n}, 'ab' * 16))

    pad = 16384 - len(padded(0))
    # Byte-level variants where Python's json and JavaScript's JSON.parse could disagree. The relay
    # requires canonical bytes for every envelope (valid False); Python's parse_envelope checks the
    # structure only, so it still accepts non-canonical spacing and key order (python_valid True).
    spaced_env = json.dumps(group_obj, sort_keys=True, separators=(', ', ': ')).encode()
    unsorted_env = json.dumps(group_obj, separators=(',', ':')).encode()
    raw_cases = [
        ('raw_canonical', group_data, True, None),
        ('raw_16384_bytes', padded(pad), True, None),
        ('raw_16385_bytes', padded(pad + 1), False, None),
        ('raw_float_seq_3_0', group_data.replace(b'"seq":1,', b'"seq":3.0,'), False, None),
        ('raw_float_seq_3e0', group_data.replace(b'"seq":1,', b'"seq":3e0,'), False, None),
        ('raw_float_v', group_data.replace(b'"v":1}', b'"v":1.0}'), False, None),
        ('raw_duplicate_seq_visit', visit_data.replace(b'"seq":3,', b'"seq":3,"seq":3,'), False, None),
        ('raw_bom', b'\xef\xbb\xbf' + group_data, False, None),
        ('raw_invalid_utf8', group_data.replace(b'Crew', b'Cr\xffw'), False, None),
        ('raw_spaced', spaced_env, False, True),
        ('raw_unsorted_keys', unsorted_env, False, True),
    ]
    for name, data, relay_ok, python_ok in raw_cases:
        assert (name == 'raw_canonical' or data != group_data), name
        case_ = {'name': name, 'e': b64(data), 'valid': relay_ok}
        if python_ok is not None:
            case_['python_valid'] = python_ok
        structure.append(case_)
    tampered = bytearray(group_data)
    tampered[-3] ^= 1
    identity = bytes([1]) + bytes(31)  # the neutral point: plain Ed25519 accepts (R=identity, S=0)
    small_order_env = canonical({**group_obj, 'author': b64(identity)})
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
    # The last character of a 43-character key carries 2 padding bits; canonical keys have them 0.
    noncanonical_tail = alphabet[alphabet.index(L.author[-1]) + 1]
    signatures = [
        {'name': 'valid', 'e': b64(group_data), 's': b64(group_sig), 'author': L.author, 'valid': True},
        {'name': 'tampered', 'e': b64(bytes(tampered)), 's': b64(group_sig), 'author': L.author,
         'valid': False},
        {'name': 'wrong_key', 'e': b64(group_data), 's': b64(A.sign(group_data)), 'author': L.author,
         'valid': False},
        {'name': 'small_order_author', 'e': b64(small_order_env), 's': b64(identity + bytes(32)),
         'author': b64(identity), 'valid': False},
        {'name': 'non_canonical_author', 'e': b64(group_data), 's': b64(group_sig),
         'author': L.author[:-1] + noncanonical_tail, 'valid': False},
        {'name': 'signature_63_bytes', 'e': b64(group_data), 's': b64(group_sig[:63]),
         'author': L.author, 'valid': False},
    ]

    spaced = json.dumps(group_obj, sort_keys=True, separators=(', ', ': ')).encode()
    duplicate = group_data.replace(b'"kind":"group"', b'"kind":"group","kind":"group"', 1)
    float_seq = group_data.replace(b'"seq":1', b'"seq":1.0', 1)
    escaped = group_data.replace(b'"name":"Crew"', b'"name":"\\u0043rew"', 1)
    canonical_cases = [
        {'name': 'canonical', **signed_text(group_data), 'canonical': True},
        {'name': 'spaces', **signed_text(spaced), 'canonical': False},
        {'name': 'duplicate_key', **signed_text(duplicate), 'canonical': False},
        {'name': 'float_seq', **signed_text(float_seq), 'canonical': False},
        {'name': 'escaped_ascii', **signed_text(escaped), 'canonical': False},
    ]

    base_invite = roster(invite_id=uid(101), token_hash=sha(TOKENS[1]),
                         expires_at='2026-10-14T10:00:00Z', label='Alice')
    v4 = '01920000-0000-4000-8000-000000000001'
    accent = '\xe9'
    payload_cases = [
        {'kind': 'group', 'payload': roster(name='Crew', commander='Leader'), 'valid': True},
        {'kind': 'group', 'payload': roster(name='\xdcn\xefcode', commander='Leader'), 'valid': False},
        {'kind': 'group', 'payload': roster(name='x' * 65, commander='Leader'), 'valid': False},
        {'kind': 'group', 'payload': roster(name='', commander='Leader'), 'valid': False},
        {'kind': 'group', 'payload': roster(name='Crew', commander='Leader', extra=1), 'valid': False},
        {'kind': 'invite', 'payload': base_invite, 'valid': True},
        {'kind': 'invite', 'payload': {**base_invite, 'label': None}, 'valid': True},
        {'kind': 'invite', 'payload': {**base_invite, 'expires_at': '2026-02-30T00:00:00Z'}, 'valid': False},
        {'kind': 'invite', 'payload': {**base_invite, 'token_hash': 'AB' * 32}, 'valid': False},
        {'kind': 'join', 'payload': roster(invite_id=uid(101), token=TOKENS[1], commander='Bob'), 'valid': True},
        {'kind': 'join', 'payload': roster(invite_id='nope', token=TOKENS[1], commander='Bob'), 'valid': False},
        {'kind': 'remove', 'payload': roster(member=A.author, last_seq=0), 'valid': True},
        {'kind': 'remove', 'payload': roster(member=A.author, last_seq=-1), 'valid': False},
        {'kind': 'remove', 'payload': roster(member=A.author, last_seq=True), 'valid': False},
        {'kind': 'remove', 'payload': roster(member=A.author[:40], last_seq=1), 'valid': False},
        {'kind': 'remove', 'payload': roster(member=A.author, last_seq=2 ** 53 - 1), 'valid': True},
        {'kind': 'remove', 'payload': roster(member=A.author, last_seq=2 ** 53), 'valid': False},
        {'kind': 'group', 'payload': roster(name='x' * 64, commander='Leader'), 'valid': True},
        {'kind': 'group', 'payload': roster(group=v4, name='Crew', commander='Leader'), 'valid': False},
        {'kind': 'group', 'payload': roster(group=uid(0xa1).upper(), name='Crew', commander='Leader'),
         'valid': False},
        {'kind': 'group', 'payload': roster(name='Crew', commander='Leader' + accent), 'valid': False},
        {'kind': 'invite', 'payload': {**base_invite, 'invite_id': v4}, 'valid': False},
        {'kind': 'invite', 'payload': {**base_invite, 'invite_id': uid(0xb1).upper()}, 'valid': False},
        {'kind': 'invite', 'payload': {**base_invite, 'label': 'Al' + accent + 'ce'}, 'valid': False},
        {'kind': 'join', 'payload': roster(invite_id=uid(101), token='AA' * 32, commander='Bob'),
         'valid': False},
        {'kind': 'join', 'payload': roster(invite_id=uid(101), token='11' * 31, commander='Bob'),
         'valid': False},
        {'kind': 'join', 'payload': roster(invite_id=v4, token=TOKENS[1], commander='Bob'), 'valid': False},
        {'kind': 'join', 'payload': roster(invite_id=uid(101), token=TOKENS[1], commander='B' + accent),
         'valid': False},
    ]

    plausibility = [{'created_at': s, 'plausible': ok} for s, ok in [
        ('2026-10-13T12:00:00Z', True), ('2014-12-16T00:00:00Z', True),
        ('2014-12-15T23:59:59Z', False), ('2026-10-14T12:00:00Z', True),
        ('2026-10-14T12:00:01Z', False), ('2026-02-30T00:00:00Z', False),
        ('0000-01-01T00:00:00Z', False), ('2026-10-13T12:00:60Z', False),
        ('2026-10-13T24:00:00Z', False),
    ]]

    def case(name, names, members, group_name='Crew'):
        return {'name': name, 'leader': L.author, 'envelopes': names,
                'members': {KEYS[k].author: v for k, v in members.items()}, 'name_of_group': group_name}

    removal = ['group', 'invite1', 'join_alice', 'remove_alice_5', 'remove_alice_3', 'remove_leader']
    roster_cases = [
        case('basic', ['group', 'invite1', 'join_alice'], {'leader': None, 'alice': None}),
        case('second_join_same_invite', ['group', 'invite1', 'join_alice', 'join_bob_same_invite'],
             {'leader': None, 'alice': None}),
        case('wrong_token_does_not_block', ['group', 'invite2', 'join_bob_wrong_token', 'join_bob_invite2'],
             {'leader': None, 'bob': None}),
        case('expired', ['group', 'invite3_expiring', 'join_carol_expired'], {'leader': None}),
        case('remove_lowest_never_leader', removal, {'leader': None, 'alice': 3}),
        case('shuffled', list(reversed(removal)), {'leader': None, 'alice': 3}),
        case('non_leader_invite', ['group', 'invite_by_alice', 'join_bob_alice_invite'], {'leader': None}),
        case('foreign_group_envelope', ['group_by_mallory', 'group', 'invite1', 'join_alice'],
             {'leader': None, 'alice': None}),
        case('other_group_join', ['group', 'invite1', 'join_bob_other_group'], {'leader': None}),
        case('implausible_join_ignored', ['group', 'invite2', 'join_carol_implausible', 'join_bob_invite2'],
             {'leader': None, 'bob': None}),
        case('leader_join_ignored', ['group', 'invite2', 'join_leader_own', 'join_bob_invite2'],
             {'leader': None, 'bob': None}),
        case('no_group_envelope', ['invite1', 'join_alice'], {'leader': None, 'alice': None}, None),
    ]

    roster_cases += [
        # Equal created_at: the order falls to the author, code-point order; the smaller wins.
        case('tie_break_by_author', ['group', 'invite1', 'join_tie_bob', 'join_tie_alice'],
             {'leader': None, 'alice' if A.author < B.author else 'bob': None}),
        case('tie_break_by_author_reversed', ['group', 'invite1', 'join_tie_alice', 'join_tie_bob'],
             {'leader': None, 'alice' if A.author < B.author else 'bob': None}),
        case('join_at_expiry_admitted', ['group', 'invite3_expiring', 'join_carol_at_expiry'],
             {'leader': None, 'carol': None}),
        case('duplicate_invite_id_first_wins',
             ['invite1_duplicate_id', 'join_bob_second_token', 'group', 'invite1', 'join_alice'],
             {'leader': None, 'alice': None}),
    ]
    # A friend's `remove` is not the leader's: Bob stays active (S2).
    roster_cases.append(case(
        'non_leader_remove_ignored',
        ['group', 'invite1', 'invite2', 'join_alice', 'join_bob_invite2', 'remove_bob_by_alice'],
        {'leader': None, 'alice': None, 'bob': None}))

    relay = 'wss://galroute.the-hub.cloud'
    invite_text = 'edmrn-crew:1:' + b64(canonical({'g': G, 'i': uid(101), 'k': TOKENS[1],
                                                   'l': L.author, 'u': relay}))
    invite = {'code': {'url': relay, 'group_id': G, 'invite_id': uid(101), 'token': TOKENS[1],
                       'leader': L.author},
              'text': invite_text, 'link': 'https://galroute.the-hub.cloud/join#' + invite_text}
    # Task 1's refused strings (control, bidi, surrogate, format and separator characters); the
    # JSON file carries them as escapes because ensure_ascii is on.
    whitelist_refusals = ['a\x00b', 'a\nb', 'a\x1bb', 'a\x7fb', 'a\x85b',
                          'a\u202eb', 'a\u2066b', 'a\ud800b', 'a\u200bb', 'a\u2028b']

    nonce = b64(bytes(range(32)))
    auth_bytes = f'edmrn-crew-auth\n{G}\n{nonce}'.encode('ascii')
    status_doc = canonical({'author': A.author, 'at': '2026-10-13T12:00:00Z', 'status': {
        'commander': 'Alice', 'system': 'Sol', 'address': '10477373803', 'coords': [0.0, 0.0, 0.0],
        'ship': 'anaconda', 'ship_name': None, 'ship_ident': None, 'edsy_url': None, 'hull': 0.5,
        'fuel_main': 10.0, 'fuel_reserve': 0.5, 'fuel_capacity_main': 32.0,
        'fuel_capacity_reserve': 0.63, 'paused': False}})

    wrong_nonce = b64(bytes(range(1, 33)))
    auth_negative = [
        {'name': 'wrong_nonce', 'group': G, 'nonce': nonce, 'author': L.author,
         'sig': b64(L.sign(f'edmrn-crew-auth{chr(10)}{G}{chr(10)}{wrong_nonce}'.encode('ascii')))},
        {'name': 'wrong_group', 'group': G, 'nonce': nonce, 'author': L.author,
         'sig': b64(L.sign(f'edmrn-crew-auth{chr(10)}{OTHER_G}{chr(10)}{nonce}'.encode('ascii')))},
        {'name': 'other_key', 'group': G, 'nonce': nonce, 'author': L.author, 'sig': b64(A.sign(auth_bytes))},
    ]
    status_negative = [
        {'name': 'signed_by_another_key', 'e': b64(status_doc), 's': b64(B.sign(status_doc)),
         'author': A.author},
    ]

    return {
        'version': 1, 'now': NOW, 'group_id': G,
        'keys': {name: {'seed': key.seed.hex(), 'author': key.author} for name, key in KEYS.items()},
        'envelopes': envs, 'structure_cases': structure, 'signature_cases': signatures,
        'canonical_cases': canonical_cases, 'roster_payload_cases': payload_cases,
        'plausibility_cases': plausibility, 'roster_cases': roster_cases,
        'auth': {'group': G, 'nonce': nonce, 'author': L.author, 'sig': b64(L.sign(auth_bytes))},
        'status': {'e': b64(status_doc), 's': b64(A.sign(status_doc)), 'author': A.author},
        'auth_negative': auth_negative, 'status_negative': status_negative,
        'invite': invite,
        'whitelist_refusals': whitelist_refusals,
    }


def render() -> str:
    return json.dumps(build(), indent=1, sort_keys=True, ensure_ascii=True) + '\n'


if __name__ == '__main__':
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding='utf-8', newline='\n')
    sys.stdout.write(f'wrote {OUT}\n')
