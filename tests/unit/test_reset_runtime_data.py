from scripts.reset_runtime_data import PRESERVED_TABLES


def test_reset_preserves_long_term_user_configuration() -> None:
    assert {
        "candidate_profiles",
        "job_strategies",
        "knowledge_items",
        "resumes",
        "automation_settings",
        "llm_runtime_settings",
        "scheduling_preferences",
    } <= PRESERVED_TABLES


def test_reset_does_not_preserve_runtime_state() -> None:
    assert {
        "jobs",
        "messages",
        "action_queue",
        "audit_events",
        "worker_instances",
        "llm_circuit_breakers",
    }.isdisjoint(PRESERVED_TABLES)
