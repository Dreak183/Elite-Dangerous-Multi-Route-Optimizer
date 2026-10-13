"""Posting a crew invite through the leader's Discord webhook (crew sync core spec S5a).

The webhook URL is a secret: it goes through api.call with a redacted log URL and never into an
error text. Discord answers rate limits with 429 and a retry_after in seconds.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from edmrn.ext import api
from edmrn.ext.crew_roster import check_webhook, redact_webhook

SERVICE = 'Discord'
MAX_RETRY_AFTER = 10.0
CONTENT_MAX = 2000


def invite_message(group_name: str, label: Optional[str], expires_at: str, link: str) -> dict:
    whom = f' for {label}' if label else ''
    expiry = expires_at.replace('T', ' ').replace('Z', ' UTC')
    content = (f'EDMRN crew invite{whom} to the group "{group_name}". It works once and expires '
               f'{expiry}.\nOpen the link, copy the code, then in EDMRN: Settings > Crew... > '
               f'Join with invite.\n{link}')
    return {'content': content[:CONTENT_MAX], 'allowed_mentions': {'parse': []}}


def _retry_after(response: Any) -> Optional[float]:
    try:
        value = response.json().get('retry_after')
    except Exception:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0 < value <= MAX_RETRY_AFTER else None


def post_invite(webhook_url: str, message: dict, *, call=api.call, sleep=time.sleep) -> None:
    """Post `message` to the webhook; raise InviteError for a bad URL, ApiError for a failed post.

    A 429 is retried once after Discord's retry_after. A retry_after above MAX_RETRY_AFTER (10 s),
    or a second 429, fails without waiting: the leader is not left blocked on a long rate limit.
    """
    url = check_webhook(webhook_url)
    shown = redact_webhook(url)
    for attempt in (1, 2):
        response = call('POST', url, service=SERVICE, params={'wait': 'true'}, json=message,
                        timeout=10, retries=1, log_url=shown)
        if response.status_code in (200, 204):
            return
        if response.status_code == 429 and attempt == 1:
            delay = _retry_after(response)
            if delay is not None:
                sleep(delay)
                continue
        raise api.ApiError(api.failure_from_response(response, SERVICE))
