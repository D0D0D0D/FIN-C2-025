# FIN-C2-025 — Investment Product Suitability Assessment Q&A Agent

> **Category**: Cat 2 (domain workflow)
> **Industry**: FIN

## Overview

This agent checks a named investment product against a customer's risk-tolerance profile and
returns a suitability reading with its rationale, the disclosures that apply, and an escalation
flag. It looks the product up in a knowledge base, resolves the verdict from a
risk-tolerance × risk-category matrix, attaches the regulatory disclosures the case requires, and
escalates to a human when the product is high-risk or the customer profile is missing.

It is reference material for a person, not a sales decision. Nothing in the output recommends a
purchase, and every response carries the advisory disclaimer; `advisory_only` is part of the
assessment payload rather than a formatting flourish.

The design is deliberately deterministic. The verdict comes from a lookup table, not a model:

| Risk tolerance | Category 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| Conservative | Suitable | Suitable | Requires Review | Not Suitable | Not Suitable |
| Moderate | Suitable | Suitable | Suitable | Requires Review | Not Suitable |
| Aggressive | Suitable | Suitable | Suitable | Suitable | Requires Review |
| Speculative | Suitable | Suitable | Suitable | Suitable | Suitable |

A product that is not in the knowledge base returns `PRODUCT_NOT_FOUND` rather than a guess, and
a request with no customer profile does not produce a definitive verdict — it escalates. Both are
the point: an unfounded suitability answer in this domain is worse than no answer.

Customer profile data — age, investment experience, risk tolerance, customer identifier — is
treated as personal data throughout. It is passed read-only through `input_context`, is never
written into agent state, and therefore never reaches a checkpoint.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Integration

Provide:

- an AgentCore runtime;
- your own product catalogue at `kb_path`. The shipped knowledge base is a six-product synthetic
  seed plus a seed of the applicable regulatory rules — enough to exercise the pipeline, not a
  substitute for a managed catalogue with version control over the regulatory text;
- an authenticated caller, and a gateway that supplies the customer profile. The standalone
  server reads it from `request.state.customer_profile`; nothing accepts it from the request body.

No language-model client is needed. `generation_mode` is `deterministic`: the matrix, the
disclosure selection and the escalation rule are all code, and the agent declares no secrets
(`requires.secrets: []`).

Runtime parameters read from `config/config.yaml` (defaults in parentheses):

| Key | Meaning |
|---|---|
| `kb_path` (empty) | Path to the product/rule catalogue. Empty means the built-in synthetic seed |
| `escalate_risk_threshold` (`4`) | Risk category at or above which the case is escalated to a human regardless of verdict |
| `compliance_disclaimer` | The advisory text appended to every formatted answer |
| `max_retry` (`3`) | Framework-level retry routing |
| `timeout_seconds` (`30`) | Execution timeout |

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent is written against the platform's runtime. Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Supplied by the platform's own package registry at build time. It is not published on public PyPI, so `pip install -e .` alone does not fetch it. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework packages (`framework.*`, `shared.*`) must be importable — without them the process
fails at import, before any agent code runs. With them installed, the standalone server in
`src/api/server.py` starts without a platform connection, `GET /health` answers, and an
authenticated `/invoke` returns a real assessment against the seed catalogue, because the
pipeline needs no external service. What it will not have is a customer profile: with no gateway
to supply one, every request escalates rather than producing a verdict.

## Input

`POST /invoke` takes a JSON body with `input` — the product identifier or question — and
`session_id`. The customer profile is **not** part of the body: the server reads it from
`request.state.customer_profile`, set by authenticating middleware, and forwards it read-only in
`input_context`.

Callers must be authenticated. The standalone entry adapter starts every request at `ANONYMOUS`
unless upstream middleware stamps a verified trust level; when `INVOKE_AUTH_TOKEN` is set on the
server environment, a matching `Authorization: Bearer` header promotes the caller to
`VERIFIED_EXTERNAL`, and a non-matching one is rejected with **401**. A caller that is neither
middleware-verified nor token-authenticated is rejected with **401**; a caller whose verified
trust is below `VERIFIED_EXTERNAL` is rejected with **403**. The entry node requires
`VERIFIED_EXTERNAL` as well, so there is no anonymous path into the graph.

```bash
curl -X POST http://127.0.0.1:8000/invoke \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${INVOKE_AUTH_TOKEN}" \
  -d '{"input": "BAL-201", "session_id": "example-session"}'
```

Input fails closed with stable error codes: `EMPTY_INPUT`, `PRODUCT_NOT_FOUND` (not in the
catalogue), `S2_INJECTION_DETECTED` (a credential pattern in the request), `S3_BLOCKED` (the
output gate refused the response).

The Marketplace adapter discards caller-supplied `input_context` and disables human-in-the-loop
for the invocation, so a Marketplace caller reaches the no-profile path by construction.

## Output

```json
{
  "formatted_output": "適合性判定: Suitable（商品: BAL-201）\n...\nこの情報は参考目的のみです。...",
  "assessment": "{\"verdict\": \"Suitable\", \"product_id\": \"BAL-201\", \"risk_category\": 3, \"rationale\": \"...\", \"disclosures\": [\"...\"], \"advisory_only\": true}",
  "escalate_flag": false,
  "product_meta": "...",
  "classified_query": "...",
  "status": "success",
  "error": null,
  "error_code": null,
  "error_message": null,
  "trace_id": "...",
  "correlation_id": "...",
  "session_id": "example-session",
  "node_history": ["InitializeNode", "QueryClassificationNode", "SuitabilityRetrievalNode", "RegulatoryComplianceNode", "AnswerGenerationNode", "FinalizeNode"],
  "error_log": []
}
```

`assessment`, `product_meta` and `classified_query` are **JSON strings**, not nested objects —
state carries primitives and JSON strings only, so a checkpoint stays serialisable. Parse them at
the boundary. `escalate_flag` is the field to route on: `true` means a person must look at the
case before anything is said to a customer.

Values above are illustrative; real output depends on the catalogue behind it.

## Security and Limitations

The entry node requires verified-external trust, so an anonymous caller is stopped before any
node body runs. Input is scanned for credential patterns; the output gate enforces that the
mandatory disclosures are present and blocks a response carrying a credential pattern. Audit
events record metadata only — never the question, the customer profile, or the assessment text.
Customer personal data never enters state, so it cannot reach a checkpoint even when one is
configured.

Known limitations:

- **Advisory only, by construction.** The agent does not decide whether a sale may proceed, does
  not recommend products, and does not replace the suitability judgement a licensed person owes
  the customer.
- **The catalogue is the ceiling.** An unlisted product returns `PRODUCT_NOT_FOUND`. The shipped
  seed is synthetic; the regulatory rule text in it is a seed too, and a real deployment needs a
  version-controlled source for it — regulation changes and a stale rule cited confidently is a
  worse failure than no citation.
- **Risk tolerance is a four-value axis.** `Conservative` / `Moderate` / `Aggressive` /
  `Speculative`. A profile that does not map onto one of those does not get a verdict.
- **No profile means no verdict.** This is deliberate, and it is the most common path in any
  deployment that has not wired profile delivery through the gateway.

Output is machine-generated and assistive. It does not substitute for expert review, and any
decision that matters should be made against primary sources by a person.

## Release

- Version: 1.0.0
- Category: fixed multi-step domain workflow (Cat 2), four-node linear pipeline
- Generation mode: `deterministic` — no model call on any path
- HITL: not enabled; escalation is signalled to the caller via `escalate_flag`
- Persistence: none — no checkpointer is configured
- External API clients: none

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

The suite runs with no platform connection and no network access, against the seed catalogue.

## Documentation

`docs/02_design.md` carries the node flow, the state schema and its JSON-string convention, the
personal-data handling rule, and the two-stage compliance gate. `docs/03_test_spec.md` records the
test specification.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and test specifications
```

See `docs/02_design.md` for the design and `docs/03_test_spec.md` for the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
