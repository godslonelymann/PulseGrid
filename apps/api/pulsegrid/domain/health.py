from datetime import timedelta


def transition(state, failures, successes, success, gap=False, incident_open=False):
    # An open incident survives pauses/gaps; recovery still requires two fresh successes.
    if gap:
        failures, successes = 0, 0
        state = "DOWN" if incident_open else "UNKNOWN"
    if success:
        failures, successes = 0, successes + 1
        if state == "DOWN" and successes < 2:
            return "DOWN", failures, successes, None
        return "UP", failures, successes, "recovery" if incident_open else None
    failures, successes = failures + 1, 0
    if state == "DOWN" or failures >= 2:
        return "DOWN", failures, successes, None if incident_open else "outage"
    return "SUSPECT", failures, successes, None


def display(runtime, monitor, at):
    if monitor.paused or monitor.archived_at is not None:
        return "PAUSED"
    if not runtime.last_observed or at - runtime.last_observed > timedelta(
        seconds=monitor.interval * 2 + monitor.timeout
    ):
        return "UNKNOWN"
    return runtime.state
