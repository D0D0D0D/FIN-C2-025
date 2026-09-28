"""QueryClassificationNode — FIN-C2-025 pre_process スロット（入力検証 + S-2 入力ゲート + 分類）.

released AgentCore SDK (v1.0.0) のノード契約:
- `FunctionNode` を継承し `execute(self, state) -> dict` のみ実装。
- **変更したキーだけ**を返す（full state / node_history は返さない。BaseNode.__call__ が
  node_history を 1 件追記する。node_history/error_log は operator.add reducer）。
- status は `AgentStatus.*.value`（文字列）で返す。S-1 は `required_trust_level` を
  `BaseNode.__call__` が `state["caller_trust_level"]` と照合し自動強制（ADR-006）。
- execute は state 1 引数で呼ばれる（__call__ は execute(state) のみ渡す。SDK v1.0.0 契約）。

責務（docs/02_design.md §3）:
- 入力検証: user_input が空なら EMPTY_INPUT で停止。
- S-2 入力ゲート: 呼び出し元が制御し得る tainted フィールド（user_input / session_id）に
  credential パターンが混入していれば S2_INJECTION_DETECTED で停止（旧 graph レベル
  _security_gate_input は released wheel に無いためここへインライン化した）。
- 決定論分類: intent="suitability_check" 固定、product_query=入力文字列（商品同定は main の
  KB ルックアップが担う）。
"""

from __future__ import annotations

import json

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.security import detect_credentials

# 呼び出し元が制御し得る tainted な入力フィールド（すべて S-2 で検査する）。
_TAINTED_INPUT_FIELDS = ("user_input", "session_id")


class QueryClassificationNode(FunctionNode):
    # S-1: pre_process(entry 相当) が agent の trust gate を BaseNode.__call__ で強制（ADR-006）。
    required_trust_level = TrustLevel.VERIFIED_EXTERNAL   # S-1: 認証済み窓口のみ

    @staticmethod
    def _fail(code: str, message: str) -> dict:
        return {
            "status": AgentStatus.ERROR.value,
            "error": message,
            "error_code": code,
            "error_message": message,
        }

    def execute(self, state: dict) -> dict:
        # 上流のエラー/終了ステータスはパススルー（pre_process は entry なので通常上流なしだが堅牢化）。
        if state.get("error") or state.get("status") == AgentStatus.ERROR.value:
            return {"error": state.get("error"), "status": AgentStatus.ERROR.value}

        # ── S-2: 入力ゲート（全 tainted フィールドの credential scan）──
        for field in _TAINTED_INPUT_FIELDS:
            if detect_credentials(str(state.get(field, ""))):
                return self._fail("S2_INJECTION_DETECTED", f"credential pattern in {field}")

        user_input = (state.get("user_input") or "").strip()
        if not user_input:
            return self._fail("EMPTY_INPUT", "user_input is empty or missing")

        classified = {"intent": "suitability_check", "product_query": user_input}

        # S-4: at least one domain event per node — framework の監査契約。
        # payload はメタのみ（user_input 本文・生 session_id は含めない）。
        emit_trace_event(
            "query_classified",
            {"intent": classified["intent"], "product_query_len": len(user_input)},
            state,
        )

        return {
            "classified_query": json.dumps(classified, ensure_ascii=False),
            "status": AgentStatus.SUCCESS.value,
        }
