"""Update check (A13): one attempt through edmrn.ext.api, honest failure message. No network."""
from types import SimpleNamespace

import pytest

import edmrn.updater as updater
from edmrn.ext.api import ApiError, ApiFailure
from fakes import FakeResponse, Script

RELEASES = "https://api.github.com/repos/NinurtaKalhu/Elite-Dangerous-Multi-Route-Optimizer/releases/latest"


def checker(monkeypatch, *items):
    fake = Script(*items)
    monkeypatch.setattr(updater, "call", fake)
    return updater.SimpleUpdateChecker("3.3.0"), fake


def test_single_attempt_to_the_upstream_repository(monkeypatch):
    c, fake = checker(monkeypatch, FakeResponse(200, {"tag_name": "v3.4.0", "html_url": "https://x"}))
    assert c.check_for_updates() is True and c.last_error is None
    args, kwargs = fake.calls[0]
    assert args == ("GET", RELEASES) and kwargs["service"] == "GitHub" and kwargs["retries"] == 0


@pytest.mark.parametrize("item,reason", [
    (ApiError(ApiFailure("GitHub", "offline")), "No connection to GitHub."),
    (ApiError(ApiFailure("GitHub", "timeout", None, "10 s")), "GitHub did not answer within 10 s."),
    (FakeResponse(403, {"message": "API rate limit exceeded"}), "GitHub rejected the request (HTTP 403)."),
])
def test_failure_reason_is_kept(monkeypatch, item, reason):
    c, _ = checker(monkeypatch, item)
    assert c.check_for_updates() is False and c.last_error == reason


@pytest.mark.parametrize("tag", ["", "nightly build", "v"])
def test_unparseable_tag_is_not_reported_as_up_to_date(monkeypatch, tag):
    c, _ = checker(monkeypatch, FakeResponse(200, {"tag_name": tag, "html_url": "https://x"}))
    assert c.check_for_updates() is False
    assert c.last_error and "version" in c.last_error


def test_unparseable_tag_manual_check_says_it_could_not_check(monkeypatch):
    [(title, text)] = manual(monkeypatch, FakeResponse(200, {"tag_name": "nightly build", "html_url": "x"}))
    assert text.startswith("Could not check for updates:") and "already up to date" not in text


def test_unexpected_exception_keeps_the_standard_sentence(monkeypatch):
    c, _ = checker(monkeypatch, RuntimeError("bug"))
    assert c.check_for_updates() is False and c.last_error == "Unexpected error talking to GitHub."


def manual(monkeypatch, *items):
    shown = []
    monkeypatch.setattr(updater, "InfoDialog", lambda app, title, text: shown.append((title, text)))
    manager = updater.UpdateManager.__new__(updater.UpdateManager)
    manager.app = SimpleNamespace(root=None)
    manager.update_checker, _ = checker(monkeypatch, *items)
    manager.manual_check()
    return shown


def test_failed_manual_check_says_so(monkeypatch):
    assert manual(monkeypatch, ApiError(ApiFailure("GitHub", "offline"))) == [
        ("Update Check", "Could not check for updates: No connection to GitHub.")]


def test_up_to_date_manual_check(monkeypatch):
    [(title, text)] = manual(monkeypatch, FakeResponse(200, {"tag_name": "v3.3.0", "html_url": "https://x"}))
    assert title == "Update Check" and "already up to date" in text
