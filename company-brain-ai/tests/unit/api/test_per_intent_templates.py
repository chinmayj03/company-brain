"""Unit tests for companybrain.api.prompts.user_message — ADR-0043 WS2."""
import pytest

from companybrain.api.prompts.user_message import build_user_message, TEMPLATES


# ── Template coverage ─────────────────────────────────────────────────────────

def test_all_intents_have_templates():
    from companybrain.api.intent_router import INTENTS
    for intent in INTENTS:
        assert intent in TEMPLATES, f"Missing template for intent '{intent}'"


def test_templates_have_context_placeholder():
    for intent, tmpl in TEMPLATES.items():
        assert "{context}" in tmpl, f"Template '{intent}' missing {{context}} placeholder"


def test_templates_have_question_placeholder():
    for intent, tmpl in TEMPLATES.items():
        assert "{question}" in tmpl, f"Template '{intent}' missing {{question}} placeholder"


# ── build_user_message ────────────────────────────────────────────────────────

def test_build_injects_question():
    msg = build_user_message(
        "who calls PaymentService?",
        intent="call_chain",
        context="## PaymentService\nSome context",
    )
    assert "who calls PaymentService?" in msg


def test_build_injects_context():
    ctx = "## PaymentService\nSome context block"
    msg = build_user_message("q", intent="call_chain", context=ctx)
    assert ctx in msg


def test_build_no_context_appends_note():
    msg = build_user_message("q", intent="concept", context=None)
    assert "brain ingest" in msg or "extraction pipeline" in msg


def test_build_empty_context_appends_note():
    msg = build_user_message("q", intent="data_flow", context="")
    assert "extraction pipeline" in msg or "brain ingest" in msg


def test_build_unknown_intent_falls_back_to_other():
    msg = build_user_message("q", intent="nonexistent_intent", context="ctx")
    # Should not raise; should produce a message
    assert "ctx" in msg
    assert "q" in msg


def test_call_chain_template_mentions_chain():
    msg = build_user_message("trace X to Y", intent="call_chain",
                             context="some context")
    assert "Call chain" in msg or "call chain" in msg


def test_data_flow_template_mentions_columns():
    msg = build_user_message("what columns?", intent="data_flow",
                             context="some context")
    assert "column" in msg.lower() or "field" in msg.lower()


def test_change_risk_template_mentions_risk():
    msg = build_user_message("what breaks?", intent="change_risk",
                             context="some context")
    assert "risk" in msg.lower() or "affected" in msg.lower()


def test_concept_template_mentions_paragraphs():
    msg = build_user_message("explain X", intent="concept",
                             context="some context")
    assert "paragraph" in msg.lower() or "explain" in msg.lower()
