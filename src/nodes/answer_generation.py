"""AnswerGenerationNode — FIN-C2-025 post_process スロット（整形 + S-3 出力ゲート + S-4 監査）.

released AgentCore SDK (v1.0.0) 契約: `FunctionNode` を継承し
`execute(self, state) -> dict` で **部分 dict** を返す。status は
`AgentStatus.*.value`。post_process は route() が status==SUCCESS のときのみ到達する。

責務（docs/02_design.md §3/§5/§8）:
- 構造化判定 + 自然文サマリを生成し、**必須の免責文を決定論的に強制注入**する
  （compliance-critical のため免責の有無を LLM に委ねない）。escalate_flag が立つ場合は
  人間担当者への引き継ぎ指示を付す。
- S-3 出力ゲート（旧 agent レベル _security_gate_output を本ノードへインライン化。released wheel に
  graph レベルゲートは無い）:
    1) 免責文が無ければ強制注入（多層防御の最後の砦）。
    2) 出力に credential パターンが残存していれば S3_BLOCKED で応答を null 化し停止。
- S-4 監査トレース（旧 agent レベル _emit_trace_event を本ノードへ移設）: `shared.utils.audit_logger`
  の emit_trace_event でメタのみ記録（user_input / 顧客 PII / 生 session_id / 応答本文は NEVER 記録しない。
  session 相関は不可逆ハッシュ参照。金商法 記録保存）。
"""

from __future__ import annotations

import hashlib
import json

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.security import detect_credentials

_TEMPLATE_ID = "FIN-C2-025"

DEFAULT_DISCLAIMER = (
    "この情報は参考目的のみです。投資判断は必ず担当アドバイザーにご相談ください。"
    "金融商品の購入は元本割れリスクがあります。"
)


class AnswerGenerationNode(FunctionNode):
    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, compliance_disclaimer: str = DEFAULT_DISCLAIMER) -> None:
        self._disclaimer = compliance_disclaimer or DEFAULT_DISCLAIMER

    def _emit_audit(self, state: dict, assessment: dict) -> None:
        """S-4: メタのみ監査記録（本文・PII・生 session_id は出さない）。"""
        sid = str(state.get("session_id") or "")
        session_ref = hashlib.sha256(sid.encode("utf-8")).hexdigest()[:16] if sid else ""
        emit_trace_event(
            "suitability_assessment_complete",
            {
                "template_id": _TEMPLATE_ID,
                "session_ref": session_ref,          # raw session_id ではなくハッシュ参照
                "verdict": assessment.get("verdict"),
                "product_id": assessment.get("product_id"),
                "risk_category": assessment.get("risk_category"),
                "escalate_flag": bool(state.get("escalate_flag")),
            },
            state,
        )

    def execute(self, state: dict) -> dict:
        if state.get("error") or state.get("status") == AgentStatus.ERROR.value:
            return {"error": state.get("error"), "status": AgentStatus.ERROR.value}

        try:
            assessment = json.loads(state.get("assessment") or "{}")
        except (ValueError, TypeError):
            assessment = {}

        verdict = assessment.get("verdict", "Requires Review")
        rationale = assessment.get("rationale", "")
        product_id = assessment.get("product_id", "")
        escalate = bool(state.get("escalate_flag"))

        lines = [
            f"適合性判定: {verdict}（商品: {product_id}）",
            rationale,
        ]
        if escalate:
            lines.append(
                "⚠️ 本商品は高リスク区分のため、最終的な適合性確認と販売判断は人間の担当者が行ってください。"
            )
        # 免責文は常に末尾へ強制注入（compliance-critical・LLM に委ねない）
        lines.append(self._disclaimer)
        output = "\n".join(s for s in lines if s)

        # ── S-3 出力ゲート（ノード内多層防御）──
        # 1) 免責文の強制（テンプレ済だが、将来 LLM 整形に差し替えた場合の最後の砦）
        if self._disclaimer not in output:
            output = f"{output}\n{self._disclaimer}"
        # 2) credential 残存チェック → S3_BLOCKED で応答を null 化
        if detect_credentials(output):
            return {
                "error": "credential pattern in formatted output",
                "error_code": "S3_BLOCKED",
                "error_message": "credential pattern in formatted output",
                "formatted_output": None,
                "status": AgentStatus.ERROR.value,
            }

        # ── S-4 監査トレース（メタのみ）──
        self._emit_audit(state, assessment)

        return {"formatted_output": output, "status": AgentStatus.SUCCESS.value}
