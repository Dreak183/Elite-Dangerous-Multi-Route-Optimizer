"""Builders for the crew tests: drafts, sealed envelopes and a dump of the projections.

Imported by test modules only, after tests/conftest.py redirected the home folders.
"""
from edmrn.ext import crew_events as ev
from edmrn.ext.crew_store import PROJECTION_TABLES

ADDR = "10477373803"
T0 = "2026-10-12T10:00:00Z"


def ts(minute, second=0):
    return f"2026-10-12T10:{minute:02d}:{second:02d}Z"


def visit(cmdr="F1", at=T0, address=ADDR, system="Sol", name="Jameson", coords=(0.0, 0.0, 0.0)):
    return ev.Draft("visit", cmdr, at, {"commander": name, "system": system, "address": address,
                                         "coords": list(coords) if coords is not None else None})


def honk(cmdr="F1", at=T0, address=ADDR, system="Sol", bodies=3, non_bodies=0):
    return ev.Draft("honk", cmdr, at, {"system": system, "address": address, "body_count": bodies,
                                        "non_body_count": non_bodies})


def all_bodies(cmdr="F1", at=T0, address=ADDR, system="Sol", count=3):
    return ev.Draft("all_bodies", cmdr, at, {"system": system, "address": address, "count": count})


def body(cmdr="F1", at=T0, address=ADDR, body_id=1, name="Sol 1", tier="full", planet_class="Rocky body",
         star_type=None, landable=True, terraform="", moon=False, discovered=True, mapped=False, system="Sol"):
    scan_type = "Basic" if tier == "basic" else "Detailed"
    if tier == "basic":
        landable, terraform = None, None
    if star_type is not None:
        planet_class, landable, terraform = None, None, None
    return ev.Draft("body", cmdr, at, {
        "system": system, "address": address, "body_id": body_id, "name": name, "tier": tier,
        "star_type": star_type, "planet_class": planet_class, "moon": moon, "landable": landable,
        "terraform": terraform, "was_discovered": discovered, "was_mapped": mapped, "scan_type": scan_type})


def signals(cmdr="F1", at=T0, address=ADDR, body_id=1, body_name="Sol 1", source="fss", counts=None,
            genera=(), system="Sol"):
    return ev.Draft("signals", cmdr, at, {
        "system": system, "address": address, "body_id": body_id, "body_name": body_name,
        "source": source, "counts": dict(counts or {}), "genera": list(genera)})


def bio_find(cmdr="F1", at=T0, address=ADDR, body_id=1, body_name="Sol 1", count=2, system="Sol"):
    return ev.Draft("find", cmdr, at, {
        "mode": "auto", "find_kind": "biological", "system": system, "address": address,
        "body_name": body_name, "body_id": body_id, "detail": {"count": count}})


def note(cmdr="F1", at=T0, note_id=None, text="hello", deleted=False, address=ADDR, system="Sol"):
    return ev.Draft("note", cmdr, at, {
        "note_id": note_id or ev.uuid7(), "system": system, "address": address, "text": text,
        "deleted": deleted}, event_id=ev.uuid7())


def manual_find(cmdr="F1", at=T0, find_id=None, note_text="wreck", deleted=False, address=ADDR,
                find_kind="other"):
    return ev.Draft("find", cmdr, at, {
        "mode": "manual", "find_id": find_id or ev.uuid7(), "find_kind": find_kind, "system": "Sol",
        "address": address, "body_name": None, "body_id": None, "note": note_text,
        "deleted": deleted}, event_id=ev.uuid7())


def sealed(identity, draft, seq, **over):
    """Envelope bytes and signature, as a friend's client would send them."""
    env = ev.build_envelope(draft.resolve_id(identity.author), identity.author, seq, draft.kind,
                            draft.created_at, draft.payload)
    env.update(over)
    return ev.seal(env, identity.sign)


def dump(store):
    with store.read() as conn:
        return {table: sorted(conn.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
                for table in PROJECTION_TABLES}
