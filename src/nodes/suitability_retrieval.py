"""SuitabilityRetrievalNode — FIN-C2-025 main スロット前半（KB 検索 / VectorRAG パターン）.

released AgentCore SDK (v1.0.0) 契約: `FunctionNode` を継承し
`execute(self, state) -> dict` で **部分 dict** を返す。status は
`AgentStatus.*.value`（文字列）。S-1 は required_trust_level を BaseNode.__call__ が
`state["caller_trust_level"]` と照合し自動強制（ADR-006・多層防御で後段ノードも明示宣言）。

責務（docs/02_design.md §3）:
- classified_query の product_query で商品 KB を同定（決定論ルックアップ。本番はベクタ/
  ハイブリッド検索へ差し替え可）。
- 該当なしは PRODUCT_NOT_FOUND で停止。
- product_meta（JSON）と retrieved_rules（JSON 配列・金商法 §40）を出力。

KB はコンストラクタ注入（テストは seed、本番は kb_path 由来）。
"""

from __future__ import annotations

import json

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.services.knowledge_base import KnowledgeBase


class SuitabilityRetrievalNode(FunctionNode):
    # S-1 多層防御: 後段ノードも明示宣言（単体実行でも S-1 を保つ / ADR-006）。
    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, knowledge_base: KnowledgeBase | None = None) -> None:
        self._kb = knowledge_base or KnowledgeBase()

    def execute(self, state: dict) -> dict:
        if state.get("error") or state.get("status") == AgentStatus.ERROR.value:
            return {"error": state.get("error"), "status": AgentStatus.ERROR.value}

        try:
            classified = json.loads(state.get("classified_query") or "{}")
        except (ValueError, TypeError):
            classified = {}
        product_query = classified.get("product_query", "")

        product = self._kb.find_product(product_query)
        if product is None:
            return {
                "status": AgentStatus.ERROR.value,
                "error": "product not found",
                "error_code": "PRODUCT_NOT_FOUND",
                "error_message": f"SuitabilityRetrievalNode: no product matched '{product_query}'",
            }

        # S-4: at least one domain event per node — framework の監査契約。
        # payload は非 PII の商品メタのみ（顧客情報は扱わないノード）。
        emit_trace_event(
            "product_retrieved",
            {"product_id": product.get("product_id"), "risk_category": product.get("risk_category")},
            state,
        )

        return {
            "product_meta": json.dumps(product, ensure_ascii=False),
            "retrieved_rules": json.dumps(self._kb.rules(), ensure_ascii=False),
            "status": AgentStatus.SUCCESS.value,
        }
