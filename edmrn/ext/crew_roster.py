"""Group roster, invite strings and group.json (crew sync core spec S1-S5a, ADR-0008).

Membership is a pure function of the roster envelopes and the leader's author. The relay imports this module
(`relay/edmrn_relay/engine.py`); `tests/vectors/crew_vectors.json` pins the shared rules.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

from edmrn.ext import crew_events as ev

INVITE_PREFIX = 'edmrn-crew:1:'
INVITE_MAX_CHARS = 2000
INVITE_HOURS = (1, 24, 168)
GROUP_FILE = 'group.json'
DEFAULT_RELAY = 'wss://galroute.the-hub.cloud'
_LOCAL_HOSTS = ('localhost', '127.0.0.1')
_B64URL = re.compile(r'[A-Za-z0-9_-]+')
_WEBHOOK = re.compile(
    r'https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/([0-9]{1,20})/[A-Za-z0-9_-]{1,100}')
_HOST = re.compile(
    r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*',
    re.ASCII)
_DECORATION = ' \t\r\n<>`'


class InviteError(ValueError):
    """An invite, relay address or webhook URL that cannot be used. The message is one sentence."""


class GroupConfigError(ValueError):
    """group.json exists but cannot be used."""


class GroupError(Exception):
    """A refused group action; the message is one sentence for the dialog.

    Lives here, not in crew_group, so the dialog can import it without `cryptography` (S4)."""


@dataclass(frozen=True)
class CreatedInvite:
    code: str
    link: str
    expires_at: str
    label: Optional[str]


@dataclass(frozen=True)
class RosterEnvelope:
    author: str
    seq: int
    kind: str
    created_at: str
    payload: dict

    @property
    def order(self) -> tuple:
        return (self.created_at, self.author, self.seq)


@dataclass(frozen=True)
class Member:
    author: str
    commander: Optional[str]
    leader: bool
    last_seq: Optional[int]


@dataclass(frozen=True)
class InviteInfo:
    invite_id: str
    label: Optional[str]
    expires_at: str
    redeemed_by: Optional[str]


@dataclass(frozen=True)
class Roster:
    group_id: str
    leader: str
    name: Optional[str]
    members: dict
    invites: dict

    def admits(self, author: str, seq: int) -> bool:
        member = self.members.get(author)
        return member is not None and (member.last_seq is None or seq <= member.last_seq)

    def active(self) -> list:
        """Members not removed, the leader first, then by commander name."""
        rest = sorted((m for m in self.members.values() if not m.leader and m.last_seq is None),
                      key=lambda m: ((m.commander or '').lower(), m.author))
        return [self.members[self.leader]] + rest


def token_hash(token: str) -> str:
    return hashlib.sha256(bytes.fromhex(token)).hexdigest()


def new_token() -> str:
    return secrets.token_hex(32)


def parse_roster_envelope(data: Any, own: frozenset, now: datetime) -> Optional[RosterEnvelope]:
    """A roster envelope that may count toward membership (S1, S2, ADR-0007), else None.

    Requires canonical bytes, a known roster kind at v 1, a UUIDv7 event id, a valid payload and,
    for other authors, a plausible created_at (S11). The relay applies the same rules."""
    try:
        env = ev.parse_envelope(data)
    except ev.EnvelopeError:
        return None
    if env['kind'] not in ev.ROSTER_KINDS or env['v'] != 1 or not ev.is_uuid7(env['event_id']):
        return None
    try:
        canonical = ev.canonical_bytes(env)
    except ValueError:  # a lone surrogate escape cannot be encoded as UTF-8
        return None
    if canonical != bytes(data):
        return None
    try:
        ev.validate_payload(env['kind'], 1, env['payload'])
    except ev.PayloadError:
        return None
    if env['author'] not in own and not ev.plausible_received(env['created_at'], now):
        return None
    return RosterEnvelope(env['author'], env['seq'], env['kind'], env['created_at'], env['payload'])


def compute_roster(envelopes: Iterable[RosterEnvelope], group_id: str, leader: str) -> Roster:
    """S2. `envelopes` must already have passed parse_roster_envelope."""
    mine = sorted((e for e in envelopes if e.payload.get('group_id') == group_id),
                  key=lambda e: e.order)
    name: Optional[str] = None
    leader_commander: Optional[str] = None
    invites: dict[str, RosterEnvelope] = {}
    for e in mine:
        if e.author != leader:
            continue
        if e.kind == 'group' and name is None:
            name, leader_commander = e.payload['name'], e.payload['commander']
        elif e.kind == 'invite' and e.payload['invite_id'] not in invites:
            invites[e.payload['invite_id']] = e
    admitted: dict[str, str] = {}
    redeemed: dict[str, str] = {}
    for e in mine:
        if e.kind != 'join' or e.author == leader:
            continue
        invite_id = e.payload['invite_id']
        invite = invites.get(invite_id)
        if invite is None or invite_id in redeemed:
            continue
        token = e.payload['token']
        if not ev.is_hex64(token) or token_hash(token) != invite.payload['token_hash']:
            continue
        if e.created_at > invite.payload['expires_at']:
            continue
        redeemed[invite_id] = e.author
        admitted.setdefault(e.author, e.payload['commander'])
    caps: dict[str, int] = {}
    for e in mine:
        if e.kind == 'remove' and e.author == leader and e.payload['member'] != leader:
            member, last = e.payload['member'], e.payload['last_seq']
            caps[member] = min(caps.get(member, last), last)
    members = {leader: Member(leader, leader_commander, True, None)}
    for author, commander in admitted.items():
        members[author] = Member(author, commander, False, caps.get(author))
    infos = {invite_id: InviteInfo(invite_id, e.payload['label'], e.payload['expires_at'],
                                   redeemed.get(invite_id))
             for invite_id, e in invites.items()}
    return Roster(group_id, leader, name, members, infos)


@dataclass(frozen=True)
class InviteCode:
    url: str
    group_id: str
    invite_id: str
    token: str
    leader: str


def check_relay_url(url: Any) -> str:
    """S5: wss:// with a host and nothing else; ws:// only for localhost. Returns the normal form."""
    message = 'The relay address must look like wss://galroute.the-hub.cloud.'
    if not isinstance(url, str) or len(url) > 200:
        raise InviteError(message)
    try:
        parts = urlsplit(url.strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        raise InviteError(message) from None
    if not host or not _HOST.fullmatch(host) or port == 0:
        raise InviteError(message)
    if parts.username or parts.password or parts.query or parts.fragment:
        raise InviteError(message)
    if parts.path not in ('', '/'):
        raise InviteError(message)
    if parts.scheme != 'wss' and not (parts.scheme == 'ws' and host in _LOCAL_HOSTS):
        raise InviteError(message)
    return f'{parts.scheme}://{host}' + (f':{port}' if port is not None else '')


def connection_url(base: str, group_id: str) -> str:
    return f'{base}/v1/groups/{group_id}/ws'


def encode_invite(code: InviteCode) -> str:
    body = ev.canonical_bytes({'g': code.group_id, 'i': code.invite_id, 'k': code.token,
                               'l': code.leader, 'u': code.url})
    return INVITE_PREFIX + base64.urlsafe_b64encode(body).rstrip(b'=').decode('ascii')


def extract_invite(text: Any) -> str:
    """The bare invite from what a commander pasted: the string, the link, or either inside
    Discord decoration (<...>, backticks, whitespace)."""
    raw = str(text).strip(_DECORATION)
    if '#' in raw:
        raw = raw.split('#', 1)[1]
    return raw.strip(_DECORATION)


def decode_invite(text: Any) -> InviteCode:
    damaged = InviteError('The invite is damaged; ask the leader for a new one.')
    raw = extract_invite(text)
    if len(raw) > INVITE_MAX_CHARS or not raw.startswith(INVITE_PREFIX):
        raise InviteError('This is not an EDMRN crew invite.')
    body = raw[len(INVITE_PREFIX):]
    if not _B64URL.fullmatch(body):
        raise damaged
    try:
        data = base64.urlsafe_b64decode(body + '=' * (-len(body) % 4))
        obj = ev.load_json(data, INVITE_MAX_CHARS, 'invite')
    except (binascii.Error, ValueError):
        raise damaged from None
    if not isinstance(obj, dict) or set(obj) != {'g', 'i', 'k', 'l', 'u'}:
        raise damaged
    if not (ev.is_uuid7(obj['g']) and ev.is_uuid7(obj['i']) and ev.is_hex64(obj['k'])
            and ev.is_author(obj['l'])):
        raise damaged
    return InviteCode(check_relay_url(obj['u']), obj['g'], obj['i'], obj['k'], obj['l'])


def invite_link(code_text: str, relay_url: str) -> str:
    parts = urlsplit(check_relay_url(relay_url))
    scheme = 'https' if parts.scheme == 'wss' else 'http'
    return f'{scheme}://{parts.netloc}/join#{code_text}'


def check_webhook(url: Any) -> str:
    if isinstance(url, str) and _WEBHOOK.fullmatch(url.strip()):
        return url.strip()
    raise InviteError('This is not a Discord webhook URL (https://discord.com/api/webhooks/...).')


def redact_webhook(url: str) -> str:
    match = _WEBHOOK.fullmatch(url) if isinstance(url, str) else None
    return f'https://discord.com/api/webhooks/{match.group(1)}/***' if match else '<webhook>'


@dataclass(frozen=True)
class GroupConfig:
    url: str
    group_id: str
    leader: str
    member: str
    webhook: Optional[str] = None
    paused: bool = False

    @property
    def is_leader(self) -> bool:
        return self.member == self.leader


_GROUP_KEYS = {'v', 'url', 'group_id', 'leader', 'member', 'webhook', 'paused'}


def load_group(directory: Any) -> Optional[GroupConfig]:
    path = Path(directory) / GROUP_FILE
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as e:
        raise GroupConfigError(f'{GROUP_FILE}: {e}') from e
    if (not isinstance(obj, dict) or set(obj) != _GROUP_KEYS or type(obj['v']) is not int
            or obj['v'] != 1):
        raise GroupConfigError(f'{GROUP_FILE}: unknown format')
    try:
        url = check_relay_url(obj['url'])
        webhook = None if obj['webhook'] is None else check_webhook(obj['webhook'])
    except InviteError as e:
        raise GroupConfigError(f'{GROUP_FILE}: {e}') from e
    if not (ev.is_uuid7(obj['group_id']) and ev.is_author(obj['leader'])
            and ev.is_author(obj['member']) and isinstance(obj['paused'], bool)):
        raise GroupConfigError(f'{GROUP_FILE}: unknown format')
    return GroupConfig(url, obj['group_id'], obj['leader'], obj['member'], webhook, obj['paused'])


def save_group(directory: Any, config: GroupConfig) -> None:
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / GROUP_FILE
    tmp = path.with_name(path.name + '.tmp')
    record = {'v': 1, 'url': config.url, 'group_id': config.group_id, 'leader': config.leader,
              'member': config.member, 'webhook': config.webhook, 'paused': config.paused}
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(record, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def delete_group(directory: Any) -> None:
    (Path(directory) / GROUP_FILE).unlink(missing_ok=True)
