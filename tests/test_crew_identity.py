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
