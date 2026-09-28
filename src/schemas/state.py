"""State schema for FIN-C2-025 InvestmentSuitabilityQAAgent.

Released AgentCore SDK (v1.0.0) 準拠: `framework.schemas.agent_state.AgentState` を
直接継承する flat TypedDict。共有フィールド（user_input / input_context / status /
caller_trust_level / node_history / error_log / formatted_output / session_id /
trace_id / correlation_id / execution_time 等）は AgentState から継承する。

ここにはドメイン固有フィールドのみ追加する。primitives + JSON 文字列のみ（msgpack 安全
= LangGraph checkpoint 安全。docs/02_design.md §4）。
classified_query / retrieved_rules / product_meta / assessment は JSON STRING で保持し、
list/dict を直接 state に入れない（PB-2 safety）。

顧客プロファイル（年齢・投資経験・リスク許容度・顧客 ID）は PII であり **State には絶対に保存しない**。
released wheel の `execute(state)` には config が渡らないため、PII は読み取り専用の
`state["input_context"]["customer_profile"]` 経由で渡す（金商法 KYC 保護 + checkpoint 漏洩防止
— docs/02_design.md §4/§5）。input_context は read-only（ノードは書き戻さない）。

NOTE: InvocationContext は State に保存しない（checkpoint に載せられない = PB-5）。trust は invoke() が
`caller_trust_level` として書き込む（AgentState 継承フィールド）。
"""

from __future__ import annotations

from typing import Optional

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """InvestmentSuitabilityQAAgent のドメイン State（AgentState 拡張）。"""

    # ── QueryClassificationNode 出力（pre_process）──
    classified_query: Optional[str]    # JSON: {"intent","product_query"}

    # ── SuitabilityRetrievalNode 出力（main 前半）──
    retrieved_rules: Optional[str]     # JSON 配列文字列: 金商法 §40 ルール
    product_meta: Optional[str]        # JSON: {"product_id","risk_category","suitable_for",...}

    # ── RegulatoryComplianceNode 出力（main 後半）──
    assessment: Optional[str]          # JSON: {"verdict","rationale","disclosures"}
    escalate_flag: Optional[bool]      # 高リスク商品の人間エスカレーション要否

    # ── AnswerGenerationNode 出力（post_process）──
    formatted_output: Optional[str]    # S-3 通過後の最終応答（構造化 + 自然文 + 免責）

    # ── エラー伝播（status とは別にドメイン診断コードを保持）──
    error: Optional[str]               # 真値が立つと下流ノードがパススルー
    error_code: Optional[str]          # EMPTY_INPUT / PRODUCT_NOT_FOUND / S2_INJECTION_DETECTED / S3_BLOCKED 等
    error_message: Optional[str]       # 人間可読の詳細
