import json

import pytest

from edmrn.ext import crew_events as ev
from edmrn.ext import crew_protocol as proto
from edmrn.ext.crew_identity import KeyRing

G = "01920000-0000-7000-8000-0000000000a1"


@pytest.fixture
def me(tmp_path):
    return KeyRing(tmp_path / "k").for_commander("C1")


def test_b64_round_trip_and_strictness():
    assert proto.unb64(proto.b64(b"\x00\xff" * 10), 20) == b"\x00\xff" * 10
    for bad in ("a=", "a+b", "ab c", "A" * 100, 5, None):
        with pytest.raises(proto.FrameError):
            proto.unb64(bad, 20)
    with pytest.raises(proto.FrameError):
        proto.unb64("AB", 1)  # non-canonical: trailing bits set


def test_decode_refuses_bad_frames():
    for text in ('{"t":"x","t":"y"}', "[]", '{"x":1}', '{"t":5}', "x" * (proto.MAX_FRAME_BYTES + 1), b"{}"):
        with pytest.raises(proto.FrameError):
            proto.decode(text)
    assert proto.decode('{"t":"synced"}') == {"t": "synced"}


def test_auth_message_and_frame(me):
    nonce = proto.b64(bytes(range(32)))
    assert proto.auth_message(G, nonce) == f"edmrn-crew-auth\n{G}\n{nonce}".encode("ascii")
    frame = proto.auth_frame(G, me.author, b"s" * 64, join=(b"env", b"sig"))
    assert frame == {"t": "auth", "group": G, "author": me.author, "sig": proto.b64(b"s" * 64),
                     "join": {"e": proto.b64(b"env"), "s": proto.b64(b"sig")}}
    assert proto.parse_hello({"t": "hello", "v": 1, "source": "", "nonce": nonce}) == nonce
    for bad in ({"t": "hello", "v": 2, "nonce": nonce}, {"t": "hello", "v": 1, "nonce": "abc"}):
        with pytest.raises(proto.FrameError):
            proto.parse_hello(bad)


def test_have_round_trip_and_refusals(me, tmp_path):
    other = KeyRing(tmp_path / "o").for_commander("C9").author
    heads = {me.author: 7, other: 0}
    assert proto.parse_have(json.loads(proto.encode(proto.have_frame(heads)))) == heads
    for bad in ({"B" * 43: 1}, {"short": 1}, {me.author: -1}, {me.author: True}, {me.author: 2**53}):
        with pytest.raises(proto.FrameError):
            proto.parse_have({"t": "have", "heads": bad})


def test_evs_frames_split_by_count_and_bytes():
    small = [(b"e" * 10, b"s" * 64)] * 250
    frames = list(proto.evs_frames(small))
    assert [len(f["items"]) for f in frames] == [100, 100, 50]
    big = [(b"e" * 16000, b"s" * 64)] * 60
    frames = list(proto.evs_frames(big))
    assert all(len(proto.encode(f)) <= proto.MAX_EVS_BYTES + 64 for f in frames)
    assert sum(len(f["items"]) for f in frames) == 60
    assert proto.parse_evs(json.loads(proto.encode(frames[0])))[0] == big[0]


def test_parse_evs_refusals():
    good = {"e": proto.b64(b"e"), "s": proto.b64(b"s" * 64)}
    for items in ([good] * 101, [{"e": good["e"]}], [{"e": good["e"], "s": proto.b64(b"s" * 63)}],
                  [dict(good, x=1)], "nope"):
        with pytest.raises(proto.FrameError):
            proto.parse_evs({"t": "evs", "items": items})


def status_doc(author, **over):
    status = {name: None for name in ev.STATUS_FIELDS}
    status.update(paused=False, commander="Jameson", hull=0.5)
    status.update(over)
    return {"author": author, "at": "2026-10-13T12:00:00Z", "status": status}


def test_status_round_trip_verifies_signature_and_author(me, tmp_path):
    data = proto.status_bytes(status_doc(me.author))
    frame = proto.status_frame(data, me.sign(data))
    author, at, status = proto.parse_status(json.loads(proto.encode(frame)))
    assert (author, at, status["hull"]) == (me.author, "2026-10-13T12:00:00Z", 0.5)
    other = KeyRing(tmp_path / "o").for_commander("C2")
    with pytest.raises(proto.FrameError):
        proto.parse_status(proto.status_frame(data, other.sign(data)))
    bad = proto.status_bytes(status_doc(me.author, hull=7))
    with pytest.raises(proto.FrameError):
        proto.parse_status(proto.status_frame(bad, me.sign(bad)))


def test_status_bytes_size_cap(me):
    with pytest.raises(proto.FrameError):
        proto.status_bytes(status_doc(me.author, commander="x" * 9000))


def test_parse_err(me):
    assert proto.parse_err({"t": "err", "code": "quota", "author": me.author, "seq": 4}) == ("quota", me.author, 4)
    assert proto.parse_err({"t": "err", "code": "weird"}) == ("unknown", None, None)
    assert proto.parse_err({"t": "err", "code": "rate", "author": "x", "seq": "y"}) == ("rate", None, None)


def test_parse_err_unhashable_code_and_seq_bounds(me):
    for code in ([], {}, 5, None):
        assert proto.parse_err({"t": "err", "code": code}) == ("unknown", None, None)
    for seq in (-1, 2**53, True):
        assert proto.parse_err({"t": "err", "code": "rate", "author": me.author, "seq": seq}) == ("rate", me.author, None)
    assert proto.parse_err({"t": "err", "code": "quota", "author": me.author}) == ("quota", me.author, None)
    assert proto.parse_err({"t": "err", "code": "rate", "author": "B" * 43, "seq": 1}) == ("rate", None, None)


def test_parse_evs_aggregate_cap():
    item = {"e": proto.b64(b"e" * 16000), "s": proto.b64(b"s" * 64)}
    with pytest.raises(proto.FrameError):
        proto.parse_evs({"t": "evs", "items": [item] * 45})
    assert len(proto.parse_evs({"t": "evs", "items": [item] * 20})) == 20


def test_parse_hello_refuses_float_version():
    nonce = proto.b64(bytes(32))
    with pytest.raises(proto.FrameError):
        proto.parse_hello({"t": "hello", "v": 1.0, "nonce": nonce})


def test_status_must_be_canonical(me):
    data = json.dumps(status_doc(me.author), indent=2).encode()
    with pytest.raises(proto.FrameError):
        proto.parse_status(proto.status_frame(data, me.sign(data)))


def test_decode_refuses_non_printable_type():
    for text in ('{"t":"\ud800"}', '{"t":""}', '{"t":"a\nb"}', '{"t":"' + "x" * 33 + '"}'):
        with pytest.raises(proto.FrameError):
            proto.decode(text)


def test_decode_refuses_overflowing_float():
    with pytest.raises(proto.FrameError):
        proto.decode('{"t":"have","x":1e400}')
