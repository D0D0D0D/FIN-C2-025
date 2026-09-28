"""SuitabilityAssessmentNode — FIN-C2-025 main スロット（retrieval → compliance の逐次合成）.

released AgentCore SDK (v1.0.0) の固定 backbone は pre_process / main / post_process の
**3 スロット**しか持たない。本テンプレの論理ノードは 4 つ（query_classification /
suitability_retrieval / regulatory_compliance / answer_generation）なので、KB 検索（retrieval）と
適合性判定（compliance）を **main スロット内で逐次実行**する 1 ノードに合成する
（GovernmentProcedureQAAgent / F.Liu の `_SequentialSlotNode` パターン）。

逐次合成の規約:
- 内側の各サブノードは `FunctionNode.execute(state)` を直接呼ぶ（__call__ は backbone が main 全体に
  対して 1 回だけ回す。S-1 ゲートは main エントリ＝本ノードの required_trust_level で強制される）。
- サブノードは **部分 dict** を返すので、合成側で state にマージしてから次段へ渡す（backbone と同じ
  マージ規約をローカルに再現）。retrieval が status=error を返したら compliance を回さず停止する。
- node_history は backbone（BaseNode.__call__）が本ノード名（SuitabilityAssessmentNode）を 1 件追記する。
  内側サブノードは node_history を返さない（部分 dict のため二重化しない）。

責務（docs/02_design.md §3）:
  main = SuitabilityRetrievalNode（KB 検索）→ RegulatoryComplianceNode（適合性判定）。
"""

from __future__ import annotations

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.nodes.regulatory_compliance import RegulatoryComplianceNode
from src.nodes.suitability_retrieval import SuitabilityRetrievalNode
from src.services.knowledge_base import KnowledgeBase


class SuitabilityAssessmentNode(FunctionNode):
    """main スロット: KB 検索 → 適合性判定 を逐次実行する合成ノード。"""

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def __init__(
        self,
        knowledge_base: KnowledgeBase | None = None,
        escalate_risk_threshold: int = 4,
    ) -> None:
        self._retrieval = SuitabilityRetrievalNode(knowledge_base)
        self._compliance = RegulatoryComplianceNode(escalate_risk_threshold)

    def execute(self, state: dict) -> dict:
        # 上流（pre_process）のエラー/S-1 拒否はパススルー（pre_process→main は無条件辺）。
        if state.get("error") or state.get("status") == AgentStatus.ERROR.value:
            return {"error": state.get("error"), "status": AgentStatus.ERROR.value}

        # ── 1) KB 検索 ──
        retrieval_out = self._retrieval.execute(state)
        if retrieval_out.get("status") == AgentStatus.ERROR.value or retrieval_out.get("error"):
            return retrieval_out

        # 検索結果を合成し、compliance へ渡す（backbone のマージ規約をローカル再現）。
        merged = dict(state)
        merged.update(retrieval_out)

        # ── 2) 適合性判定（PII は merged["input_context"]["customer_profile"] 経由で読む）──
        compliance_out = self._compliance.execute(merged)

        # main の出力は retrieval + compliance の差分の和集合（部分 dict）。
        out = dict(retrieval_out)
        out.update(compliance_out)

        # S-4: at least one domain event per node — framework の監査契約。
        # main スロット（合成ノード）自体の完了イベント（内側サブノードのイベントとは別に記録）。
        emit_trace_event(
            "main_slot_complete",
            {"status": out.get("status"), "escalate_flag": bool(out.get("escalate_flag"))},
            state,
        )

        return out
