import logging

from app.core.redact import RedactingFilter
from app.main import create_app
from app.models.enums import AiTaskName
from app.services.rules.chooser import AiChooser
from app.services.rules.worker import enqueue_new


def test_app_has_the_rules_parts(app):
    s = app.state
    assert s.mail.backup.on_kept is enqueue_new
    assert s.sorting_chooser() is None
    conn = s.ai.create("Ollama", "http://ollama.example.com:11434/v1")
    s.ai.set_task(AiTaskName.SORTING, conn.id, "llama3.1:8b")
    assert isinstance(s.sorting_chooser(), AiChooser)
    for name in ("ai_client", "rule_applier", "rule_worker", "rule_decisions", "rule_jobs"):
        assert getattr(s, name) is not None
    assert s.rule_worker.applier is s.rule_applier
    assert s.rule_decisions.applier is s.rule_applier
    assert s.rule_jobs.applier is s.rule_applier


def test_saved_keys_go_to_the_log_redactor(settings, layout):
    first = create_app(settings, start_background=False)
    first.state.ai.create("GW", "https://gw.example.com/v1", api_key="sk-wired-secret-1")
    create_app(settings, start_background=False)  # a restart loads it again
    handler = next(
        h
        for h in logging.getLogger().handlers
        if any(isinstance(f, RedactingFilter) for f in h.filters)
    )
    redactor = next(f for f in handler.filters if isinstance(f, RedactingFilter))
    assert "sk-wired-secret-1" in redactor._secrets
