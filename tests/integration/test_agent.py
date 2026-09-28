"""Integration tests — FIN-C2-025 InvestmentSuitabilityQAAgent E2E (released SDK v1.0.0).

released entry: invoke(user_input, session_id, ctx, input_context). The fixed backbone
(initialize → pre_process[S-2/分類] → main[検索+判定] → post_process[整形+S-3+S-4] → finalize)
runs deterministically. S-1（ADR-006）は entry ノードの required_trust_level(VERIFIED_EXTERNAL) を
`caller_trust_level`（ctx 由来）と照合し、不足/不在時は status="error" + error_log で停止する
（例外ではない）。顧客プロファイル（PII）は input_context["customer_profile"] 経由で渡す。
"""

import json
import logging

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.agent import InvestmentSuitabilityQAAgent
from src.nodes.answer_generation import DEFAULT_DISCLAIMER


def _run(agent, product="BAL-201", risk_tolerance="Moderate",
         level=TrustLevel.VERIFIED_EXTERNAL, session_id="s1", with_ctx=True):
    """released invoke 署名で起動。顧客プロファイルは input_context 経由（read-only）。"""
    ctx = (InvocationContext(caller_trust_level=level, session_id=session_id)
           if with_ctx else None)
    profile = {"risk_tolerance": risk_tolerance} if risk_tolerance else None
    return agent.invoke(
        user_input=product,
        session_id=session_id,
        ctx=ctx,
        input_context={"customer_profile": profile},
    )


def test_happy_path_suitable_with_disclaimer():
    out = _run(InvestmentSuitabilityQAAgent(), product="BAL-201", risk_tolerance="Moderate")
    assert out.get("error") is None
    assert out["status"] == "success"
    assert json.loads(out["assessment"])["verdict"] == "Suitable"
    assert DEFAULT_DISCLAIMER in out["formatted_output"]


def test_s2_blocks_credential_in_input():
    leak = "MMF-001 api_key=sk-" + "a" * 25
    out = _run(InvestmentSuitabilityQAAgent(), product=leak, risk_tolerance="Conservative")
    assert out["error_code"] == "S2_INJECTION_DETECTED"
    assert out.get("formatted_output") is None


def test_s1_absent_context_default_deny():
    # ctx 不在 → caller_trust_level=ANONYMOUS → entry ノードが status=error で停止（例外ではない）。
    out = InvestmentSuitabilityQAAgent().invoke(
        user_input="MMF-001", input_context={"customer_profile": {"risk_tolerance": "Conservative"}})
    assert out["status"] == "error"
    assert out.get("formatted_output") is None
    assert any("trust gate denied" in e.lower() for e in out["error_log"])


def test_s1_insufficient_trust_rejected():
    out = _run(InvestmentSuitabilityQAAgent(), product="MMF-001",
               risk_tolerance="Conservative", level=TrustLevel.ANONYMOUS)
    assert out["status"] == "error"
    assert any("trust gate denied" in e.lower() for e in out["error_log"])


def test_s1_internal_trust_allowed():
    out = _run(InvestmentSuitabilityQAAgent(), product="MMF-001",
               risk_tolerance="Conservative", level=TrustLevel.INTERNAL)
    assert out.get("error") is None
    assert json.loads(out["assessment"])["verdict"] == "Suitable"


def test_high_risk_escalates():
    out = _run(InvestmentSuitabilityQAAgent(), product="DERIV-501", risk_tolerance="Speculative")
    assert out["escalate_flag"] is True
    assert "担当者" in out["formatted_output"]


def test_profile_missing_requires_review():
    out = _run(InvestmentSuitabilityQAAgent(), product="BAL-201", risk_tolerance=None)
    assert out.get("error") is None
    assert json.loads(out["assessment"])["verdict"] == "Requires Review"
    assert out["escalate_flag"] is True


def test_s4_trace_emits_metadata_only(caplog):
    # S-4: post_process が emit_trace_event でドメインイベント（suitability_assessment_complete）を
    # agentcore.audit ロガーに 1 件出す（メタのみ）。SDK v1.0.0 では backbone（Initialize/Finalize ほか）
    # が node_start / node_complete を同じ agentcore.audit ロガーへ自動 emit するため、総件数ではなく
    # 我々が emit するドメインイベントだけを抽出して 1 件であることを検証する。
    with caplog.at_level(logging.INFO, logger="agentcore.audit"):
        _run(InvestmentSuitabilityQAAgent(), product="EM-401",
             risk_tolerance="Conservative", session_id="s9-raw-sid")
    records = [json.loads(r.getMessage()) for r in caplog.records if r.name == "agentcore.audit"]
    domain = [rec for rec in records if rec.get("event_type") == "suitability_assessment_complete"]
    assert len(domain) == 1
    record = domain[0]
    payload = record["payload"]
    # 我々が emit する payload には顧客 PII を含めない（session 相関は不可逆ハッシュ session_ref）。
    payload_blob = json.dumps(payload, ensure_ascii=False)
    assert "risk_tolerance" not in payload_blob
    assert "s9-raw-sid" not in payload_blob          # payload は生 session_id を持たない
    assert payload["session_ref"] and payload["session_ref"] != "s9-raw-sid"
    # 応答本文（formatted_output）も監査 payload に出さない。
    assert "formatted_output" not in payload_blob
    # NOTE: 監査エンベロープの "session_id" は framework が相関キーとして付与する（PII ではない
    # 呼び出し元提供の識別子）。我々が制御する payload に PII を載せないことが S-4 の責務。


def test_pii_not_in_output():
    # 一意な sentinel リスク許容度で起動し、キー名だけでなく **値そのもの** が顧客由来
    # フィールド（assessment / formatted_output）に漏れていないことを検証する
    #（rationale 等にプロファイル生値を埋め込む回帰を検知）。
    # NOTE: 商品 KB の suitable_for 等は標準許容度ラベルを列挙する **商品メタ（非 PII）** なので、
    #       sentinel は product_meta に正当に現れ得る。値レベルの PII 検査は **顧客由来フィールド**
    #       （assessment / formatted_output）に限定する。
    sentinel = "Aggressive"  # この商品(BAL-201)の suitable_for/unsuitable_for に依らず判定に使う許容度
    out = _run(InvestmentSuitabilityQAAgent(), product="BAL-201", risk_tolerance=sentinel)
    blob = json.dumps(out, ensure_ascii=False, default=str)
    # 顧客プロファイルのキー名は出力に現れない（input_context に閉じる）。
    assert "risk_tolerance" not in blob
    assert "customer_profile" not in blob
    assert "invocation_context" not in out
    # ★ 値レベルの漏洩検査: 顧客リスク許容度の生値が、顧客由来の出力フィールドに残っていないこと
    #   （FIEA §40 KYC: 顧客プロファイルは出力・State へ書き戻さない。verdict 等の派生結果のみ可）。
    assert sentinel not in (out.get("assessment") or "")
    assert sentinel not in (out.get("formatted_output") or "")


def test_independent_runs_do_not_share_state():
    agent = InvestmentSuitabilityQAAgent()
    out1 = _run(agent, product="MMF-001", risk_tolerance="Conservative", session_id="a")
    out2 = _run(agent, product="DERIV-501", risk_tolerance="Speculative", session_id="b")
    assert json.loads(out1["assessment"])["verdict"] == "Suitable"
    assert out2["escalate_flag"] is True
