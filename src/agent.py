"""InvestmentSuitabilityQAAgent — FIN-C2-025 (released AgentCore SDK v1.0.0).

Agent は Graph を継承し、本番エントリは L1 `AgentBaseGraph.invoke(user_input, session_id,
ctx, input_context)` を継承する（`.run()` は存在しない — 呼ぶと AttributeError になり、
S-1 trust ゲートも通らない）。`self.graph` 合成も不可（独自クラスは 5 層セキュリティを迂回する）。

5 層セキュリティ（released SDK の実配線に整合）:
  S-1 required_trust_level : `BaseNode.__call__()` が各ノードで自動強制（ADR-006）。entry の
                             QueryClassificationNode が VERIFIED_EXTERNAL を要求（default-deny）。
  S-2 入力ゲート            : QueryClassificationNode.execute 内（user_input / session_id の
                             credential scan）。
  S-3 出力ゲート            : AnswerGenerationNode.execute 内（必須免責の強制 + credential ブロック）。
  S-4 監査トレース          : AnswerGenerationNode.execute 内 emit_trace_event（メタのみ・本文/PII なし）。
  S-5 credential scan       : CI の静的スキャン（ソースに資格情報を置かない）。

顧客プロファイル（リスク許容度等の PII）は invoke(..., input_context={"customer_profile": ...}) で
渡す（read-only。State 本体には入れない — 金商法 KYC, docs/02_design.md §4）。

旧 graph レベルの _security_gate_input/output / _emit_trace_event / _invoke_impl フックは
released wheel に無いため廃止（S-2/S-3/S-4 を各ノードへ移設・移行: SDK v1.0.0）。
"""

from __future__ import annotations

from framework.schemas.trust_level import TrustLevel

from src.graph.graph import InvestmentSuitabilityQAGraph

TEMPLATE_ID = "FIN-C2-025"
# 有効値は ANONYMOUS / VERIFIED_EXTERNAL / INTERNAL の 3 つのみ（他の値は import 時に AttributeError）。
REQUIRED_TRUST_LEVEL = TrustLevel.VERIFIED_EXTERNAL


class InvestmentSuitabilityQAAgent(InvestmentSuitabilityQAGraph):
    """Cat 2 投資商品適合性 Q&A エージェント。Graph を継承し invoke() を継承する。"""

    TEMPLATE_ID = TEMPLATE_ID
    REQUIRED_TRUST_LEVEL = REQUIRED_TRUST_LEVEL
