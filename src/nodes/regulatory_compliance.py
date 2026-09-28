"""RegulatoryComplianceNode — FIN-C2-025 main スロット後半（ComplianceGate）.

責務（docs/02_design.md §3/§5）: 適合性突合 → 区分判定 + ディスクロージャー + エスカレーション判定。
- 顧客リスク許容度は **read-only な `state["input_context"]["customer_profile"]`** から読む。
  released wheel の `execute(state)` には config が渡らない（__call__ は execute(state) を呼ぶ）ため、
  顧客プロファイル（PII）は invoke(..., input_context=...) で渡し input_context 経由で受け取る。
  State 本体には PII を書き込まない（設計 §4 厳守 / checkpoint 漏洩防止）。
- assess_suitability(risk_tolerance, risk_category) で Suitable / Requires Review / Not Suitable。
- risk_category >= escalate_risk_threshold（既定 4）で escalate_flag=True（人間へ）。
- "reference" の範囲に留め "recommendation"（特定商品の推奨）は生成しない。

released SDK 契約: FunctionNode 継承・execute(self, state) で部分 dict を返す。
status は AgentStatus.*.value。S-1: required_trust_level 宣言（自動強制）。
"""

from __future__ import annotations

import json

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.services.knowledge_base import assess_suitability


class RegulatoryComplianceNode(FunctionNode):
    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, escalate_risk_threshold: int = 4) -> None:
        self._escalate_threshold = int(escalate_risk_threshold)

    @staticmethod
    def _risk_tolerance(state: dict) -> str | None:
        """顧客リスク許容度を read-only な input_context **のみ**から取得。

        PII（顧客プロファイル）は `state["input_context"]["customer_profile"]` 経由が唯一の
        正規経路（invoke の input_context 引数。read-only・State 本体には書き戻さない）。
        ここから読むだけで、ドメイン State へ PII が混入する余地を作らない（設計 §4 厳守）。
        """
        input_context = state.get("input_context") or {}
        profile = input_context.get("customer_profile")
        if isinstance(profile, dict):
            return profile.get("risk_tolerance")
        return None

    def execute(self, state: dict) -> dict:
        if state.get("error") or state.get("status") == AgentStatus.ERROR.value:
            return {"error": state.get("error"), "status": AgentStatus.ERROR.value}

        try:
            product = json.loads(state.get("product_meta") or "{}")
            rules = json.loads(state.get("retrieved_rules") or "[]")
        except (ValueError, TypeError):
            product, rules = {}, []

        risk_category = int(product.get("risk_category", 0) or 0)
        risk_tolerance = self._risk_tolerance(state)

        if not risk_tolerance:
            # プロファイル欠落 → 安全側に倒し人間確認（判定を断定しない）
            verdict = "Requires Review"
            rationale = "顧客のリスク許容度が未提供のため、適合性を断定できません。担当者の確認が必要です。"
            escalate = True
        else:
            verdict = assess_suitability(risk_tolerance, risk_category)
            # rationale には顧客リスク許容度（PII）の生値を入れない。
            # 出力/State に残ってよいのは派生結果（verdict）と非 PII の商品リスク区分のみ。
            # 生の risk_tolerance は input_context に閉じ、ここでは判定（verdict）に昇華して捨てる
            # （金商法 KYC 保護 / docs/02_design.md §4: customer_profile は出力・State へ書き戻さない）。
            rationale = (
                f"お客様のリスク許容度プロファイル × 商品リスク区分 {risk_category} の"
                f"適合性マトリクス判定: {verdict}。"
            )
            escalate = risk_category >= self._escalate_threshold

        assessment = {
            "verdict": verdict,                          # Suitable / Requires Review / Not Suitable
            "product_id": product.get("product_id"),
            "risk_category": risk_category,
            "rationale": rationale,
            "disclosures": [r.get("text") for r in rules],
            "advisory_only": True,                       # reference であり recommendation ではない
        }

        # S-4: at least one domain event per node — framework の監査契約。
        # payload は判定結果のみ（risk_tolerance などの顧客 PII は含めない・§4 厳守）。
        emit_trace_event(
            "suitability_assessed",
            {"verdict": verdict, "risk_category": risk_category, "escalate_flag": bool(escalate)},
            state,
        )

        return {
            "assessment": json.dumps(assessment, ensure_ascii=False),
            "escalate_flag": bool(escalate),
            "status": AgentStatus.SUCCESS.value,
        }
