"""InvestmentSuitabilityQAGraph — FIN-C2-025 (released AgentCore SDK v1.0.0).

released L1 `AgentBaseGraph` は **固定 5 ノード backbone** を強制する:
    START → initialize → pre_process → main → {route} → post_process → finalize → END
                                            ↓ (RETRY, max 3)
                                         pre_process
`super().register_nodes()` が initialize / finalize を自動注入し、テンプレは
pre_process / main / post_process の 3 スロットを埋める（compile() が None を検出すると
MissingNodeError）。route() は state["status"] を読み、SUCCESS のときのみ post_process に
到達する。`add_edges()` / `route()` は override しない（L1 が配線する）。

論理 4 ノード（query_classification → suitability_retrieval → regulatory_compliance →
answer_generation）を 3 スロットへマッピングする（docs/02_design.md §2, §3）:
    pre_process  : QueryClassificationNode      (S-1 entry / S-2 入力ゲート / 分類)
    main         : SuitabilityAssessmentNode    (KB 検索 → 適合性判定 を逐次合成)
    post_process : AnswerGenerationNode          (整形 + S-3 出力ゲート + S-4 監査)

旧 graph レベルの _security_gate_input/output / _emit_trace_event は released wheel に存在
しないため廃止し、S-2/S-3/S-4 を各ノード内に実装した（移行: SDK v1.0.0）。
KnowledgeBase は依存性注入可能（テストは seed 注入、本番は kb_path 由来）。
"""

from __future__ import annotations

from framework.graph.agent_base_graph import AgentBaseGraph

from src.nodes.answer_generation import DEFAULT_DISCLAIMER, AnswerGenerationNode
from src.nodes.query_classification import QueryClassificationNode
from src.nodes.suitability_assessment import SuitabilityAssessmentNode
from src.schemas.state import State
from src.services.knowledge_base import KnowledgeBase


class InvestmentSuitabilityQAGraph(AgentBaseGraph):
    """固定 3 スロット: pre_process(分類) → main(検索+判定) → post_process(整形+S-3+S-4)。"""

    def __init__(self, config: dict | None = None, knowledge_base: KnowledgeBase | None = None) -> None:
        cfg = dict(config or {})
        super().__init__(config=cfg)  # BaseGraph.__init__ が self.config を保持（register は compile 時）
        self._kb_path = cfg.get("kb_path")
        self._escalate_threshold = int(cfg.get("escalate_risk_threshold", 4))
        self._disclaimer = cfg.get("compliance_disclaimer", DEFAULT_DISCLAIMER)
        # テストは KB 注入、本番は kb_path 由来（未指定はシード）
        self._kb = knowledge_base or KnowledgeBase.from_path(self._kb_path)

    @property
    def name(self) -> str:
        return "InvestmentSuitabilityQAAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # initialize / finalize 自動注入 + pre/main/post スロット枠
        self._nodes["pre_process"] = QueryClassificationNode()
        self._nodes["main"] = SuitabilityAssessmentNode(
            knowledge_base=self._kb,
            escalate_risk_threshold=self._escalate_threshold,
        )
        self._nodes["post_process"] = AnswerGenerationNode(self._disclaimer)

    def get_output(self, state) -> dict:
        """最終 state からクライアント向け出力を構築（primitives + list/dict のみ = msgpack 安全）。"""
        return {
            "formatted_output": state.get("formatted_output"),
            "assessment": state.get("assessment"),
            "escalate_flag": state.get("escalate_flag"),
            "product_meta": state.get("product_meta"),
            "classified_query": state.get("classified_query"),
            "status": state.get("status"),
            "error": state.get("error"),
            "error_code": state.get("error_code"),
            "error_message": state.get("error_message"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "session_id": state.get("session_id"),
            "node_history": state.get("node_history", []),
            "error_log": state.get("error_log", []),
        }
