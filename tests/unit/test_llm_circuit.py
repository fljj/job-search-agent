from apps.api.app.services.llm_circuit_service import _probe_delay_seconds


def test_llm_probe_backoff_caps_at_sixty_minutes_forever() -> None:
    assert [_probe_delay_seconds(attempt) for attempt in range(8)] == [
        300,
        600,
        1200,
        2400,
        3600,
        3600,
        3600,
        3600,
    ]
