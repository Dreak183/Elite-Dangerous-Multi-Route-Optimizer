import itertools
import sqlite3
import threading
import time
from datetime import datetime, timezone

import pytest

from crew_helpers import note, sealed, visit
from crew_sync_fakes import NONCE, BlockingSocket, Clock, FakeSocket, RecordingStop, hello, relay
from fakes import Root
from journal_helpers import wait_for
from edmrn.ext import crew_events as ev
from edmrn.ext import crew_protocol as proto
from edmrn.ext.crew_identity import KeyRing, verify
from edmrn.ext.crew_roster import GroupConfig
from edmrn.ext.crew_store import CrewStore
from edmrn.ext.crew_sync import CrewBoard, CrewSync, Inbox, SocketClosed
from edmrn.ext.store import Store

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
G = "01920000-0000-7000-8000-0000000000a1"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "edmrn.db")
    yield s
    s.close()


@pytest.fixture
def crew(store):
    return CrewStore(store, clock=lambda: NOW, admit=lambda a, s: True)


@pytest.fixture
def me(tmp_path):
    return KeyRing(tmp_path / "crew_keys").for_commander("ME")


@pytest.fixture
def friend(tmp_path):
    return KeyRing(tmp_path / "friend").for_commander("FR")


class DirectInbox:
    """Stands in for an Inbox the Tk loop has already drained: received items are stored at once."""

    def __init__(self, crew):
        self.crew = crew

    def put(self, items, stop):
        self.crew.insert_received_batch(items)


def make_sync(crew, me, sock, leader=None, clock=None, status=lambda: None, paused=False,
              inbox=None, wall=lambda: "2026-10-13T12:00:00Z", connect=None):
    config = GroupConfig("wss://galroute.the-hub.cloud", G, leader or "L" * 43, me.author, None, paused)
    inbox = inbox or Inbox(crew, Root())
    sync = CrewSync(crew, config, me, inbox, CrewBoard(), status, connect=connect or (lambda url: sock),
                    clock=clock or Clock(), wall=wall, rand=lambda: 0.5)
    return sync, inbox


def stepping_clock():
    """Every call is 10 s later than the one before, so every tick is due for a flush."""
    ticks = itertools.count(0, 10)
    return lambda: 1000.0 + next(ticks)


def own_visits(crew, me, count):
    crew.insert_received_batch([sealed(me, visit(at=f"2026-10-13T09:{n // 60:02d}:{n % 60:02d}Z",
                                                 address=str(n)), n) for n in range(1, count + 1)])


def uploaded_frame(frame):
    return [(env["author"], env["seq"]) for env in (ev.parse_envelope(d) for d, _ in proto.parse_evs(frame))]


def uploaded(sock):
    """(author, seq) of every envelope sent in evs frames, in order."""
    out = []
    for frame in sock.sent_of("evs"):
        for data, _ in proto.parse_evs(frame):
            env = ev.parse_envelope(data)
            out.append((env["author"], env["seq"]))
    return out


def test_auth_signs_the_nonce_then_sends_have(crew, me):
    sock = FakeSocket(relay(), [hello()])
    sync, _ = make_sync(crew, me, sock)
    assert sync.connect_once() == "live"
    auth, have = sock.sent[0], sock.sent[1]
    assert auth["t"] == "auth" and auth["group"] == G and auth["author"] == me.author
    assert verify(me.author, proto.unb64(auth["sig"], 64), proto.auth_message(G, NONCE))
    assert have == {"t": "have", "heads": {}}
    assert "join" not in auth and "genesis" not in auth


def test_a_member_auth_carries_its_join_and_a_leader_auth_its_genesis(crew, me):
    join = ev.build_envelope(ev.uuid7(), me.author, 1, "join", "2026-10-13T10:00:00Z",
                             {"group_id": G, "invite_id": ev.uuid7(), "token": "11" * 32, "commander": "Me"})
    crew.insert_received_batch([ev.seal(join, me.sign)])
    sock = FakeSocket(relay(), [hello()])
    make_sync(crew, me, sock)[0].connect_once()
    assert "join" in sock.sent[0]
    genesis = ev.build_envelope(ev.uuid7(), me.author, 2, "group", "2026-10-13T10:00:00Z",
                                {"group_id": G, "name": "Crew", "commander": "Me"})
    crew.insert_received_batch([ev.seal(genesis, me.sign)])
    sock = FakeSocket(relay(), [hello()])
    make_sync(crew, me, sock, leader=me.author)[0].connect_once()
    assert "genesis" in sock.sent[0] and "join" not in sock.sent[0]


def test_catch_up_uploads_what_the_relay_lacks_in_pages_and_order(crew, me):
    own_visits(crew, me, 250)
    sock = FakeSocket(relay(heads={me.author: 100}), [hello()])
    sync, _ = make_sync(crew, me, sock)
    sync.connect_once()
    frames = sock.sent_of("evs")
    assert [seq for _, seq in uploaded(sock)] == list(range(101, 251))
    assert [len(f["items"]) for f in frames] == [100, 50]
    assert all(len(proto.encode(f)) <= proto.MAX_EVS_BYTES + 64 for f in frames)


def test_a_large_first_upload_keeps_frames_under_512_kib_in_ascending_seq(crew, me, friend):
    # Review focus 4: ~6 KB envelopes, 150 of them, from two authors; a page of 100 would exceed 512 KiB.
    big = "€" * 1900  # 3 bytes each in UTF-8: about 5.7 KB per envelope, 7.6 KB in base64
    for author in (me, friend):
        crew.insert_received_batch([sealed(author, note(at=f"2026-10-13T09:00:{n % 60:02d}Z", text=big), n)
                                    for n in range(1, 151)])
    sock = FakeSocket(relay(heads={me.author: 0, friend.author: 0}), [hello()])
    make_sync(crew, me, sock)[0].connect_once()
    frames = sock.sent_of("evs")
    assert len(frames) > 4 and all(len(f["items"]) <= 100 for f in frames)
    assert all(len(proto.encode(f)) <= proto.MAX_EVS_BYTES + 64 for f in frames)
    sent = uploaded(sock)
    assert len(sent) == 300
    for author in (me.author, friend.author):
        assert [seq for a, seq in sent if a == author] == list(range(1, 151))


def test_only_authors_the_relay_lists_are_uploaded(crew, me, friend):
    own_visits(crew, me, 3)
    crew.insert_received_batch([sealed(friend, visit(at="2026-10-13T09:00:00Z"), 1)])
    sock = FakeSocket(relay(heads={me.author: 0}), [hello()])
    make_sync(crew, me, sock)[0].connect_once()
    assert {author for author, _ in uploaded(sock)} == {me.author}


def test_an_emptied_relay_is_offered_the_joins_of_unlisted_members(crew, me, friend):
    join = ev.build_envelope(ev.uuid7(), friend.author, 4, "join", "2026-10-13T10:00:00Z",
                             {"group_id": G, "invite_id": ev.uuid7(), "token": "11" * 32, "commander": "Fr"})
    crew.insert_received_batch([ev.seal(join, friend.sign)])
    sock = FakeSocket(relay(heads={me.author: 0}), [hello()])
    make_sync(crew, me, sock)[0].connect_once()
    offered = [ev.parse_envelope(d) for f in sock.sent_of("evs") for d, _ in proto.parse_evs(f)]
    assert [(e["author"], e["kind"]) for e in offered] == [(friend.author, "join")]


def test_a_fresh_have_uploads_a_newly_listed_author_without_resending(crew, me, friend):
    crew.insert_received_batch([sealed(friend, visit(at="2026-10-13T09:00:00Z", address=str(n)), n)
                                for n in (1, 2)])
    own_visits(crew, me, 2)

    def on_send(sock, frame):
        if frame["t"] == "auth":
            sock.push({"t": "have", "heads": {me.author: 0}})
            sock.push({"t": "have", "heads": {me.author: 0, friend.author: 0}})
    sock = FakeSocket(on_send, [hello()])
    make_sync(crew, me, sock)[0].connect_once()
    sent = uploaded(sock)
    assert sent.count((me.author, 1)) == 1 and (friend.author, 2) in sent


def test_an_error_for_an_author_stops_its_uploads_for_the_connection(crew, me):
    own_visits(crew, me, 3)

    def on_send(sock, frame):
        if frame["t"] == "auth":
            sock.push({"t": "err", "code": "quota", "author": me.author, "seq": 1})
            sock.push({"t": "have", "heads": {me.author: 0}})
    sock = FakeSocket(on_send, [hello()])
    make_sync(crew, me, sock, clock=stepping_clock())[0].connect_once()
    assert sock.sent_of("evs") == []


def quota_run(crew, me, seq):
    """Own 1..3 are uploaded and the relay answers `err quota` to that evs; the day then changes
    and own 4 is created. Returns (socket, sync, connect_once outcome)."""
    own_visits(crew, me, 3)
    day = {"stamp": "2026-10-13T23:59:00Z", "n": 0}

    def on_send(sock, frame):
        if frame["t"] == "auth":
            sock.push({"t": "have", "heads": {me.author: 0}})
        elif frame["t"] == "evs" and len(sock.sent_of("evs")) == 1:
            err = {"t": "err", "code": "quota", "author": me.author}
            if seq is not None:
                err["seq"] = seq
            sock.push(err)

    def on_recv(sock):
        day["n"] += 1
        if day["n"] == 5:
            day["stamp"] = "2026-10-14T00:00:01Z"
            crew.insert_received_batch([sealed(me, visit(at="2026-10-14T00:00:00Z", address="4"), 4)])
        if day["n"] <= 7 and not sock.incoming:
            sock.push({"t": "synced"})
    sock = FakeSocket(on_send, [hello()], on_recv)
    sync, _ = make_sync(crew, me, sock, clock=stepping_clock(), wall=lambda: day["stamp"])
    return sock, sync, sync.connect_once()


def test_a_quota_block_lifts_at_the_day_change_and_resends_the_refused_envelopes(crew, me):
    # S11 + S7: the refused envelopes 1..3 are offered again, before 4, so the relay never has a gap.
    sock, _, _ = quota_run(crew, me, seq=1)
    batches = [[seq for _, seq in uploaded_frame(f)] for f in sock.sent_of("evs")]
    assert batches == [[1, 2, 3], [1, 2, 3, 4]]


def test_a_quota_refusal_without_a_seq_ends_the_session_at_the_day_change(crew, me):
    sock, sync, outcome = quota_run(crew, me, seq=None)
    assert [seq for _, seq in uploaded(sock)] == [1, 2, 3]  # 4 is never sent across the unknown hole
    assert outcome == "live" and "closed" in sync.last_error


def test_a_non_quota_block_survives_the_day_change(crew, me):
    own_visits(crew, me, 2)
    day = {"stamp": "2026-10-13T23:59:00Z", "n": 0}

    def on_send(sock, frame):
        if frame["t"] == "auth":
            sock.push({"t": "err", "code": "bad_sig", "author": me.author, "seq": 1})
            sock.push({"t": "have", "heads": {me.author: 0}})

    def on_recv(sock):
        day["n"] += 1
        if day["n"] == 5:
            day["stamp"] = "2026-10-14T00:00:01Z"
        if day["n"] <= 7 and not sock.incoming:
            sock.push({"t": "synced"})
    sock = FakeSocket(on_send, [hello()], on_recv)
    make_sync(crew, me, sock, clock=stepping_clock(), wall=lambda: day["stamp"])[0].connect_once()
    assert sock.sent_of("evs") == []


def test_forwarded_envelopes_are_not_echoed_back(crew, me, friend):
    # S1 (ruling): an evs from the relay advances its head, so the next upload does not return it.
    crew.insert_received_batch([sealed(friend, visit(at="2026-10-13T09:00:00Z", address=str(n)), n)
                                for n in range(1, 5)])
    data, sig = sealed(friend, visit(at="2026-10-13T09:00:00Z", address="5"), 5)
    sock = FakeSocket(relay(heads={me.author: 0, friend.author: 4},
                            extra=[{"t": "evs", "items": [{"e": proto.b64(data), "s": proto.b64(sig)}]}]),
                      [hello()])
    sync, _ = make_sync(crew, me, sock, clock=stepping_clock(), inbox=DirectInbox(crew))
    sync.connect_once()
    assert crew.heads()[friend.author] == 5
    assert sock.sent_of("evs") == []


def test_flush_soon_uploads_at_the_next_tick(crew, me):
    # B4 (ruling): Task 9 calls flush_soon after recording an invite or a remove.
    seen = {"n": 0}

    def on_recv(sock):
        seen["n"] += 1
        if seen["n"] == 4:
            own_visits(crew, me, 1)
            seen["before"] = len(sock.sent_of("evs"))
            sync.flush_soon()
            sock.push({"t": "synced"})
    sock = FakeSocket(relay(heads={me.author: 0}), [hello()], on_recv)
    sync, _ = make_sync(crew, me, sock)  # constant clock: only flush_soon can trigger the upload
    sync.connect_once()
    assert seen["before"] == 0 and uploaded(sock) == [(me.author, 1)]


def test_without_flush_soon_a_new_envelope_waits_for_the_interval(crew, me):
    seen = {"n": 0}

    def on_recv(sock):
        seen["n"] += 1
        if seen["n"] == 4:
            own_visits(crew, me, 1)
            sock.push({"t": "synced"})
    sock = FakeSocket(relay(heads={me.author: 0}), [hello()], on_recv)
    make_sync(crew, me, sock)[0].connect_once()
    assert sock.sent_of("evs") == []


def test_received_evs_reach_the_store_through_the_inbox(crew, me, friend):
    data, sig = sealed(friend, visit(at="2026-10-13T09:00:00Z"), 1)
    sock = FakeSocket(relay(extra=[{"t": "evs", "items": [{"e": proto.b64(data), "s": proto.b64(sig)}]}]),
                      [hello()])
    sync, inbox = make_sync(crew, me, sock)
    sync.connect_once()
    assert sync.state == "live"
    assert inbox.pending == 1 and crew.heads() == {}
    assert inbox.drain() == 1 and crew.heads() == {friend.author: 1}


def test_inbox_requeues_on_a_locked_store(me, friend):
    class Locked:
        def insert_received_batch(self, items):
            raise sqlite3.OperationalError("database is locked")
    inbox = Inbox(Locked(), Root())
    item = sealed(friend, visit(), 1)
    inbox._queue.extend([item, item])
    assert inbox.drain() == 0 and inbox.pending == 2


def test_inbox_ticks_drain_in_batches_and_stop_ends_the_chain(crew, friend):
    root = Root()
    inbox = Inbox(crew, root)
    inbox.BATCH = 2
    inbox._queue.extend(sealed(friend, visit(address=str(n)), n) for n in range(1, 6))
    inbox.start()
    assert len(root.queue) == 1
    root.queue.pop(0)()
    assert inbox.pending == 3 and len(root.queue) == 1
    root.queue.pop(0)()
    root.queue.pop(0)()
    assert inbox.pending == 0 and crew.heads() == {friend.author: 5}
    inbox.stop()
    root.queue.pop(0)()
    assert root.queue == []


def test_inbox_isolates_a_poison_envelope_by_retrying_one_by_one(friend):
    class Picky:
        def __init__(self):
            self.stored = []

        def insert_received_batch(self, items):
            if len(items) > 1:
                raise ValueError("boom")
            if items[0] == "poison":
                raise ValueError("poison")
            self.stored.append(items[0])
            return type("R", (), {"inserted": 1, "rejected": []})()
    picky = Picky()
    inbox = Inbox(picky, Root())
    inbox._queue.extend(["a", "poison", "b"])
    assert inbox.drain() == 2 and picky.stored == ["a", "b"]


def test_a_full_inbox_holds_the_sync_thread_until_the_tk_loop_drains(crew, friend):
    inbox = Inbox(crew, Root())
    inbox.CAP = 2
    inbox.start()
    items = [sealed(friend, visit(address=str(n)), n) for n in range(1, 5)]
    inbox.put(items[:2], threading.Event())
    done = threading.Event()
    worker = threading.Thread(target=lambda: (inbox.put(items[2:], threading.Event()), done.set()))
    worker.start()
    assert not done.wait(0.3)
    inbox.drain(2)
    assert wait_for(done.is_set)
    worker.join()
    inbox.stop()
    assert inbox.pending == 2


def test_a_stopping_sync_is_not_held_by_a_full_inbox_and_the_queue_stays_bounded(crew, friend):
    inbox = Inbox(crew, Root())
    inbox.CAP = 1
    inbox.start()
    stop = threading.Event()
    stop.set()
    inbox.put([sealed(friend, visit(address=str(n)), n) for n in (1, 2)], stop)
    assert inbox.pending == 1


def test_a_stopped_inbox_stays_bounded(crew, friend):
    inbox = Inbox(crew, Root())  # never started: nobody drains
    inbox.CAP = 2
    inbox.put([sealed(friend, visit(address=str(n)), n) for n in range(1, 5)], threading.Event())
    assert inbox.pending == 2


def test_a_lock_during_the_one_by_one_retry_requeues_the_rest_in_order():
    class Flaky:
        def __init__(self):
            self.stored, self.locked_once = [], False

        def insert_received_batch(self, items):
            if len(items) > 1:
                raise ValueError("boom")
            if items[0] == "b" and not self.locked_once:
                self.locked_once = True
                raise sqlite3.OperationalError("database is locked")
            self.stored.append(items[0])
            return type("R", (), {"inserted": 1, "rejected": []})()
    flaky = Flaky()
    inbox = Inbox(flaky, Root())
    inbox._queue.extend(["a", "b", "c"])
    assert inbox.drain() == 1 and list(inbox._queue) == ["b", "c"]
    assert inbox.drain() == 2 and flaky.stored == ["a", "b", "c"]


def test_backoff_doubles_to_five_minutes_and_has_jitter(crew, me):
    sync, _ = make_sync(crew, me, None)
    sync._connect = lambda url: (_ for _ in ()).throw(OSError("offline"))
    sync._stop = RecordingStop(limit=11)
    sync._run()
    assert sync._stop.waits == [1, 2, 4, 8, 16, 32, 64, 128, 256, 300, 300]
    sync._rand = lambda: 1.0
    sync._stop = RecordingStop(limit=1)
    sync._run()
    assert sync._stop.waits == [pytest.approx(1.2)]


@pytest.mark.parametrize("code", [proto.CLOSE_BAD_AUTH, proto.CLOSE_FORBIDDEN])
def test_a_refusal_stops_reconnecting(crew, me, code):
    # 4001 bad auth and 4003 not a member / removed (spec S9) both end the retry loop.
    sock = FakeSocket(None, [hello()])
    sock.push_close(code, "not_member")
    sync, _ = make_sync(crew, me, sock)
    sync._stop = RecordingStop(limit=5)
    sync._run()
    assert sync.state == "refused" and sync._stop.waits == [] and "refused" in sync.last_error


def test_a_replaced_connection_waits_a_minute(crew, me):
    sock = FakeSocket(relay(), [hello()])
    sock.on_send = None
    sock.push_close(proto.CLOSE_REPLACED, "replaced")
    sync, _ = make_sync(crew, me, sock)
    sync._stop = RecordingStop(limit=1)
    sync._run()
    assert sync._stop.waits == [60]


def test_a_replaced_connection_does_not_flap_on_repeat(crew, me):
    # Review focus 1: two installations with one key; each reconnect is replaced again.
    def fresh(url):
        sock = FakeSocket(None, [hello()])
        sock.push_close(proto.CLOSE_REPLACED, "replaced")
        return sock
    sync, _ = make_sync(crew, me, None, connect=fresh)
    sync._stop = RecordingStop(limit=4)
    sync._run()
    assert sync._stop.waits == [60, 60, 60, 60]


def test_a_bad_hello_fails_the_attempt(crew, me):
    sock = FakeSocket(None, [{"t": "hello", "v": 2, "nonce": NONCE}])
    sync, _ = make_sync(crew, me, sock)
    assert sync.connect_once() == "failed" and "version" in sync.last_error


def test_an_unexpected_error_fails_the_attempt_and_the_loop_continues(crew, me):
    # S13 (ruling): nothing escapes the thread; the state does not stay at "syncing".
    class Broken:
        def __getattr__(self, name):
            def boom(*a, **k):
                raise RuntimeError("store exploded")
            return boom
    sock = FakeSocket(relay(), [hello()])
    sync, _ = make_sync(Broken(), me, sock)
    assert sync.connect_once() == "failed" and "RuntimeError" in sync.last_error
    assert sock.closed is not None
    sock2 = FakeSocket(relay(), [hello()])
    sync, _ = make_sync(Broken(), me, sock2)
    sync._stop = RecordingStop(limit=2)
    sync._run()
    assert sync._stop.waits == [1, 2] and sync.state == "stopped"


def test_the_thread_connects_and_stop_closes_the_socket(crew, me):
    sock = BlockingSocket(relay(), [hello()])
    sync, _ = make_sync(crew, me, sock)
    sync.start()
    assert wait_for(lambda: sync.state == "live")
    sync.stop()
    assert sync.state == "stopped" and sock.closed == 1000
    assert not sync._thread.is_alive()


def test_the_websockets_adapter_maps_a_close_and_refuses_binary():
    from websockets.exceptions import ConnectionClosed
    from websockets.frames import Close
    from edmrn.ext.crew_sync import _WebSocketsAdapter

    class Ws:
        def __init__(self, result):
            self.result = result

        def recv(self, timeout):
            if isinstance(self.result, BaseException):
                raise self.result
            return self.result

    closed = ConnectionClosed(Close(4003, "not_member"), None)
    with pytest.raises(SocketClosed) as caught:
        _WebSocketsAdapter(Ws(closed), ConnectionClosed).recv(1)
    assert (caught.value.code, caught.value.reason) == (4003, "not_member")
    with pytest.raises(proto.FrameError):
        _WebSocketsAdapter(Ws(b"\x00"), ConnectionClosed).recv(1)
    assert _WebSocketsAdapter(Ws("{}"), ConnectionClosed).recv(1) == "{}"
    with pytest.raises(TimeoutError):
        _WebSocketsAdapter(Ws(TimeoutError()), ConnectionClosed).recv(1)


def test_the_board_keeps_the_newest_status_and_notifies():
    board = CrewBoard()
    seen = []
    board.subscribe(seen.append)
    assert board.update("A", "2026-10-13T10:00:00Z", {"system": "Sol"})
    assert not board.update("A", "2026-10-13T09:00:00Z", {"system": "Old"})
    assert board.snapshot()["A"].status == {"system": "Sol"} and len(seen) == 1


def test_uploads_read_the_log_in_pages_of_at_most_100(crew, me, friend):
    # Review focus 4: a first upload of thousands of envelopes never reads more than 100 at a time.
    class Spy:
        def __init__(self, inner):
            self.inner, self.reads = inner, []

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def envelopes_after(self, author, after_seq, limit=100):
            rows = self.inner.envelopes_after(author, after_seq, limit)
            self.reads.append((limit, len(rows)))
            return rows
    own_visits(crew, me, 250)
    crew.insert_received_batch([sealed(friend, visit(at="2026-10-13T09:00:00Z", address=str(n)), n)
                                for n in range(1, 131)])
    spy = Spy(crew)
    sock = FakeSocket(relay(heads={me.author: 0, friend.author: 0}), [hello()])
    make_sync(spy, me, sock)[0].connect_once()
    assert len(spy.reads) >= 5 and all(limit <= 100 and got <= 100 for limit, got in spy.reads)
    assert len(uploaded(sock)) == 380


def sessions(outcomes):
    """A connect() that serves `outcomes` in turn: an exception is raised, 'live' is a socket that
    reaches synced and then closes, 'replaced' one that is replaced."""
    queue = list(outcomes)

    def connect(url):
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        sock = FakeSocket(relay(), [hello()])
        if item == "replaced":
            sock.on_send = None
            sock.push_close(proto.CLOSE_REPLACED, "replaced")
        return sock
    return connect


def test_a_session_that_dies_young_does_not_reset_the_backoff(crew, me):
    sync, _ = make_sync(crew, me, None, connect=sessions(["live"] * 3))
    sync._stop = RecordingStop(limit=3)
    sync._run()
    assert sync._stop.waits == [1, 2, 4]


def test_a_session_that_lived_long_resets_the_backoff(crew, me):
    clock = iter(range(0, 10_000, 100))
    sync, _ = make_sync(crew, me, None, connect=sessions([OSError("x"), "live", "live", "live"]),
                        clock=lambda: float(next(clock)))
    sync._stop = RecordingStop(limit=4)
    sync._run()
    assert sync._stop.waits == [1, 1, 1, 1]


def test_a_replacement_neither_resets_nor_doubles_the_backoff(crew, me):
    sync, _ = make_sync(crew, me, None, connect=sessions([OSError("x"), "replaced", OSError("y")]))
    sync._stop = RecordingStop(limit=3)
    sync._run()
    assert sync._stop.waits == [1, 60, 2]


def test_an_unlisted_author_is_neither_uploaded_nor_hides_its_join_offer(crew, me, friend):
    # S7: evs received before `have` must not make the relay "list" an author it did not list.
    join = ev.build_envelope(ev.uuid7(), friend.author, 4, "join", "2026-10-13T10:00:00Z",
                             {"group_id": G, "invite_id": ev.uuid7(), "token": "11" * 32, "commander": "Fr"})
    crew.insert_received_batch([ev.seal(join, friend.sign)])
    data, sig = sealed(friend, visit(at="2026-10-13T09:00:00Z", address="5"), 5)

    def on_send(sock, frame):
        if frame["t"] == "auth":
            sock.push({"t": "evs", "items": [{"e": proto.b64(data), "s": proto.b64(sig)}]})
            sock.push({"t": "have", "heads": {me.author: 0}})
    sock = FakeSocket(on_send, [hello()])
    make_sync(crew, me, sock, inbox=DirectInbox(crew))[0].connect_once()
    assert uploaded(sock) == [(friend.author, 4)]


class SlowClose(BlockingSocket):
    def close(self, code=1000, reason=""):
        time.sleep(1.5)
        super().close(code, reason)


def test_stop_does_not_wait_for_a_closing_handshake(crew, me):
    sock = SlowClose(relay(), [hello()])
    sync, _ = make_sync(crew, me, sock)
    sync.start()
    assert wait_for(lambda: sync.state == "live")
    began = time.monotonic()
    sync.stop()
    assert time.monotonic() - began < 1.0 and sync.state == "stopped"
    sync._thread.join(5)
    assert sock.closed == 1000


def test_start_after_a_stop_that_timed_out_runs_a_new_session(crew, me):
    # Task 9's reconnect(): the old run is stuck in connect() when stop() gives up waiting.
    release, entered = threading.Event(), threading.Event()
    old, new = BlockingSocket(relay(), [hello()]), BlockingSocket(relay(), [hello()])
    calls = []

    def connect(url):
        calls.append(url)
        if len(calls) == 1:
            entered.set()
            release.wait(5)
            return old
        return new
    sync, _ = make_sync(crew, me, None, connect=connect)
    sync.start()
    assert entered.wait(2)
    first = sync._thread
    sync.stop(timeout=0.05)
    sync.start()
    assert wait_for(lambda: sync.state == "live")
    release.set()
    first.join(3)
    assert not first.is_alive() and old.closed == 1000 and old.sent == []
    assert sync.state == "live" and new.closed is None
    sync.stop()
    sync._thread.join(3)


class DeferredThread:
    """A thread that does not run until the test says so: the old run is 'not yet scheduled'."""
    made = []

    def __init__(self, target=None, args=(), name=None, daemon=None):
        self.target, self.args, self.started, self.ran = target, args, False, False
        DeferredThread.made.append(self)

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started and not self.ran

    def join(self, timeout=None):
        pass

    def run(self):
        self.target(*self.args)
        self.ran = True


def test_a_delayed_old_run_does_not_adopt_the_next_runs_stop_event(crew, me, monkeypatch):
    # start(); stop(); start() before the first thread is scheduled: only the last run may connect.
    DeferredThread.made = []
    monkeypatch.setattr(threading, "Thread", DeferredThread)
    calls = []

    def connect(url):
        calls.append(url)
        sync._stop.set()  # end the run after this one attempt
        raise OSError("offline")
    sync, _ = make_sync(crew, me, None, connect=connect)
    sync.start()
    sync.stop()
    sync.start()
    first, second = DeferredThread.made
    first.run()
    assert calls == []
    second.run()
    assert len(calls) == 1


def test_a_superseded_session_does_not_consume_a_flush_request(crew, me):
    sync, _ = make_sync(crew, me, None)
    from edmrn.ext.crew_sync import _Session
    old = threading.Event()
    session = _Session(sync, FakeSocket(), old)
    session.relay_heads = {}
    sync.flush_soon()
    session.upload()
    assert sync._flush_requested is True
    current = _Session(sync, FakeSocket(), sync._stop)
    current.relay_heads = {}
    current.upload()
    assert sync._flush_requested is False


def test_a_stopped_run_publishes_no_state_but_its_final_stopped(crew, me):
    sync, _ = make_sync(crew, me, None)
    stop = sync._stop
    stop.set()
    sync._set_state("live", stop=stop)
    sync._set_state("refused", "late", stop=stop)
    sync._note("late", stop)
    assert sync.state == "stopped" and sync.last_error is None
    sync.state = "waiting"
    sync._set_state("stopped", stop=stop)
    assert sync.state == "stopped"


def test_stop_called_from_the_sync_thread_itself_does_not_join_itself(crew, me, caplog):
    sock = BlockingSocket(relay(), [hello()])
    sync, _ = make_sync(crew, me, sock)
    seen = []

    def observer(state, error):
        if state == "live" and not seen:
            seen.append(state)
            sync.stop()  # runs on the sync thread: joining that thread would raise RuntimeError

    sync.subscribe(observer)
    with caplog.at_level("ERROR"):
        sync.start()
        assert wait_for(lambda: seen and not sync._thread.is_alive())
    assert sync.state == "stopped"
    assert not [r for r in caplog.records if "observer failed" in r.getMessage()]


@pytest.mark.loopback
def test_websockets_connect_does_not_use_the_deprecated_bare_connect():
    import warnings
    from websockets.sync.server import serve
    from edmrn.ext.crew_sync import websockets_connect

    def handler(conn):
        conn.send("hello")

    with serve(handler, "127.0.0.1", 0) as server:
        port = server.socket.getsockname()[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            ws = websockets_connect(f"ws://127.0.0.1:{port}/")
            try:
                assert ws.recv(5) == "hello"
            finally:
                ws.close()
                server.shutdown()
