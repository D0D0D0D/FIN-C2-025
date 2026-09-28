"""Proof-of-Boundary — FIN-C2-025 (released AgentCore SDK v1.0.0).

released wheel の固定 5 ノード backbone（initialize→pre_process→main→route→post_process→
finalize）と node 契約に整合させて移行（旧 _invoke_impl / graph レベル gate フック /
SecurityViolationError 例外モデルは廃止）。

PB-S1  未認証拒否（S-1 トラストゲート非バイパス・absent/ANONYMOUS → status=error）
PB-S2  顧客 PII（リスク許容度・年齢・顧客 ID）が State / 出力 / 監査に漏れない（input_context に閉じる）
PB-S3  成功応答には必ず免責文が含まれる + credential はブロック（S3_BLOCKED）
PB-ESC 高リスク商品（risk_category >= threshold）は escalate_flag が立つ
PB-ORD backbone 実行順序（initialize → pre → main → post → finalize）
PB-MSG 出力は msgpack 安全（checkpoint 安全）
PB-NODE ノード契約（execute のみ / _invoke_impl 不実装 / required_trust_level 宣言）
"""

import ast
import inspect
import json
import logging
import os

import ormsgpack
import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.agent import InvestmentSuitabilityQAAgent
from src.nodes.answer_generation import AnswerGenerationNode, DEFAULT_DISCLAIMER
from src.nodes.query_classification import QueryClassificationNode
from src.nodes.suitability_assessment import SuitabilityAssessmentNode

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
_VALID_TRUST = {TrustLevel.VERIFIED_EXTERNAL, TrustLevel.INTERNAL}
_NODE_CLASSES = [QueryClassificationNode, SuitabilityAssessmentNode, AnswerGenerationNode]


def _run(agent, product="MMF-001", risk_tolerance="Conservative",
         level=TrustLevel.VERIFIED_EXTERNAL, session_id="s", profile_extra=None):
    ctx = InvocationContext(caller_trust_level=level, session_id=session_id)
    profile = {"risk_tolerance": risk_tolerance, **(profile_extra or {})} if risk_tolerance else None
    return agent.invoke(user_input=product, session_id=session_id, ctx=ctx,
                        input_context={"customer_profile": profile})


# ── PB-S1: 未認証拒否（非バイパス）──
def test_pb_s1_absent_context_rejected():
    out = InvestmentSuitabilityQAAgent().invoke(
        user_input="MMF-001",
        input_context={"customer_profile": {"risk_tolerance": "Conservative"}})
    assert out["status"] == "error"
    assert out.get("formatted_output") is None
    assert any("trust gate denied" in e.lower() for e in out["error_log"])


def test_pb_s1_anonymous_rejected():
    out = _run(InvestmentSuitabilityQAAgent(), level=TrustLevel.ANONYMOUS)
    assert out["status"] == "error"
    assert any("trust gate denied" in e.lower() for e in out["error_log"])


# ── PB-S2: 顧客 PII が State/出力/監査に漏れない ──
def test_pb_s2_customer_pii_not_in_output(caplog):
    agent = InvestmentSuitabilityQAAgent()
    with caplog.at_level(logging.INFO, logger="agentcore.audit"):
        out = _run(agent, product="BAL-201", risk_tolerance="Moderate",
                   profile_extra={"age": 65, "customer_id": "C-12345"})
    blob = json.dumps(out, ensure_ascii=False, default=str)
    assert "C-12345" not in blob                 # 顧客 ID は出力に現れない
    assert "risk_tolerance" not in blob          # リスク許容度（PII キー）も現れない
    assert "customer_profile" not in blob
    assert "invocation_context" not in out
    # 監査ログ payload にも PII は出さない（framework 相関キー session_id/uuid とは別物）
    for r in caplog.records:
        if r.name != "agentcore.audit":
            continue
        payload_blob = json.dumps(json.loads(r.getMessage())["payload"], ensure_ascii=False)
        assert "C-12345" not in payload_blob
        assert "risk_tolerance" not in payload_blob


# ── PB-S3: 免責文の強制 + credential ブロック ──
def test_pb_s3_disclaimer_enforced_and_credential_blocked():
    agent = InvestmentSuitabilityQAAgent()
    ok = _run(agent, product="MMF-001", risk_tolerance="Conservative")
    assert DEFAULT_DISCLAIMER in ok["formatted_output"]
    # credential 混入はブロック（S-2 入力ゲート）
    leak = _run(agent, product="MMF-001 token=sk-" + "z" * 25, risk_tolerance="Conservative")
    assert leak.get("formatted_output") is None
    assert leak["error_code"] == "S2_INJECTION_DETECTED"


def test_pb_s2_credential_in_session_id_blocked():
    # session_id（呼び出し元制御）に credential を仕込んでもブロックされる（user_input 以外も検査）。
    # SDK v1.0.0 では framework の S-3 出力ゲートが backbone InitializeNode で session_id 内の
    # credential を検出し status=error で停止させる（rc1 ではノード内インライン S-2 が
    # error_code="S2_INJECTION_DETECTED" を立てていたが、1.0.0 では framework ゲートが先に発火する）。
    # いずれにせよ「応答が一切返らない（formatted_output is None）かつ status=error」という
    # セキュリティ不変条件が保たれることを検証する。
    agent = InvestmentSuitabilityQAAgent()
    ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
                            session_id="sk-" + "q" * 25)
    out = agent.invoke(user_input="MMF-001", session_id="sk-" + "q" * 25, ctx=ctx,
                       input_context={"customer_profile": {"risk_tolerance": "Conservative"}})
    assert out["status"] == "error"
    assert out.get("formatted_output") is None


def test_pb_s3_blocks_credential_in_output():
    # post_process ノード単体: 下流が credential を出力に混入させても S-3 が null 化（S3_BLOCKED）。
    leaked_assessment = {"verdict": "sk-" + "w" * 25, "product_id": "X", "rationale": "", "disclosures": []}
    out = AnswerGenerationNode().execute(
        {"assessment": json.dumps(leaked_assessment), "escalate_flag": False})
    assert out["error_code"] == "S3_BLOCKED"
    assert out.get("formatted_output") is None


# ── PB-ESC: 高リスク商品は escalate ──
def test_pb_high_risk_triggers_escalation():
    out = _run(InvestmentSuitabilityQAAgent(), product="DERIV-501", risk_tolerance="Speculative")
    assert out["escalate_flag"] is True
    assert "担当者" in out["formatted_output"]


# ── PB-ORD: backbone 実行順序 ──
def test_pb_node_execution_order():
    out = _run(InvestmentSuitabilityQAAgent(), product="MMF-001", risk_tolerance="Conservative")
    assert out["node_history"] == [
        "InitializeNode",
        "QueryClassificationNode",
        "SuitabilityAssessmentNode",
        "AnswerGenerationNode",
        "FinalizeNode",
    ]


# ── PB-MSG: 出力は msgpack 安全 ──
def test_pb_output_is_msgpack_safe():
    out = _run(InvestmentSuitabilityQAAgent(), product="MMF-001", risk_tolerance="Conservative")
    ormsgpack.packb(out)
    for key, value in out.items():
        assert isinstance(value, (str, int, float, bool, type(None), list, dict)), (
            f"out['{key}'] is {type(value).__name__} — not msgpack-safe")


# ── PB-NODE: ノード契約 ──
@pytest.mark.parametrize("cls", _NODE_CLASSES)
def test_node_contract_execute_only(cls):
    assert "execute" in cls.__dict__, f"{cls.__name__} must implement execute()"
    assert "_invoke_impl" not in cls.__dict__, (
        f"{cls.__name__} must NOT implement _invoke_impl() — it is the graph execution hook")


@pytest.mark.parametrize("cls", _NODE_CLASSES)
def test_all_nodes_declare_required_trust_level(cls):
    assert "required_trust_level" in cls.__dict__, (
        f"{cls.__name__} must explicitly declare required_trust_level")
    assert cls.required_trust_level in _VALID_TRUST, (
        f"{cls.__name__} required_trust_level must be VERIFIED_EXTERNAL or INTERNAL")


@pytest.mark.parametrize("cls", _NODE_CLASSES)
def test_execute_method_signature(cls):
    # SDK v1.0.0 厳密契約: BaseNode.__call__ は execute(state) を state 1 引数のみで呼ぶ。
    # config は渡されないため、ノード execute は execute(self, state) でなければならない
    # （余計な config 引数を持たない）。
    sig = inspect.signature(cls.execute)
    params = list(sig.parameters)
    assert params == ["self", "state"], (
        f"{cls.__name__}.execute must be execute(self, state) (SDK v1.0.0 contract), got {params}")
    assert "config" not in sig.parameters, (
        f"{cls.__name__}.execute must NOT accept config — __call__ passes state only (SDK v1.0.0)")


# ── 内側サブノードも status を文字列で返す（AgentStatus 生 enum 返却の回帰防止）──
def test_status_returned_as_string_value():
    out = QueryClassificationNode().execute({"user_input": "MMF-001"})
    assert out["status"] == AgentStatus.SUCCESS.value
    assert isinstance(out["status"], str)


# ── src/ が Level 0 agenticstar を import しない（多層化のため PB でも検証）──
def test_no_agenticstar_import_in_src():
    violations = []
    for root, _, files in os.walk(_SRC):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            tree = ast.parse(open(path).read(), filename=path)
            for n in ast.walk(tree):
                mod = ""
                if isinstance(n, ast.Import):
                    mod = n.names[0].name
                elif isinstance(n, ast.ImportFrom):
                    mod = n.module or ""
                if mod.startswith("agenticstar"):
                    violations.append(f"{path}: {mod}")
    assert violations == [], "Level 0 import(s) found: " + "; ".join(violations)
