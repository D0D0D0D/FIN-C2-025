"""Unit tests (TC) — FIN-C2-025 nodes (released AgentCore SDK v1.0.0).

released contract: nodes implement execute(self, state, config=None) and are called by the
backbone via __call__ with state ONLY (config is not threaded). status は AgentStatus.*.value
（文字列）。顧客プロファイル（PII）は read-only な state["input_context"]["customer_profile"]
経由で読む（config ではない）。
"""

import json

from framework.schemas.agent_status import AgentStatus

from src.nodes.answer_generation import AnswerGenerationNode, DEFAULT_DISCLAIMER
from src.nodes.query_classification import QueryClassificationNode
from src.nodes.regulatory_compliance import RegulatoryComplianceNode
from src.nodes.suitability_assessment import SuitabilityAssessmentNode
from src.nodes.suitability_retrieval import SuitabilityRetrievalNode
from src.services.knowledge_base import KnowledgeBase


def _state_with_profile(base: dict, risk_tolerance=None) -> dict:
    """input_context 経由で顧客プロファイル（PII）を載せた state を作る。"""
    profile = {"risk_tolerance": risk_tolerance} if risk_tolerance else None
    return {**base, "input_context": {"customer_profile": profile}}


# ── QueryClassificationNode (pre_process) ──
def test_tc01_query_classification_empty_input():
    out = QueryClassificationNode().execute({"user_input": "   "})
    assert out["status"] == AgentStatus.ERROR.value
    assert out["error_code"] == "EMPTY_INPUT"


def test_tc02_query_classification_success():
    out = QueryClassificationNode().execute({"user_input": "EM-401 は保守的顧客に適合？"})
    assert out["status"] == AgentStatus.SUCCESS.value
    assert json.loads(out["classified_query"])["product_query"]


def test_tc02b_query_classification_s2_blocks_credential():
    leak = "MMF-001 api_key=sk-" + "a" * 25
    out = QueryClassificationNode().execute({"user_input": leak})
    assert out["status"] == AgentStatus.ERROR.value
    assert out["error_code"] == "S2_INJECTION_DETECTED"


# ── SuitabilityRetrievalNode (main 前半) ──
def test_tc03_retrieval_found():
    node = SuitabilityRetrievalNode(KnowledgeBase())
    out = node.execute({"classified_query": json.dumps({"product_query": "MMF-001"})})
    assert out["status"] == AgentStatus.SUCCESS.value
    assert json.loads(out["product_meta"])["product_id"] == "MMF-001"
    assert len(json.loads(out["retrieved_rules"])) >= 1


def test_tc04_retrieval_not_found():
    node = SuitabilityRetrievalNode(KnowledgeBase())
    out = node.execute({"classified_query": json.dumps({"product_query": "ZZZ-999"})})
    assert out["status"] == AgentStatus.ERROR.value
    assert out["error_code"] == "PRODUCT_NOT_FOUND"


# ── RegulatoryComplianceNode (main 後半) ──
def _retrieved(product_id):
    kb = KnowledgeBase()
    return {"product_meta": json.dumps(kb.get_product(product_id)),
            "retrieved_rules": json.dumps(kb.rules())}


def test_tc05_compliance_suitable_low_risk():
    out = RegulatoryComplianceNode().execute(_state_with_profile(_retrieved("MMF-001"), "Conservative"))
    assert json.loads(out["assessment"])["verdict"] == "Suitable"
    assert out["escalate_flag"] is False


def test_tc06_compliance_not_suitable_and_escalates():
    # EM-401 = risk 5; Conservative => Not Suitable; risk>=4 => escalate
    out = RegulatoryComplianceNode(escalate_risk_threshold=4).execute(
        _state_with_profile(_retrieved("EM-401"), "Conservative"))
    a = json.loads(out["assessment"])
    assert a["verdict"] == "Not Suitable"
    assert a["advisory_only"] is True
    assert out["escalate_flag"] is True


def test_tc07_compliance_profile_missing_requires_review():
    out = RegulatoryComplianceNode().execute(_state_with_profile(_retrieved("BAL-201"), None))
    assert json.loads(out["assessment"])["verdict"] == "Requires Review"
    assert out["escalate_flag"] is True


# ── SuitabilityAssessmentNode (main 合成: retrieval → compliance) ──
def test_tc07b_main_composite_runs_retrieval_then_compliance():
    node = SuitabilityAssessmentNode(KnowledgeBase(), escalate_risk_threshold=4)
    state = _state_with_profile(
        {"classified_query": json.dumps({"product_query": "MMF-001"})}, "Conservative")
    out = node.execute(state)
    assert out["status"] == AgentStatus.SUCCESS.value
    assert json.loads(out["product_meta"])["product_id"] == "MMF-001"
    assert json.loads(out["assessment"])["verdict"] == "Suitable"


def test_tc07c_main_composite_stops_on_retrieval_error():
    node = SuitabilityAssessmentNode(KnowledgeBase())
    out = node.execute({"classified_query": json.dumps({"product_query": "ZZZ-999"})})
    assert out["status"] == AgentStatus.ERROR.value
    assert out["error_code"] == "PRODUCT_NOT_FOUND"
    assert "assessment" not in out   # compliance は回らない


# ── AnswerGenerationNode (post_process) ──
def test_tc08_answer_includes_mandatory_disclaimer():
    assessment = {"verdict": "Suitable", "product_id": "MMF-001", "rationale": "ok", "disclosures": []}
    out = AnswerGenerationNode().execute({"assessment": json.dumps(assessment), "escalate_flag": False})
    assert DEFAULT_DISCLAIMER in out["formatted_output"]
    assert "Suitable" in out["formatted_output"]
    assert out["status"] == AgentStatus.SUCCESS.value


def test_tc09_answer_adds_escalation_note_when_flagged():
    assessment = {"verdict": "Not Suitable", "product_id": "EM-401", "rationale": "high risk", "disclosures": []}
    out = AnswerGenerationNode().execute({"assessment": json.dumps(assessment), "escalate_flag": True})
    assert "担当者" in out["formatted_output"]
    assert DEFAULT_DISCLAIMER in out["formatted_output"]
