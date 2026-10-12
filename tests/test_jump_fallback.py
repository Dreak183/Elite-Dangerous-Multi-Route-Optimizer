import sys
from types import SimpleNamespace

import pytest

_hook = sys.excepthook
import edmrn.app as app_module  # noqa: E402  (sets sys.excepthook at import)
sys.excepthook = _hook

from edmrn.ext.location import LocationState, LocationTracker  # noqa: E402


class Reached(Exception):
    pass


class Tabview:
    def get(self):
        raise Reached


class Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def handler_self(**extra):
    return SimpleNamespace(tabview=Tabview(), _last_handled_system=None,
                           cmdr_location=Var("Sol · Abraham Lincoln"), **extra)


def test_jump_handler_asks_the_tracker_before_the_header_text():
    tracker = LocationTracker()
    tracker.adopt(LocationState(system="Sol", station="Abraham Lincoln"))
    me = handler_self(location_tracker=tracker)

    with pytest.raises(Reached):
        app_module.EDMRN_App._handle_system_jump(me, None)

    assert me.last_known_system == "Sol"


def test_jump_handler_falls_back_to_the_header_without_a_tracker():
    me = handler_self()
    me.cmdr_location = Var("Lave")

    with pytest.raises(Reached):
        app_module.EDMRN_App._handle_system_jump(me, None)

    assert me.last_known_system == "Lave"
