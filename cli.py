"""AGENTIC STAR Marketplace entry adapter for FIN-C2-025."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

try:
    from framework.utils.config_loader import load_agent_config
except ImportError:  # AgentCore 1.0.0 / 1.0.1 compatibility
    from framework.utils.config_loader import load_config

    def load_agent_config(agent_dir: Path) -> dict[str, Any]:
        config_path = agent_dir / "config" / "config.yaml"
        return load_config(str(config_path)) if config_path.exists() else {}


try:
    from shared.bootstrap import run_agent_marketplace
except ModuleNotFoundError as exc:  # AgentCore 1.0.0 / 1.0.1 compatibility
    if exc.name != "shared.bootstrap":
        raise
    _BOOTSTRAP_IMPORT_ERROR: ModuleNotFoundError | None = exc

    def run_agent_marketplace(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError(
            "The installed AgentCore wheel does not provide Marketplace bootstrap support"
        ) from _BOOTSTRAP_IMPORT_ERROR
else:
    _BOOTSTRAP_IMPORT_ERROR = None


from src.agent import InvestmentSuitabilityQAAgent

_NAMESPACE = "fin"
_AGENT_NAME = "FIN-C2-025"


class MarketplaceGraph(InvestmentSuitabilityQAAgent):
    """Marketplace boundary for the deterministic suitability Q&A agent."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        merged = dict(config or {})
        super().__init__(config=merged)

    def invoke(
        self,
        user_input: str,
        session_id: str = "",
        ctx: InvocationContext | None = None,
        input_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Runner-owned conversation_history and any future keys are untrusted.
        # This adapter owns no input-context keys, so rebuild rather than merge.
        del input_context
        base_ctx = (
            ctx
            if ctx is not None
            else (InvocationContext(session_id=session_id) if session_id else InvocationContext())
        )
        marketplace_ctx = replace(
            base_ctx,
            caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
            hitl_allowed=False,
        )
        return super().invoke(
            user_input,
            session_id=marketplace_ctx.session_id,
            ctx=marketplace_ctx,
            input_context={},
        )

    def _enrich_output(self, output: dict[str, Any], result: dict[str, Any]) -> None:
        super()._enrich_output(output, result)
        if output.get("status") == AgentStatus.SUCCESS.value:
            output["output"] = result.get("formatted_output")
        # On failure, keep the graph's diagnostics and omit ``output``: exposing a
        # failure payload as a Marketplace result would disguise it as an artifact.


def main() -> None:
    run_agent_marketplace(
        MarketplaceGraph,
        agent_name=_AGENT_NAME,
        namespace=_NAMESPACE,
        config=load_agent_config(Path(__file__).resolve().parent),
    )


if __name__ == "__main__":
    main()
