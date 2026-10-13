"""The crew settings card and dialog (crew sync core spec S20). Tk thread only.

The dialog renders from the group controller (crew_group.CrewGroup). It re-renders in full
only when its structure changes (group, problem, roster, commander); a sync state change updates the
connection label and the Reconnect button in place, and typed input survives a full render. The state
notification arrives through root.after and re-reads `group.state`; roster changes have no
notification, so a one-second check looks for them. Friend statuses update their label in place. Every action goes through the controller; a GroupError's
sentence appears under the dialog.

Everything a friend sends (names, systems, ships) is shown as plain widget text only.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import customtkinter as ctk

from edmrn.ext.crew_roster import DEFAULT_RELAY, INVITE_HOURS, CreatedInvite, GroupError
from edmrn.logger import get_logger

logger = get_logger('CrewDialog')

EXPIRY = dict(zip(('1 hour', '24 hours', '7 days'), INVITE_HOURS))
STATE_TEXT = {'connecting': 'Connecting...', 'syncing': 'Catching up...', 'live': 'Connected',
              'waiting': 'Offline, retrying', 'refused': 'Refused by the relay',
              'stopped': 'Not connected', 'no group': 'Not in a group'}
WEBHOOK_WARNING = ('A webhook posts into a channel, not a DM: whoever reads the channel first can '
                   'use the invite. Use a private channel and a short expiry.')
STATUS_MAX = 80
_UNSET = object()
_card_failure_logged = False


def add_settings_card(app: Any, parent: Any, colors: dict) -> Any:
    """The Settings card. Called from upstream's tab builder: it never raises into it, because a
    raise there leaves the Settings tab half built and unmarked, and it is built again on each visit."""
    global _card_failure_logged
    frame = None
    try:
        frame = ctk.CTkFrame(parent, corner_radius=10, fg_color=colors['frame'],
                             border_color=colors['border'], border_width=1)
        frame.grid(row=1, column=2, padx=5, pady=5, sticky='nsew')
        ctk.CTkLabel(frame, text='Crew', font=ctk.CTkFont(size=13, weight='bold')).pack(pady=(8, 6))
        ctk.CTkButton(frame, text='Crew...', command=lambda: open_dialog(app)).pack(
            pady=(0, 8), padx=10, fill='x')
        return frame
    except Exception as e:
        if not _card_failure_logged:
            _card_failure_logged = True
            logger.error(f'The crew settings card could not be built: {e}')
    try:
        if frame is not None:
            frame.destroy()
        frame = ctk.CTkFrame(parent, corner_radius=10)
        frame.grid(row=1, column=2, padx=5, pady=5, sticky='nsew')
        ctk.CTkLabel(frame, text='Crew is unavailable.').pack(padx=10, pady=10)
        return frame
    except Exception:
        return None


def open_dialog(app: Any) -> Optional['CrewDialog']:
    existing = getattr(app, '_edmrn_crew_dialog', None)
    if existing is not None:
        try:
            alive = bool(existing.winfo_exists())
        except Exception:
            alive = False
        if alive:
            existing.lift()
            existing.focus()
            return existing

    def who() -> tuple:  # read on each render: the commander can change while the dialog is open
        crew = getattr(app, 'crew', None)
        identity = crew.identity() if crew is not None else None
        return (getattr(crew, 'commander_name', None),
                identity.author if identity is not None else None)

    crew = getattr(app, 'crew', None)
    dialog = CrewDialog(app.root, getattr(crew, 'group', None), commander=None, author=None, who=who)
    app._edmrn_crew_dialog = dialog
    return dialog


def _plain(value: Any) -> str:
    """A friend's text for a label: a bounded plain string, nothing else."""
    if not isinstance(value, str):
        return ''
    value = ' '.join(value.split())
    return value if len(value) <= STATUS_MAX else value[:STATUS_MAX - 1] + '...'


def _status_text(status: Any) -> str:
    if not isinstance(status, dict):
        return ''
    ship = _plain(status.get('ship_name')) or _plain(status.get('ship'))
    return ' - '.join(part for part in (_plain(status.get('system')), ship) if part)


class CrewDialog(ctk.CTkToplevel):
    REFRESH_MS = 1000
    CONFIRM_MS = 4000

    def __init__(self, master: Any, group: Any, *, commander: Optional[str], author: Optional[str],
                 copy: Optional[Callable[[str], None]] = None,
                 who: Optional[Callable[[], tuple]] = None) -> None:
        super().__init__(master)
        self.title('Crew')
        self.geometry('560x640')
        self.group = group
        self._who = who or (lambda: (commander, author))
        self._copy = copy or self._clipboard
        self.message = ctk.StringVar(value='')
        self.invite: Optional[CreatedInvite] = None
        self._invite_group: Optional[str] = None
        self._armed: dict = {}
        self._unsubscribers: list = []
        self._rendered_group: Any = _UNSET
        self.body = ctk.CTkScrollableFrame(self)
        self.body.pack(fill='both', expand=True, padx=10, pady=(10, 4))
        ctk.CTkLabel(self, textvariable=self.message, wraplength=520, justify='left').pack(
            fill='x', padx=10, pady=(0, 10))
        self._shown: Any = None
        self.render()
        for name, observer in (('subscribe_state', self.on_state), ('subscribe_board', self.on_board)):
            subscribe = getattr(group, name, None)
            if subscribe is not None:
                unsubscribe = subscribe(observer)
                if callable(unsubscribe):
                    self._unsubscribers.append(unsubscribe)
        self.after(self.REFRESH_MS, self._tick)

    def destroy(self) -> None:
        unsubscribers, self._unsubscribers = self._unsubscribers, []
        for unsubscribe in unsubscribers:
            unsubscribe()
        for key in list(self._armed):
            self._disarm(key)
        super().destroy()

    # --- plumbing ----------------------------------------------------------------------

    def _clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)

    def _alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except Exception:
            return False

    def _structure(self) -> tuple:
        """What decides the widget tree. State and error text are not part of it: they update in place."""
        g = self.group
        if g is None:
            return ('none',)
        roster = g.roster
        members = () if roster is None else tuple(sorted(
            (m.author, m.commander or '', m.leader, m.last_seq is not None)
            for m in roster.members.values()))
        name = roster.name if roster is not None else None
        return (g.config, g.problem, name, members, self._who())

    def _tick(self) -> None:
        if not self._alive():
            return
        self._refresh()
        self.after(self.REFRESH_MS, self._tick)

    def _refresh(self) -> None:
        if self._structure() != self._shown:
            self.render()
        else:
            self._update_connection()

    def on_state(self) -> None:
        """The sync state changed (arrives on the Tk loop; the state is re-read, not trusted)."""
        if self._alive():
            self._refresh()

    def on_board(self, entry: Any) -> None:
        """A friend's status was accepted; only a member shown here has a label."""
        if not self._alive():
            return
        label = self.status_labels.get(getattr(entry, 'author', None))
        if label is not None:
            label.configure(text=_status_text(getattr(entry, 'status', None)))

    def _do(self, action: Callable[[], Any], done: Optional[str] = None,
            on_error: Optional[Callable[[], None]] = None) -> Any:
        """Run a controller action. `done=None` leaves the message alone (the action set it)."""
        try:
            result = action()
        except GroupError as e:
            self.message.set(str(e))
            if on_error is not None:
                on_error()
            return None
        if done is not None:
            self.message.set(done)
        self.render()
        return result

    def _confirm_button(self, parent: Any, key: Any, text: str, confirm_text: str,
                        action: Callable[[], None], **options: Any) -> Any:
        """Two clicks within CONFIRM_MS run `action`; the first only asks (no modal dialog)."""
        button = ctk.CTkButton(parent, text=text, **options)

        def click() -> None:
            if key in self._armed:
                self._disarm(key)
                action()
                return
            button.configure(text=confirm_text)
            self._armed[key] = (self.after(self.CONFIRM_MS, lambda: self._disarm(key)), button, text)
        button.configure(command=click)
        return button

    def _disarm(self, key: Any) -> None:
        armed = self._armed.pop(key, None)
        if armed is None:
            return
        timer, button, text = armed
        try:
            self.after_cancel(timer)
            button.configure(text=text)
        except Exception:  # the button was destroyed by a render
            pass

    def _section(self, title: str) -> Any:
        frame = ctk.CTkFrame(self.body)
        frame.pack(fill='x', pady=6)
        frame.title_label = ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=13, weight='bold'))
        frame.title_label.pack(anchor='w', padx=8, pady=(6, 2))
        return frame

    def _copy_row(self, parent: Any, value: str, label: str) -> tuple:
        row = ctk.CTkFrame(parent, fg_color='transparent')
        row.pack(fill='x', padx=8, pady=2)
        entry = ctk.CTkEntry(row)
        entry.insert(0, value)
        entry.configure(state='readonly')
        entry.pack(side='left', fill='x', expand=True)
        button = ctk.CTkButton(row, text=label, width=70, command=lambda: self._copy(value))
        button.pack(side='left', padx=(6, 0))
        return entry, button

    # --- typed input survives a full render ----------------------------------------------

    def _typed(self) -> dict:
        typed: dict = {}
        for name in ('name_entry', 'relay_entry', 'label_entry', 'webhook_entry', 'expiry'):
            widget = getattr(self, name, None)
            if widget is not None:
                typed[name] = widget.get()
        if getattr(self, 'invite_box', None) is not None:
            typed['invite_box'] = self.invite_box.get('1.0', 'end-1c')
        return typed

    def _restore(self, typed: dict) -> None:
        for name, value in typed.items():
            widget = getattr(self, name, None)
            if widget is None:
                continue
            if name == 'expiry':
                widget.set(value)
            elif name == 'invite_box':
                widget.insert('1.0', value)
            else:
                widget.delete(0, 'end')
                widget.insert(0, value)

    # --- views -------------------------------------------------------------------------

    def render(self) -> None:
        g = self.group
        group_id = g.config.group_id if g is not None and g.config is not None else None
        typed = self._typed() if group_id == self._rendered_group else {}
        if self.invite is not None and group_id != self._invite_group:
            self.invite = None  # an old invite never shows under another group
        for key in list(self._armed):
            self._disarm(key)
        for child in self.body.winfo_children():
            child.destroy()
        self.name_entry = self.relay_entry = self.create_button = None
        self.invite_box = self.join_button = None
        self.pause_switch = self.leave_button = self.reconnect_button = None
        self.label_entry = self.expiry = self.invite_button = None
        self.code_entry = self.link_entry = self.copy_code_button = self.copy_link_button = None
        self.post_button = self.webhook_entry = self.webhook_button = None
        self.key_entry = self.copy_key_button = None
        self.group_section = self.connection_label = None
        self._conn_frame: Any = None
        self.remove_buttons: dict = {}
        self.status_labels: dict = {}
        self._shown = self._structure()
        self._rendered_group = group_id
        if g is None:
            ctk.CTkLabel(self.body, text='Crew features are unavailable; the log file says why.',
                         wraplength=500).pack(anchor='w', padx=8, pady=8)
            return
        self._render_identity()
        if g.problem:
            ctk.CTkLabel(self.body, text=g.problem, wraplength=500,
                         text_color='orange').pack(anchor='w', padx=8, pady=4)
        if g.config is None:
            self._render_outside()
        else:
            self._render_inside()
        self._restore(typed)

    def _render_identity(self) -> None:
        commander, author = self._who()
        frame = self._section('You')
        ctk.CTkLabel(frame, text=f'Commander: {_plain(commander) or "not known yet"}').pack(
            anchor='w', padx=8)
        if author:
            ctk.CTkLabel(frame, text='Your crew key (public; the relay lists leaders by it):').pack(
                anchor='w', padx=8, pady=(4, 0))
            self.key_entry, self.copy_key_button = self._copy_row(frame, author, 'Copy')

    def _leave_button(self, parent: Any) -> None:
        self.leave_button = self._confirm_button(
            parent, 'leave', 'Leave group', 'Click again to leave',
            lambda: self._do(self.group.leave, 'Left the group.'), fg_color='gray30')
        self.leave_button.pack(anchor='e', padx=8, pady=8)

    def _render_outside(self) -> None:
        if self.group.problem:  # an unusable group.json: the only way out is to leave (S5)
            self._leave_button(self.body)
            return
        frame = self._section('Create a group')
        self.name_entry = ctk.CTkEntry(frame, placeholder_text='Group name')
        self.name_entry.pack(fill='x', padx=8, pady=2)
        self.relay_entry = ctk.CTkEntry(frame)
        self.relay_entry.insert(0, DEFAULT_RELAY)
        self.relay_entry.pack(fill='x', padx=8, pady=2)
        self.create_button = ctk.CTkButton(frame, text='Create group', command=lambda: self._do(
            lambda: self.group.create_group(self.name_entry.get(), self.relay_entry.get()),
            'Group created.'))
        self.create_button.pack(anchor='e', padx=8, pady=(2, 8))

        frame = self._section('Join with invite')
        self.invite_box = ctk.CTkTextbox(frame, height=70)
        self.invite_box.pack(fill='x', padx=8, pady=2)
        self.join_button = ctk.CTkButton(frame, text='Join', command=lambda: self._do(
            lambda: self.group.join(self.invite_box.get('1.0', 'end').strip()), 'Joined.'))
        self.join_button.pack(anchor='e', padx=8, pady=(2, 8))

    def _connection_text(self) -> str:
        g = self.group
        state = STATE_TEXT.get(g.state, g.state)
        error = _plain(getattr(g.sync, 'last_error', None))
        return f'Connection: {state}' + (f' ({error})' if error and g.state != 'live' else '')

    def _update_connection(self) -> None:
        """Label and Reconnect button follow the sync state in place; nothing typed is touched."""
        if self.connection_label is None:
            return
        g = self.group
        self.connection_label.configure(text=self._connection_text())
        refused = g.state == 'refused'
        if refused and self.reconnect_button is None:
            self.reconnect_button = ctk.CTkButton(
                self._conn_frame, text='Reconnect', width=100,
                command=lambda: self._do(g.reconnect, 'Reconnecting...'))
            self.reconnect_button.pack(anchor='w', padx=8, pady=4)
        elif not refused and self.reconnect_button is not None:
            self.reconnect_button.destroy()
            self.reconnect_button = None

    def _render_inside(self) -> None:
        g = self.group
        cfg = g.config
        roster = g.roster
        name = _plain(roster.name) if roster is not None and roster.name else ''
        frame = self.group_section = self._conn_frame = self._section(f'Group: {name or "crew"}')
        self.connection_label = ctk.CTkLabel(frame, text='', wraplength=500, justify='left')
        self.connection_label.pack(anchor='w', padx=8)
        self._update_connection()
        self.pause_switch = ctk.CTkSwitch(frame, text='Pause sharing', command=self._paused)
        if cfg.paused:
            self.pause_switch.select()
        self.pause_switch.pack(anchor='w', padx=8, pady=4)

        members = self._section('Members')
        board = getattr(g, 'board', None)
        statuses = board.snapshot() if board is not None else {}
        for member in (roster.active() if roster is not None else []):
            row = ctk.CTkFrame(members, fg_color='transparent')
            row.pack(fill='x', padx=8, pady=1)
            shown = _plain(member.commander) or member.author[:12]
            ctk.CTkLabel(row, text=shown + (' (leader)' if member.leader else '')).pack(side='left')
            status = ctk.CTkLabel(row, text=_status_text(getattr(statuses.get(member.author),
                                                                  'status', None)))
            status.pack(side='left', padx=(10, 0))
            self.status_labels[member.author] = status
            if cfg.is_leader and not member.leader:
                button = self._confirm_button(
                    row, ('remove', member.author), 'Remove', 'Click again to remove',
                    lambda a=member.author: self._do(lambda: g.remove(a), 'Member removed.'),
                    width=70)
                button.pack(side='right')
                self.remove_buttons[member.author] = button

        if cfg.is_leader:
            self._render_invite(cfg)
        self._leave_button(self.body)

    def _paused(self) -> None:
        paused = bool(self.pause_switch.get())

        def revert() -> None:  # the switch must show what the group file says
            cfg = self.group.config
            if cfg is not None and cfg.paused:
                self.pause_switch.select()
            else:
                self.pause_switch.deselect()
        self._do(lambda: self.group.set_paused(paused),
                 'Sharing paused.' if paused else 'Sharing resumed.', on_error=revert)

    def _render_invite(self, cfg: Any) -> None:
        g = self.group
        frame = self._section('Invite')
        self.label_entry = ctk.CTkEntry(frame, placeholder_text='For whom (optional)')
        self.label_entry.pack(fill='x', padx=8, pady=2)
        self.expiry = ctk.CTkSegmentedButton(frame, values=list(EXPIRY))
        self.expiry.set('24 hours')
        self.expiry.pack(anchor='w', padx=8, pady=2)

        def create() -> CreatedInvite:
            invite = g.create_invite(self.label_entry.get(), EXPIRY[self.expiry.get()])
            self.invite, self._invite_group = invite, cfg.group_id
            return invite
        self.invite_button = ctk.CTkButton(
            frame, text='Create invite',
            command=lambda: self._do(create, 'Invite created; it works once.'))
        self.invite_button.pack(anchor='e', padx=8, pady=2)
        if self.invite is not None:
            self.code_entry, self.copy_code_button = self._copy_row(frame, self.invite.code, 'Copy')
            self.link_entry, self.copy_link_button = self._copy_row(frame, self.invite.link,
                                                                    'Copy link')
            ctk.CTkLabel(frame, text=f'Expires {self.invite.expires_at.replace("T", " ")}').pack(
                anchor='w', padx=8)
            if cfg.webhook:
                self.post_button = ctk.CTkButton(frame, text='Post to Discord', command=self._post)
                self.post_button.pack(anchor='e', padx=8, pady=2)
                ctk.CTkLabel(frame, text=WEBHOOK_WARNING, wraplength=500, justify='left').pack(
                    anchor='w', padx=8)

        hook = self._section('Discord webhook (leader only, stays on this PC)')
        self.webhook_entry = ctk.CTkEntry(hook, placeholder_text='https://discord.com/api/webhooks/...',
                                          show='*')  # the URL is a secret (S5a)
        if cfg.webhook:
            self.webhook_entry.insert(0, cfg.webhook)
        self.webhook_entry.pack(fill='x', padx=8, pady=2)
        self.webhook_button = ctk.CTkButton(hook, text='Save webhook', command=lambda: self._do(
            lambda: g.set_webhook(self.webhook_entry.get()), 'Webhook saved.'))
        self.webhook_button.pack(anchor='e', padx=8, pady=(2, 8))

    def _post(self) -> None:
        invite = self.invite
        if invite is None:
            return
        self.message.set('Posting to Discord...')
        # No message of ours after the call: `_posted` may already have set the result sentence.
        self._do(lambda: self.group.post_invite(invite, self._posted))

    def _posted(self, error: Optional[str]) -> None:
        if self._alive():
            self.message.set(error or 'Invite posted to Discord.')
