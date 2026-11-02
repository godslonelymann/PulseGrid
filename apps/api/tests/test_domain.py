from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as Obj

import pytest
from pulsegrid.domain.health import display, transition


@pytest.mark.parametrize(
    "samples,states,events",
    [
        ([True, False, True], ["UP", "SUSPECT", "UP"], [None, None, None]),
        (
            [False, False, True, False, True, True],
            ["SUSPECT", "DOWN", "DOWN", "DOWN", "DOWN", "UP"],
            [None, "outage", None, None, None, "recovery"],
        ),
        ([False, False, False], ["SUSPECT", "DOWN", "DOWN"], [None, "outage", None]),
    ],
)
def test_sequences(samples, states, events):
    state = "UNKNOWN"
    fail = success = 0
    opened = False
    for ok, want, event in zip(samples, states, events):
        state, fail, success, got = transition(state, fail, success, ok, incident_open=opened)
        assert (state, got) == (want, event)
        if got == "outage":
            opened = True
        if got == "recovery":
            opened = False


def test_long_gap_resets_consecutive_samples():
    assert transition("SUSPECT", 1, 0, False, gap=True) == ("SUSPECT", 1, 0, None)
    assert transition("DOWN", 2, 1, True, gap=True, incident_open=True) == ("DOWN", 0, 1, None)


def test_unknown_and_pause_preserve_observed_state():
    at = datetime.now(UTC)
    r = Obj(state="DOWN", last_observed=at - timedelta(seconds=71))
    m = Obj(paused=False, archived_at=None, interval=30, timeout=10)
    assert display(r, m, at) == "UNKNOWN"
    m.paused = True
    assert display(r, m, at) == "PAUSED"
    assert r.state == "DOWN"
