"""Unit contracts for the AGENTIC STAR Marketplace entry adapter."""

from __future__ import annotations

from typing import Any

import cli
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.base import NullProvider
from src.agent import InvestmentSuitabilityQAAgent
from src.nodes.query_classification import QueryClassificationNode


def test_runner_lifecycle_returns_non_empty_marketplace_output():
    agent = cli.MarketplaceGraph(config={})
    agent.compile()
    agent.provision_secrets(NullProvider())

    result = agent.invoke(
        "国内MMF",
        ctx=InvocationContext(
            caller_id="u1",
            caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
        ),
        input_context={"conversation_history": ["検査されていない過去の会話"]},
    )

    assert result["status"] == "success"
    assert result["output"]


def test_invoke_does_not_put_conversation_history_in_state(monkeypatch):
    seen_input_contexts: list[dict[str, Any]] = []
    original_execute = QueryClassificationNode.execute

    def recording_execute(self, state):
        seen_input_contexts.append(dict(state["input_context"]))
        return original_execute(self, state)

    monkeypatch.setattr(QueryClassificationNode, "execute", recording_execute)
    agent = cli.MarketplaceGraph(config={})
    agent.compile()

    result = agent.invoke(
        "国内MMF",
        input_context={"conversation_history": ["全会話の自由文"]},
    )

    assert result["status"] == "success"
    assert seen_input_contexts == [{}]


def test_enrich_output_adds_formatted_output_for_success():
    agent = cli.MarketplaceGraph(config={})
    output = {"status": "success"}

    agent._enrich_output(output, {"formatted_output": "適合性判定と免責文"})

    assert output["output"] == "適合性判定と免責文"


def test_enrich_output_omits_marketplace_output_for_failure():
    agent = cli.MarketplaceGraph(config={})
    output = {"status": "error", "error_code": "PRODUCT_NOT_FOUND"}

    agent._enrich_output(output, {"formatted_output": None})

    assert "output" not in output


def test_invoke_without_context_forces_verified_external_and_disables_hitl(monkeypatch):
    captured: dict[str, Any] = {}

    def recording_invoke(self, user_input, session_id="", ctx=None, input_context=None):
        del self
        captured.update(
            user_input=user_input,
            session_id=session_id,
            ctx=ctx,
            input_context=input_context,
        )
        return {"status": "success"}

    monkeypatch.setattr(InvestmentSuitabilityQAAgent, "invoke", recording_invoke)
    result = cli.MarketplaceGraph(config={}).invoke(
        "素の日本語の質問文",
        session_id="marketplace-session",
        input_context={"conversation_history": ["untrusted"]},
    )

    assert result == {"status": "success"}
    assert captured["ctx"].caller_trust_level is TrustLevel.VERIFIED_EXTERNAL
    assert captured["ctx"].hitl_allowed is False
    assert captured["ctx"].session_id == "marketplace-session"
    assert captured["session_id"] == "marketplace-session"
    assert captured["input_context"] == {}


def test_main_passes_marketplace_graph_and_manifest_identity(monkeypatch):
    captured: dict[str, Any] = {}

    def recording_runner(agent_cls, **kwargs):
        captured.update(agent_cls=agent_cls, **kwargs)

    monkeypatch.setattr(cli, "run_agent_marketplace", recording_runner)
    cli.main()

    assert captured["agent_cls"] is cli.MarketplaceGraph
    assert captured["agent_name"] == "FIN-C2-025"
    assert captured["namespace"] == "fin"
    assert captured["config"]["escalate_risk_threshold"] == 4
