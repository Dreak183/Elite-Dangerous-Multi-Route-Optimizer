import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ctk = pytest.importorskip("customtkinter")

from edmrn.ext.crew_dialog import CrewDialog, add_settings_card, open_dialog
from edmrn.ext.crew_group import CreatedInvite, GroupError
from edmrn.ext.crew_roster import GroupConfig, Member, Roster
from edmrn.ext.crew_sync import BoardEntry

LEADER, ALICE, BOB = "L" * 43, "A" * 43, "B" * 43
BLOCK_CRYPTOGRAPHY = """
import sys
class _Block:
    def find_spec(self, name, path=None, target=None):
        if name == 'cryptography' or name.startswith('cryptography.'):
            raise ModuleNotFoundError('blocked: ' + name)
sys.meta_path.insert(0, _Block())
"""


def run_isolated(script, tmp_path):
    """Run `script` in a clean interpreter from the repository root with HOME redirected."""
    home = str(tmp_path)
    env = dict(os.environ, HOME=home, USERPROFILE=home, APPDATA=home, LOCALAPPDATA=home)
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parent.parent, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return result.stdout


G = "01920000-0000-7000-8000-0000000000a1"
SOL = {"system": "Sol", "ship": "krait_light", "ship_name": "Wanderer"}


@pytest.fixture(scope="module")
def _tk_root():
    try:
        r = ctk.CTk()
    except Exception as e:
        pytest.skip(f"no Tk display: {e}")
    r.withdraw()
    yield r
    try:
        r.destroy()
    except Exception:
        pass


@pytest.fixture
def root(_tk_root):
    yield _tk_root
    for child in _tk_root.winfo_children():
        if isinstance(child, ctk.CTkToplevel):
            child.destroy()


class FakeGroup:
    def __init__(self, config=None, roster=None, post_result=None):
        self.config, self._roster, self.problem, self.calls = config, roster, None, []
        self.state = "live" if config else "no group"
        self.sync = SimpleNamespace(last_error=None)
        self.board = SimpleNamespace(snapshot=lambda: {})
        self.state_observers, self.board_observers = [], []
        self.post_result = post_result
        self.pause_error = None

    @property
    def roster(self):
        return self._roster

    def subscribe_state(self, observer):
        self.state_observers.append(observer)
        return lambda: self.state_observers.remove(observer)

    def subscribe_board(self, observer):
        self.board_observers.append(observer)
        return lambda: self.board_observers.remove(observer)

    def create_group(self, name, relay):
        self.calls.append(("create", name, relay))
        if name == "bad":
            raise GroupError("A group name is 1 to 64 plain ASCII characters.")

    def join(self, text):
        self.calls.append(("join", text))

    def create_invite(self, label, hours):
        self.calls.append(("invite", label, hours))
        return CreatedInvite("edmrn-crew:1:abc", "https://h/join#edmrn-crew:1:abc",
                             "2026-10-14T10:00:00Z", label)

    def post_invite(self, invite, done):
        self.calls.append(("post", invite.code))
        done(self.post_result)

    def remove(self, member):
        self.calls.append(("remove", member))

    def leave(self):
        self.calls.append(("leave",))
        self.config, self.problem, self.state = None, None, "no group"

    def reconnect(self):
        self.calls.append(("reconnect",))

    def set_paused(self, paused):
        self.calls.append(("pause", paused))
        if self.pause_error:
            raise GroupError(self.pause_error)

    def set_webhook(self, url):
        self.calls.append(("webhook", url))


def roster_of(*extra):
    members = {LEADER: Member(LEADER, "Leader", True, None), ALICE: Member(ALICE, "Alice", False, None)}
    members.update({m.author: m for m in extra})
    return Roster(G, LEADER, "Crew", members, {})


def leader_group(webhook=None):
    return FakeGroup(GroupConfig("wss://h", G, LEADER, LEADER, webhook), roster_of())


def dialog_for(root, group, author=LEADER, commander="Leader"):
    return CrewDialog(root, group, commander=commander, author=author, copy=[].append)


def test_no_group_view_creates_and_joins(root):
    group = FakeGroup()
    dialog = dialog_for(root, group)
    dialog.name_entry.insert(0, "Crew")
    dialog.create_button.invoke()
    assert group.calls[0] == ("create", "Crew", "wss://galroute.the-hub.cloud")
    dialog.invite_box.insert("1.0", "edmrn-crew:1:abc")
    dialog.join_button.invoke()
    assert group.calls[-1] == ("join", "edmrn-crew:1:abc")


def test_a_refusal_shows_its_sentence(root):
    dialog = dialog_for(root, FakeGroup())
    dialog.name_entry.insert(0, "bad")
    dialog.create_button.invoke()
    assert dialog.message.get() == "A group name is 1 to 64 plain ASCII characters."


def test_leader_view_invites_copies_posts_and_removes(root):
    copied = []
    group = leader_group(webhook="https://discord.com/api/webhooks/1/x")
    dialog = CrewDialog(root, group, commander="Leader", author=LEADER, copy=copied.append)
    dialog.label_entry.insert(0, "Alice")
    dialog.expiry.set("24 hours")
    dialog.invite_button.invoke()
    assert group.calls[-1] == ("invite", "Alice", 24)
    assert dialog.code_entry.get() == "edmrn-crew:1:abc" and dialog.link_entry.get().endswith("abc")
    dialog.copy_code_button.invoke()
    assert copied[-1] == "edmrn-crew:1:abc"
    dialog.post_button.invoke()
    assert group.calls[-1] == ("post", "edmrn-crew:1:abc")
    assert set(dialog.remove_buttons) == {ALICE}
    dialog.remove_buttons[ALICE].invoke()
    assert group.calls[-1] == ("post", "edmrn-crew:1:abc")  # the first click only asks
    dialog.remove_buttons[ALICE].invoke()
    assert group.calls[-1] == ("remove", ALICE)


def test_the_post_result_sentence_survives_the_rerender(root):
    """C4: the result may arrive before post_invite returns; nothing overwrites it afterwards."""
    group = leader_group(webhook="https://discord.com/api/webhooks/1/x")
    dialog = dialog_for(root, group)
    dialog.invite_button.invoke()
    dialog.post_button.invoke()
    assert dialog.message.get() == "Invite posted to Discord."
    group.post_result = "Discord refused the post (HTTP 404)."
    dialog.post_button.invoke()
    assert dialog.message.get() == "Discord refused the post (HTTP 404)."


def test_the_webhook_url_is_masked(root):
    dialog = dialog_for(root, leader_group(webhook="https://discord.com/api/webhooks/1/x"))
    assert dialog.webhook_entry.cget("show") == "*"
    dialog.webhook_entry.delete(0, "end")
    dialog.webhook_entry.insert(0, "https://discord.com/api/webhooks/2/y")
    dialog.webhook_button.invoke()
    assert dialog.message.get() == "Webhook saved."


def test_member_view_has_no_leader_controls_and_pauses(root):
    group = FakeGroup(GroupConfig("wss://h", G, LEADER, ALICE), roster_of())
    dialog = dialog_for(root, group, author=ALICE, commander="Alice")
    assert dialog.invite_button is None and dialog.remove_buttons == {}
    dialog.pause_switch.toggle()
    assert group.calls[-1] == ("pause", True)


def test_post_button_needs_a_webhook(root):
    dialog = dialog_for(root, leader_group())
    dialog.invite_button.invoke()
    assert dialog.post_button is None


def test_an_unusable_group_file_offers_leave(root):
    """S5: a group.json that cannot be read leaves no config; Leave is the way out."""
    group = FakeGroup()
    group.problem = "The group file cannot be used (bad); leave the group to start over."
    dialog = dialog_for(root, group)
    assert dialog.create_button is None and dialog.leave_button is not None
    dialog.leave_button.invoke()
    dialog.leave_button.invoke()
    assert group.calls == [("leave",)]
    assert dialog.create_button is not None and dialog.leave_button is None


def test_reconnect_is_offered_only_when_refused(root):
    """S6."""
    group = leader_group()
    dialog = dialog_for(root, group)
    assert dialog.reconnect_button is None
    group.state = "refused"
    dialog.render()
    dialog.reconnect_button.invoke()
    assert group.calls[-1] == ("reconnect",)


def test_statuses_show_for_active_members_only(root):
    removed = Member(BOB, "Bob", False, 7)
    group = FakeGroup(GroupConfig("wss://h", G, LEADER, LEADER), roster_of(removed))
    group.board = SimpleNamespace(snapshot=lambda: {
        ALICE: BoardEntry(ALICE, "2026-10-13T10:00:00Z", SOL),
        BOB: BoardEntry(BOB, "2026-10-13T10:00:00Z", SOL)})
    dialog = dialog_for(root, group)
    assert set(dialog.status_labels) == {LEADER, ALICE}
    assert dialog.status_labels[ALICE].cget("text") == "Sol - Wanderer"
    dialog.on_board(BoardEntry(BOB, "2026-10-13T10:01:00Z", {"system": "Elsewhere"}))
    dialog.on_board(BoardEntry(ALICE, "2026-10-13T10:01:00Z", {"system": "<b>Achenar</b>\n[x]", "ship": 5}))
    assert dialog.status_labels[ALICE].cget("text") == "<b>Achenar</b> [x]"
    assert BOB not in dialog.status_labels


def test_observers_rerender_from_the_current_state_and_stop_when_destroyed(root):
    group = leader_group()
    first = dialog_for(root, group)
    first.destroy()
    assert group.state_observers == [] and group.board_observers == []
    second = dialog_for(root, group)
    assert len(group.state_observers) == 1 and len(group.board_observers) == 1
    group.state = "refused"  # the notification carries nothing: the state is re-read
    group.state_observers[0]()
    assert second.reconnect_button is not None
    group.board_observers[0](BoardEntry(ALICE, "2026-10-13T10:00:00Z", SOL))
    assert second.status_labels[ALICE].cget("text") == "Sol - Wanderer"
    second.destroy()
    assert group.state_observers == [] and group.board_observers == []


def test_no_group_controller_shows_unavailable(root):
    dialog = dialog_for(root, None)
    assert dialog.create_button is None and dialog.leave_button is None


def test_open_dialog_reuses_a_live_dialog_and_survives_a_missing_crew(root):
    app = SimpleNamespace(root=root, crew=None)
    first = open_dialog(app)
    assert first is not None and open_dialog(app) is first
    first.destroy()
    assert open_dialog(app) is not first


def test_the_settings_card_builds(root):
    frame = ctk.CTkFrame(root)
    card = add_settings_card(SimpleNamespace(root=root, crew=None), frame,
                             {"frame": "gray20", "border": "gray40"})
    assert card.winfo_exists()


def test_the_dialog_imports_and_the_card_builds_without_cryptography(tmp_path):
    """S4: a clean interpreter with `cryptography` unimportable. An in-process check is a false
    green: the cached package attributes hide the import chain."""
    script = BLOCK_CRYPTOGRAPHY + """
import customtkinter as ctk
from types import SimpleNamespace
from edmrn.ext import crew_dialog
assert 'edmrn.ext.crew_identity' not in sys.modules and 'edmrn.ext.crew_sync' not in sys.modules
try:
    root = ctk.CTk()
except Exception:
    print('NO_TK')
    raise SystemExit(0)
root.withdraw()
app = SimpleNamespace(root=root, crew=None)
card = crew_dialog.add_settings_card(app, ctk.CTkFrame(root), {'frame': 'gray20', 'border': 'gray40'})
assert card.winfo_exists()
assert crew_dialog.open_dialog(app) is not None
print('OK')
"""
    out = run_isolated(script, tmp_path)
    if "NO_TK" in out:
        pytest.skip("no Tk display in the subprocess")
    assert out.strip().endswith("OK")


# --- fix round 1 ---------------------------------------------------------------------------

def test_state_changes_keep_typed_input_and_do_not_rebuild(root):
    group = leader_group(webhook="https://discord.com/api/webhooks/1/x")
    dialog = dialog_for(root, group)
    dialog.webhook_entry.delete(0, "end")
    dialog.webhook_entry.insert(0, "https://discord.com/api/webhooks/2/typed")
    dialog.label_entry.insert(0, "Alice")
    dialog.expiry.set("1 hour")
    entry = dialog.label_entry
    for state in ("connecting", "waiting", "refused", "waiting", "live"):
        group.state = state
        group.sync.last_error = "cannot reach the relay" if state == "waiting" else None
        group.state_observers[0]()
        assert dialog.connection_label.cget("text").startswith("Connection: ")
    assert dialog.label_entry is entry  # in place: no rebuild
    assert dialog.webhook_entry.get().endswith("/2/typed") and dialog.label_entry.get() == "Alice"
    dialog.invite_button.invoke()
    assert group.calls[-1] == ("invite", "Alice", 1)


def test_the_connection_text_and_reconnect_follow_the_state(root):
    group = leader_group()
    dialog = dialog_for(root, group)
    group.state, group.sync.last_error = "waiting", "cannot reach the relay"
    group.state_observers[0]()
    assert dialog.connection_label.cget("text") == "Connection: Offline, retrying (cannot reach the relay)"
    assert dialog.reconnect_button is None
    group.state = "refused"
    group.state_observers[0]()
    assert dialog.reconnect_button is not None
    group.state = "live"
    group.state_observers[0]()
    assert dialog.reconnect_button is None


def test_a_full_render_restores_typed_input(root):
    """The safety net: a roster change rebuilds, the leader's half-typed label stays."""
    group = leader_group()
    dialog = dialog_for(root, group)
    dialog.label_entry.insert(0, "Bob")
    dialog.expiry.set("7 days")
    group._roster = roster_of(Member(BOB, "Bob", False, None))
    dialog._tick()
    assert BOB in dialog.remove_buttons  # the tick saw the roster change
    assert dialog.label_entry.get() == "Bob" and dialog.expiry.get() == "7 days"
    dialog.invite_button.invoke()
    assert group.calls[-1] == ("invite", "Bob", 168)


def test_typed_group_name_survives_a_render(root):
    group = FakeGroup()
    dialog = dialog_for(root, group)
    dialog.name_entry.insert(0, "Half typed")
    dialog.invite_box.insert("1.0", "edmrn-crew:1:xy")
    dialog.render()
    assert dialog.name_entry.get() == "Half typed"
    assert dialog.invite_box.get("1.0", "end-1c") == "edmrn-crew:1:xy"


def test_a_refused_pause_reverts_the_switch(root):
    group = FakeGroup(GroupConfig("wss://h", G, LEADER, ALICE), roster_of())
    group.pause_error = "EDMRN could not save the group file; the log file says why."
    dialog = dialog_for(root, group, author=ALICE, commander="Alice")
    dialog.pause_switch.toggle()
    assert group.calls[-1] == ("pause", True)
    assert dialog.pause_switch.get() == 0
    assert dialog.message.get() == group.pause_error


def test_an_old_invite_never_shows_under_another_group(root):
    group = leader_group()
    dialog = dialog_for(root, group)
    dialog.invite_button.invoke()
    assert dialog.code_entry is not None
    other = "01920000-0000-7000-8000-0000000000b2"
    group.config = GroupConfig("wss://h", other, LEADER, LEADER)
    dialog.render()
    assert dialog.code_entry is None and dialog.invite is None
    dialog.invite_button.invoke()
    group.config = None  # leave, then a group again
    dialog.render()
    group.config = GroupConfig("wss://h", other, LEADER, LEADER)
    dialog.render()
    assert dialog.code_entry is None


def test_leave_and_remove_need_a_second_click_within_the_window(root):
    group = leader_group()
    dialog = dialog_for(root, group)
    dialog.CONFIRM_MS = 50
    button = dialog.remove_buttons[ALICE]
    button.invoke()
    assert button.cget("text") == "Click again to remove" and group.calls == []
    time.sleep(0.15)
    dialog.update()
    assert button.cget("text") == "Remove"
    button.invoke()
    assert group.calls == []  # a click after the window asks again
    dialog.leave_button.invoke()
    assert dialog.leave_button.cget("text") == "Click again to leave" and group.calls == []
    dialog.leave_button.invoke()
    assert group.calls == [("leave",)]


def test_create_invite_renders_once(root):
    dialog = dialog_for(root, leader_group())
    renders = []
    real = dialog.render
    dialog.render = lambda: (renders.append(1), real())[1]
    dialog.invite_button.invoke()
    assert len(renders) == 1


def _texts(widget):
    found = []
    for child in widget.winfo_children():
        try:
            found.append(child.cget("text"))
        except Exception:
            pass
        found.extend(_texts(child))
    return found


def test_the_commander_and_key_are_reread_and_the_frontier_id_never_shows(root):
    crew = SimpleNamespace(commander="F1234567", commander_name="Jameson", group=leader_group(),
                           identity=lambda: SimpleNamespace(author=LEADER))
    dialog = open_dialog(SimpleNamespace(root=root, crew=crew))
    assert dialog.key_entry.get() == LEADER
    assert "Commander: Jameson" in _texts(dialog.body)
    assert not any("F1234567" in t for t in _texts(dialog.body))
    crew.commander, crew.commander_name = "F7654321", "Alt"
    crew.identity = lambda: SimpleNamespace(author=ALICE)
    dialog._tick()
    assert "Commander: Alt" in _texts(dialog.body) and dialog.key_entry.get() == ALICE
    assert not any("F7654321" in t for t in _texts(dialog.body))


def test_copy_buttons_copy_their_own_value(root):
    copied = []
    dialog = CrewDialog(root, leader_group(), commander="Leader", author=LEADER, copy=copied.append)
    dialog.copy_key_button.invoke()
    dialog.invite_button.invoke()
    dialog.copy_link_button.invoke()
    dialog.copy_code_button.invoke()
    assert copied == [LEADER, "https://h/join#edmrn-crew:1:abc", "edmrn-crew:1:abc"]


def test_every_expiry_is_sent(root):
    group = leader_group()
    dialog = dialog_for(root, group)
    sent = []
    for label in ("1 hour", "24 hours", "7 days"):
        dialog.expiry.set(label)
        dialog.invite_button.invoke()
        sent.append(group.calls[-1][2])
    assert sent == [1, 24, 168]


def test_the_group_name_is_plain_text(root):
    group = leader_group()
    group._roster = Roster(G, LEADER, "Crew\n  <b>X</b>" + "y" * 100, roster_of().members, {})
    dialog = dialog_for(root, group)
    title = dialog.group_section.title_label.cget("text")
    assert title.startswith("Group: Crew <b>X</b>y") and "\n" not in title and len(title) < 100


def test_a_failing_card_shows_unavailable_and_does_not_raise(root):
    parent = ctk.CTkFrame(root)
    card = add_settings_card(SimpleNamespace(root=root, crew=None), parent, {})  # no colours: KeyError
    assert card is not None
    assert [c.cget("text") for c in card.winfo_children() if isinstance(c, ctk.CTkLabel)] == [
        "Crew is unavailable."]


# --- fix round 2 ---------------------------------------------------------------------------

def test_a_render_keeps_the_placeholders_active(root):
    dialog = dialog_for(root, FakeGroup())
    dialog.render()
    assert dialog.name_entry._placeholder_text_active
    leader = dialog_for(root, leader_group())
    leader.render()
    assert leader.label_entry._placeholder_text_active


def test_the_label_is_cleared_after_a_created_invite(root):
    group = leader_group()
    dialog = dialog_for(root, group)
    dialog.label_entry.insert(0, "Alice")
    dialog.invite_button.invoke()
    assert group.calls[-1] == ("invite", "Alice", 24) and dialog.label_entry.get() == ""
    dialog.invite_button.invoke()
    assert group.calls[-1] == ("invite", "", 24)


def test_a_render_disarms_a_pending_confirmation(root):
    group = leader_group()
    dialog = dialog_for(root, group)
    dialog.remove_buttons[ALICE].invoke()  # armed
    dialog.render()
    dialog.remove_buttons[ALICE].invoke()  # the new button asks again
    assert ("remove", ALICE) not in group.calls
    dialog.remove_buttons[ALICE].invoke()
    assert group.calls[-1] == ("remove", ALICE)


def test_the_identity_is_not_read_on_every_tick(root):
    reads = []

    def identity():
        reads.append(1)
        return SimpleNamespace(author=LEADER)
    crew = SimpleNamespace(commander="F1", commander_name="Jameson", group=leader_group(),
                           identity=identity)
    dialog = open_dialog(SimpleNamespace(root=root, crew=crew))
    first = len(reads)
    for _ in range(5):
        dialog._tick()
    assert len(reads) == first
    crew.commander, crew.commander_name = "F2", "Alt"
    dialog._tick()
    assert len(reads) == first + 1
