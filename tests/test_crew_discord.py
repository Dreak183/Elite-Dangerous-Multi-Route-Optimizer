import pytest

from edmrn.ext import api
from edmrn.ext.crew_discord import invite_message, post_invite
from edmrn.ext.crew_roster import InviteError
from fakes import FakeResponse, Script

HOOK = "https://discord.com/api/webhooks/123/SECRET-TOKEN"
LINK = "https://galroute.the-hub.cloud/join#edmrn-crew:1:abc"


def test_message_never_pings_and_carries_the_link():
    message = invite_message("Crew @everyone", "Alice", "2026-10-14T10:00:00Z", LINK)
    assert message["allowed_mentions"] == {"parse": []}
    assert LINK in message["content"] and "Alice" in message["content"]
    assert "2026-10-14 10:00:00 UTC" in message["content"] and len(message["content"]) <= 2000


def test_post_sends_wait_true_through_api_call_with_a_redacted_log_url():
    call = Script(FakeResponse(200, {"id": "1"}))
    post_invite(HOOK, {"content": "x", "allowed_mentions": {"parse": []}}, call=call)
    (args, kwargs), = call.calls
    assert args == ("POST", HOOK) and kwargs["params"] == {"wait": "true"}
    assert kwargs["service"] == "Discord" and "SECRET-TOKEN" not in kwargs["log_url"]
    assert kwargs["json"]["allowed_mentions"] == {"parse": []}


def test_a_429_is_retried_once_after_retry_after():
    slept = []
    call = Script(FakeResponse(429, {"retry_after": 1.5}), FakeResponse(204))
    post_invite(HOOK, {"content": "x"}, call=call, sleep=slept.append)
    assert slept == [1.5] and len(call.calls) == 2


@pytest.mark.parametrize("responses,posts", [
    ([FakeResponse(429, {"retry_after": 60})], 1),
    ([FakeResponse(429, {"retry_after": 1}), FakeResponse(429, {"retry_after": 1})], 2),
    ([FakeResponse(401, {"message": "Invalid Webhook Token"})], 1),
])
def test_failures_raise_one_sentence_without_the_token(responses, posts):
    call = Script(*responses)
    with pytest.raises(api.ApiError) as info:
        post_invite(HOOK, {"content": "x"}, call=call, sleep=lambda s: None)
    assert "SECRET-TOKEN" not in str(info.value) and str(info.value).endswith(".")
    assert len(call.calls) == posts


def test_a_bad_webhook_url_is_refused_before_any_request():
    call = Script()
    with pytest.raises(InviteError):
        post_invite("https://evil.example/api/webhooks/1/x", {"content": "x"}, call=call)
    assert call.calls == []


def test_an_over_long_name_is_cut_so_the_link_survives():
    message = invite_message("G" * 5000, "L" * 5000, "2026-10-14T10:00:00Z", LINK)
    assert message["content"].endswith(LINK) and len(message["content"]) <= 2000


def test_post_invite_through_the_real_api_call_redacts_and_sends_the_body(caplog):
    import logging
    transport = Script(FakeResponse(200, {"id": "1"}))
    body = {"content": "x", "allowed_mentions": {"parse": []}}

    def real_call(*args, **kwargs):
        return api.call(*args, transport=transport, **kwargs)
    with caplog.at_level(logging.DEBUG, logger=""):
        post_invite(HOOK, body, call=real_call)
    (args, kwargs), = transport.calls
    assert args == ("POST", HOOK) and kwargs["params"] == {"wait": "true"}
    assert kwargs["json"] == body
    assert "SECRET-TOKEN" not in " ".join(r.getMessage() for r in caplog.records)
