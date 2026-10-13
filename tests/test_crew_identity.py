import json

import pytest

from edmrn.ext.crew_identity import KeyFileError, KeyRing, b64url, verify


@pytest.fixture
def ring(tmp_path):
    return KeyRing(tmp_path / "crew_keys")


def test_new_commander_gets_a_key_file_and_a_stable_author(ring, tmp_path):
    ident = ring.for_commander("F123")
    assert len(ident.author) == 43 and ident.high_water == 0
    assert ident.path.parent == tmp_path / "crew_keys"
    assert ident.path.name.startswith("cmdr-") and "F123" not in ident.path.name
    again = KeyRing(tmp_path / "crew_keys").for_commander("F123")
    assert again.author == ident.author


def test_two_commanders_get_two_identities(ring):
    assert ring.for_commander("F1").author != ring.for_commander("F2").author
    assert ring.path_for("F1") != ring.path_for("F2")


def test_sign_and_verify(ring):
    ident = ring.for_commander("F1")
    sig = ident.sign(b"hello")
    assert len(sig) == 64
    assert verify(ident.author, sig, b"hello")
    assert not verify(ident.author, sig, b"hellp")
    tampered = bytes([sig[0] ^ 1]) + sig[1:]
    assert not verify(ident.author, tampered, b"hello")
    assert not verify(ring.for_commander("F2").author, sig, b"hello")


@pytest.mark.parametrize("author,sig", [
    ("A" * 43, b"s" * 64),
    ("short", b"s" * 64),
    ("!" * 43, b"s" * 64),
    (None, b"s" * 64),
])
def test_verify_never_raises(author, sig):
    assert verify(author, sig, b"x") is False


def test_verify_rejects_wrong_signature_length_and_non_canonical_author(ring):
    ident = ring.for_commander("F1")
    sig = ident.sign(b"x")
    assert not verify(ident.author, sig[:63], b"x")
    assert not verify(ident.author, sig + b"\x00", b"x")
    # The last base64url character carries 2 unused bits; flipping one of them decodes to the
    # same key bytes. A second spelling of one author must not verify.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    last = alphabet.index(ident.author[-1])
    twin = ident.author[:-1] + alphabet[last ^ 1]
    assert not verify(twin, sig, b"x")


def test_high_water_survives_reload(ring, tmp_path):
    ident = ring.for_commander("F1")
    ident.raise_high_water(41)
    ident.raise_high_water(7)
    assert ident.high_water == 41
    assert KeyRing(tmp_path / "crew_keys").for_commander("F1").high_water == 41
    assert not ident.path.with_name(ident.path.name + ".tmp").exists()


@pytest.mark.parametrize("content", [
    "not json",
    "[]",
    '{"v": 1}',
    '{"v": 2, "author": "x", "private_key": "x", "high_water": 0}',
])
def test_unusable_key_file_raises_and_is_kept(ring, content):
    path = ring.path_for("F1")
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(KeyFileError):
        ring.for_commander("F1")
    assert path.read_text(encoding="utf-8") == content


def test_key_file_whose_author_does_not_match_raises(ring, tmp_path):
    record = json.loads(ring.export_text("F1"))
    record["author"] = ring.for_commander("F2").author
    other = KeyRing(tmp_path / "other")
    with pytest.raises(KeyFileError, match="author"):
        other.import_text("F9", json.dumps(record))


def test_bad_high_water_raises(ring):
    record = json.loads(ring.export_text("F1"))
    for bad in (-1, True, 2**53, "3"):
        with pytest.raises(KeyFileError):
            KeyRing(ring.directory / "x").import_text("F1", json.dumps(dict(record, high_water=bad)))


def test_export_import_moves_the_identity(ring, tmp_path):
    ident = ring.for_commander("F1")
    ident.raise_high_water(5)
    text = ring.export_text("F1")
    other = KeyRing(tmp_path / "elsewhere")
    imported = other.import_text("F1", text)
    assert imported.author == ident.author and imported.high_water == 5
    assert verify(imported.author, imported.sign(b"m"), b"m")


def test_import_refuses_to_replace_an_existing_key_unless_asked(ring):
    ring.for_commander("F1")
    text = KeyRing(ring.directory / "x").export_text("F2")
    with pytest.raises(KeyFileError):
        ring.import_text("F1", text)
    assert ring.import_text("F1", text, replace=True).author == json.loads(text)["author"]
    assert KeyRing(ring.directory).for_commander("F1").author == json.loads(text)["author"]


def test_b64url_has_no_padding():
    assert b64url(b"\x00" * 32).endswith("A") and "=" not in b64url(b"\x00" * 32)


# ---- fix round 1 ----

def test_stale_identity_does_not_revert_a_replaced_key(ring):
    held = ring.for_commander("F1")
    other_text = KeyRing(ring.directory / "x").export_text("F2")
    ring.import_text("F1", other_text, replace=True)
    with pytest.raises(KeyFileError):
        held.raise_high_water(3)
    assert json.loads(held.path.read_text(encoding="utf-8"))["author"] == json.loads(other_text)["author"]


def test_stale_identity_from_a_second_keyring_does_not_revert(ring):
    held = ring.for_commander("F1")
    other_text = KeyRing(ring.directory / "x").export_text("F2")
    KeyRing(ring.directory).import_text("F1", other_text, replace=True)
    with pytest.raises(KeyFileError):
        held.raise_high_water(3)
    assert json.loads(held.path.read_text(encoding="utf-8"))["author"] == json.loads(other_text)["author"]


def test_raise_high_water_never_overwrites_an_unusable_file(ring):
    held = ring.for_commander("F1")
    held.path.write_text("garbage", encoding="utf-8")
    with pytest.raises(KeyFileError):
        held.raise_high_water(3)
    assert held.path.read_text(encoding="utf-8") == "garbage"


@pytest.mark.parametrize("bad", [2**53, 1.5, True, -1, "3"])
def test_raise_high_water_validates_seq(ring, bad):
    ident = ring.for_commander("F1")
    with pytest.raises(ValueError):
        ident.raise_high_water(bad)
    assert ident.high_water == 0


def test_high_water_unchanged_when_write_fails_and_retry_writes(ring, monkeypatch, tmp_path):
    import os
    ident = ring.for_commander("F1")
    real = os.replace

    def boom(*a, **k):
        raise PermissionError("locked")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(PermissionError):
        ident.raise_high_water(9)
    assert ident.high_water == 0
    monkeypatch.setattr(os, "replace", real)
    ident.raise_high_water(9)
    assert KeyRing(tmp_path / "crew_keys").for_commander("F1").high_water == 9


def test_verify_rejects_non_bytes_data(ring):
    ident = ring.for_commander("F1")
    sig = ident.sign(b"x")
    assert verify(ident.author, sig, 5) is False
    assert verify(ident.author, sig, "x") is False
    assert verify(ident.author, sig, bytearray(b"x")) is True


# Pure-Python Ed25519 (RFC 8032 5.1.3 / 5.1.4) to prove the blocklist points have small order.
_P = 2**255 - 19
_D = -121665 * pow(121666, -1, _P) % _P

_SMALL_ORDER = {
    "order4_zero": bytes(32),
    "identity": b"\x01" + bytes(31),
    "order8_a": bytes.fromhex("26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05"),
    "order8_b": bytes.fromhex("c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a"),
    "p_minus_1": b"\xec" + b"\xff" * 30 + b"\x7f",
    "p_noncanon0": b"\xed" + b"\xff" * 30 + b"\x7f",
    "p_plus_1_noncanon1": b"\xee" + b"\xff" * 30 + b"\x7f",
}


def _decode(raw):
    y = int.from_bytes(raw, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    if y >= _P:
        return None  # non-canonical y: RFC 8032 decoding fails
    x2 = (y * y - 1) * pow(_D * y * y + 1, -1, _P) % _P
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * pow(2, (_P - 1) // 4, _P) % _P
    if (x * x - x2) % _P:
        return None
    if x == 0 and sign:
        return None
    if x & 1 != sign:
        x = _P - x
    return (x, y, 1, x * y % _P)


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


@pytest.mark.parametrize("name", sorted(_SMALL_ORDER))
def test_blocklist_points_really_have_small_order(name):
    pt = _decode(_SMALL_ORDER[name])
    if pt is None:
        pytest.skip("non-canonical y; does not decode under RFC 8032")
    for _ in range(3):
        pt = _add(pt, pt)
    assert pt[0] % _P == 0 and (pt[1] - pt[2]) % _P == 0


@pytest.mark.parametrize("name", sorted(_SMALL_ORDER))
def test_verify_rejects_small_order_authors(name):
    raw = _SMALL_ORDER[name]
    for variant in (raw, raw[:31] + bytes([raw[31] | 0x80])):
        assert verify(b64url(variant), variant + bytes(32), b"any message") is False


def test_identity_point_forgery_is_rejected():
    ident = b"\x01" + bytes(31)
    assert verify(b64url(ident), ident + bytes(32), b"anything") is False


def test_normal_key_still_verifies(ring):
    ident = ring.for_commander("F1")
    assert verify(ident.author, ident.sign(b"m"), b"m") is True
