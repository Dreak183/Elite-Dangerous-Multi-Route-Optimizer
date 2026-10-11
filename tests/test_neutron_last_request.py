from types import SimpleNamespace

import edmrn.neutron as neutron
from edmrn.ext.spansh import RouteSubmission


def _fake_submit(status, from_used="Sol"):
    body = {"job": "J1"} if status == 202 else {"error": "Could not find starting system"}
    response = SimpleNamespace(status_code=status, json=lambda: body, text="", reason="")
    return lambda *a, **k: RouteSubmission(response, None, from_used)


def test_last_request_set_on_success(monkeypatch):
    monkeypatch.setattr(neutron, "submit_route_job", _fake_submit(202, "Nearest"))
    router = neutron.NeutronRouter()
    monkeypatch.setattr(router, "_wait_for_result", lambda job, cb=None: {
        "success": True, "data": {"result": {"system_jumps": [
            {"system": "Nearest", "distance_jumped": 0, "jumps": 0, "neutron_star": False},
            {"system": "Colonia", "distance_jumped": 100, "jumps": 2, "neutron_star": True}]}}})

    result = router.calculate_route("Unknown", "Colonia", 70.0, "x6")

    assert result["success"]
    assert router.last_request == {"from": "Nearest", "to": "Colonia", "range": 70.0, "boost": "x6"}


def test_last_request_untouched_on_failure(monkeypatch):
    monkeypatch.setattr(neutron, "submit_route_job", _fake_submit(400))
    router = neutron.NeutronRouter()
    router.last_request = {"keep": True}

    assert not router.calculate_route("Sool", "Colonia", 70.0)["success"]
    assert router.last_request == {"keep": True}
