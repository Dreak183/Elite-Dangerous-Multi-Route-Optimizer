"""edmrn.ext.api lookup_system and nearest: Spansh first, EDSM fallback, A3 distinction. No network."""
import pytest

from edmrn.ext.api import ApiError, ApiFailure, Lookup, lookup_system, nearest_known_system, nearest_system
from fakes import FakeResponse, Script

BLAE = "Blae Phoe XT-A e1459"
BLAE_COORDS = (768.375, -565.90625, 14325.34375)


def spansh_hits(*records):
    return FakeResponse(200, {"count": len(records), "results": list(records)})


def test_lookup_spansh_hit_sends_json_search():
    fake = Script(spansh_hits({"name": "Sol", "x": 0, "y": 0, "z": 0}))
    assert lookup_system("Sol", call=fake) == Lookup("Sol", (0.0, 0.0, 0.0), "Spansh")
    args, kwargs = fake.calls[0]
    assert args == ("POST", "https://spansh.co.uk/api/systems/search")
    assert kwargs["service"] == "Spansh" and kwargs["json"]["filters"] == {"name": {"value": "Sol"}}
    assert kwargs.get("params") is None
    assert "sort" not in kwargs["json"]


def test_lookup_name_match_is_exact_and_case_insensitive():
    fake = Script(spansh_hits({"name": "Solati", "x": 9, "y": 9, "z": 9},
                              {"name": "SOL", "x": 0, "y": 0, "z": 0}))
    assert lookup_system(" sol ", call=fake).coords == (0.0, 0.0, 0.0)


def test_lookup_spansh_miss_then_edsm_hit():
    fake = Script(spansh_hits({"name": "Blae Phoe XT-A e1458", "x": 1, "y": 1, "z": 1}),
                  FakeResponse(200, {"name": BLAE, "coords": dict(zip("xyz", BLAE_COORDS))}))
    result = lookup_system(BLAE, call=fake)
    assert result == Lookup(BLAE, BLAE_COORDS, "EDSM") and result.message is None
    args, kwargs = fake.calls[1]
    assert args == ("GET", "https://www.edsm.net/api-v1/system")
    assert kwargs["params"] == {"systemName": BLAE, "showCoordinates": 1}


def test_lookup_both_miss_is_not_found():
    result = lookup_system("Sool", call=Script(spansh_hits(), FakeResponse(200, [])))
    assert result == Lookup.not_found("Sool") and not result.found
    assert result.message == "System 'Sool' was not found at Spansh or EDSM. Check the spelling."


def test_lookup_spansh_502_and_edsm_miss_is_failed_not_not_found():
    fake = Script(ApiError(ApiFailure("Spansh", "overloaded", 502)), FakeResponse(200, []))
    result = lookup_system("Sool", call=fake)
    assert result.failures == (ApiFailure("Spansh", "overloaded", 502),) and not result.found
    assert result.message == ("Could not look up 'Sool': Spansh is overloaded (HTTP 502). Try again in "
                              "a minute; System 'Sool' is not known to EDSM.")


def test_lookup_both_fail_carries_both_reasons():
    fake = Script(ApiError(ApiFailure("Spansh", "timeout", None, "10 s")),
                  ApiError(ApiFailure("EDSM", "offline")))
    assert lookup_system("Sol", call=fake).message == \
        "Could not look up 'Sol': Spansh did not answer within 10 s; No connection to EDSM."


def test_lookup_spansh_html_200_counts_as_failure():
    fake = Script(FakeResponse(200, text="<html>maintenance</html>"), FakeResponse(200, []))
    result = lookup_system("Sol", call=fake)
    assert [f.service for f in result.failures] == ["Spansh"] and "<html>" not in result.message


def test_lookup_ignores_records_without_numeric_coords():
    fake = Script(spansh_hits({"name": "Sol", "x": None, "y": 0, "z": 0}),
                  FakeResponse(200, {"name": "Sol", "coords": {"x": "0", "y": 0, "z": 0}}))
    assert lookup_system("Sol", call=fake) == Lookup.not_found("Sol")


def test_lookup_empty_name_does_not_call():
    assert lookup_system("  ", call=Script()) == Lookup.not_found("")


NEAREST_BODY = {"system": {"name": "Sol", "id64": 10477373803, "x": 0, "y": 0, "z": 0, "distance": 0.0}}


def test_nearest_system_reads_distance_inside_system():
    fake = Script(FakeResponse(200, {"system": {**NEAREST_BODY["system"], "distance": 4.38}}))
    assert nearest_system(1, 2, 3, call=fake) == {"name": "Sol", "x": 0.0, "y": 0.0, "z": 0.0, "distance": 4.38}
    args, kwargs = fake.calls[0]
    assert args == ("GET", "https://spansh.co.uk/api/nearest")
    assert kwargs["params"] == {"x": 1, "y": 2, "z": 3}


def test_nearest_known_system_pair():
    assert nearest_known_system(0, 0, 0, call=Script(FakeResponse(200, NEAREST_BODY))) == ("Sol", 0.0)


@pytest.mark.parametrize("body", [
    {}, {"system": None},
    {"system": {"name": "", "x": 0, "y": 0, "z": 0, "distance": 1}},
    {"system": {"name": "X", "x": 0, "y": 0, "z": 0}},
])
def test_nearest_without_usable_system_is_none(body):
    assert nearest_known_system(0, 0, 0, call=Script(FakeResponse(200, body))) is None


def test_nearest_rejection_raises_readable_error():
    with pytest.raises(ApiError) as info:
        nearest_system(0, 0, 0, call=Script(FakeResponse(400, {"error": "x must be a number"})))
    assert str(info.value) == "Spansh rejected the request: x must be a number"
