import importlib
import sys
from types import SimpleNamespace

import pytest

ctk = pytest.importorskip("customtkinter")

from edmrn.ext.crew_dialog import CrewDialog, add_settings_card, open_dialog
from edmrn.ext.crew_group import CreatedInvite, GroupError
from edmrn.ext.crew_roster import GroupConfig, Member, Roster
from edmrn.ext.crew_sync import BoardEntry

LEADER, ALICE, BOB = "L" * 43, "A" * 43, "B" * 43
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

    @property
    def roster(self):
        return self._roster

    def subscribe_state(self, observer):
        self.state_observers.append(observer)

    def subscribe_board(self, observer):
        self.board_observers.append(observer)

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


def test_observers_rerender_from_the_current_state_once_per_group(root):
    group = leader_group()
    first = dialog_for(root, group)
    first.destroy()
    second = dialog_for(root, group)
    assert len(group.state_observers) == 1 and len(group.board_observers) == 1
    group.state = "refused"  # the notification carries nothing: the state is re-read
    group.state_observers[0]()
    assert second.reconnect_button is not None
    group.board_observers[0](BoardEntry(ALICE, "2026-10-13T10:00:00Z", SOL))
    assert second.status_labels[ALICE].cget("text") == "Sol - Wanderer"


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


def test_the_dialog_imports_and_the_card_builds_without_cryptography(root, monkeypatch):
    """S4: Spec A's optional cryptography; mirrors tests/test_crew_install.py."""
    import edmrn.ext as ext_package
    for name in [n for n in sys.modules if n.startswith(("edmrn.ext.crew", "cryptography."))]:
        monkeypatch.delitem(sys.modules, name)
        attribute = name.rpartition(".")[2]
        if name.startswith("edmrn.ext.") and hasattr(ext_package, attribute):
            monkeypatch.setattr(ext_package, attribute, getattr(ext_package, attribute))  # restored later
    monkeypatch.setitem(sys.modules, "cryptography", None)
    fresh = importlib.import_module("edmrn.ext.crew_dialog")
    assert "edmrn.ext.crew_identity" not in sys.modules
    card = fresh.add_settings_card(SimpleNamespace(root=root, crew=None), ctk.CTkFrame(root),
                                   {"frame": "gray20", "border": "gray40"})
    assert card.winfo_exists()
    dialog = fresh.open_dialog(SimpleNamespace(root=root, crew=None))
    assert dialog is not None
    dialog.destroy()
