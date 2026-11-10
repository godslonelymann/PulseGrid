from pulsegrid.worker.runner import alert_email


def test_outage_email_names_service_in_subject_and_is_plain_text():
    subject, body = alert_email(
        {
            "event_kind": "outage",
            "monitor": "Checkout API",
            "url": "https://shop.example.com/health",
            "detected_at": "2026-11-10T07:00:00+00:00",
            "reason": "HTTP 503",
            "link": "https://pulse.example.com/?monitor=1",
        }
    )
    assert subject == "[PulseGrid] Checkout API is DOWN"
    assert "HTTP 503" in body and "https://pulse.example.com/?monitor=1" in body
    assert "{" not in body


def test_recovery_and_test_emails():
    assert alert_email({"event_kind": "recovery", "monitor": "Checkout API"})[0] == (
        "[PulseGrid] Checkout API has recovered"
    )
    # Test deliveries carry no event_kind in their payload.
    subject, body = alert_email({"message": "PulseGrid test notification", "time": "2026-11-10T07:00:00+00:00"})
    assert subject == "[PulseGrid] Test alert" and "2026-11-10" in body
