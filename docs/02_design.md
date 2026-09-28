# FIN-C2-025 InvestmentSuitabilityQAAgent — 設計仕様書

## 1. 概要

顧客のリスクプロファイルに対する投資商品の**適合性**を、商品 KB と金融商品取引法 §40
（適合性の原則）の規制 KB に基づいて判定する Cat 2 ドメインワークフローエージェント。
出力は **Suitable / Requires Review / Not Suitable** の構造化判定 ＋ 必須の規制ディスクロージャー。

> **設計原則（compliance-critical）:** 本エージェントは金商法 §40（KYC / 適合性）の領域で動作する。
> 出力は **「参照情報（reference）」に限定**し、「推奨（recommendation）」「勧誘」には決して踏み込まない。
> 免責文（ディスクロージャー）は任意ではなく**金商法準拠のため必須**であり、S-3 出力ゲートで
> 全応答に強制注入する。高リスク商品は自動的に人間担当者へエスカレーションする。

### 対象スコープ

| 区分 | 内容 |
|---|---|
| ✅ 対応 | 商品 KB ＋ 金商法ルール KB に基づく適合性照会への**参照回答**（区分判定 + 根拠 + ディスクロージャー）。顧客リスク許容度 × 商品リスク区分の突合 |
| ❌ 対象外 | 特定商品の推奨・勧誘 / 顧客への直接の投資助言 / 売買執行 / ポートフォリオ提案 / 顧客プロファイルの永続保存。これらは人間担当者の責務 or 別テンプレ |

---

## 2. レイヤーアーキテクチャ

> **2026-05-18 ポリシー:** 旧 **Level 2 → Level 3** 継承モデルは撤廃。本テンプレートは
> **L1 フレームワーク（`framework/`）を直接継承**する。下記の「Level 2 / VectorRAGAgent」表記は
> 概念パターンの参照であり、インポート上の継承先ではない。

```
L1: framework/                         ← 直接継承（AgentBaseGraph / BaseNode / LLMClient / TrustLevel）
FIN-C2-025                             ← 本テンプレート（L1 を直接継承）
```

- **L1 Base**: `AgentBaseGraph`
- **概念パターン参照**: `VectorRAGAgent`（検索 → 生成）。Cat 2 として業種 KB / config を上乗せ。
- 継承構造: `InvestmentSuitabilityQAAgent(InvestmentSuitabilityQAGraph)` /
  `InvestmentSuitabilityQAGraph(AgentBaseGraph)`。独自 `run()` / `self.graph` 合成は不可。
- エントリアダプタは認証済み HTTP 経路（`src/api/server.py`）と AGENTIC STAR Marketplace 経路（`cli.py`）の 2 本。

```python
# src/graph/graph.py — グラフ定義（ノード登録 + エッジ配線）
class InvestmentSuitabilityQAGraph(AgentBaseGraph):
    def register_nodes(self): ...   # query_classification → suitability_retrieval
                                     #   → regulatory_compliance → answer_generation
    def add_edges(self): ...

# src/agent.py — Agent は Graph を継承し、S-2〜S-4 のセキュリティフックを実装。
# S-1（トラストレベル強制）は ADR-006 で __pre_invoke__ を廃止済み。
# 各ノードが required_trust_level を宣言し、BaseNode.__call__() が自動強制する
# （Agent 側で __pre_invoke__ を実装してはいけない＝dead code）。
# S-2/S-3 フックの正確なメソッド名・シグネチャは framework の 5 層セキュリティ契約に従う。
class InvestmentSuitabilityQAAgent(InvestmentSuitabilityQAGraph):
    # S-1: 各ノードで required_trust_level = TrustLevel.VERIFIED_EXTERNAL を宣言（認証済み窓口のみ・自動強制）
    def _security_gate_input(self, state, config=None): ...  # S-2（顧客 PII 保護）
    def _security_gate_output(self, state, config=None): ... # S-3（推奨化ブロック + 免責強制）
    def _emit_trace_event(self, state, config=None): ...     # S-4（金商法 記録保存）
```

---

## 3. ノードフロー

固定 4 ノード線形パイプライン:

```
query_classification → suitability_retrieval → regulatory_compliance → answer_generation
```

### Node Configuration

| Node | 責務 | Input State | Output State | Inherits |
|------|------|-------------|--------------|----------|
| initialize | schema_version / session_id / trust_level | — | — | InitializeNode (default) |
| query_classification | 入力検証（S-1/S-2）+ 照会意図・対象商品の抽出 | `user_input` | `classified_query` | BaseNode |
| suitability_retrieval | 商品 KB + 金商法 §40 ルール KB の hybrid 検索 | `classified_query` | `retrieved_rules`, `product_meta` | BaseNode |
| regulatory_compliance | 適合性突合 → 区分判定 + ディスクロージャー + エスカレーション判定 | `retrieved_rules`, `product_meta` | `assessment`, `escalate_flag` | BaseNode |
| answer_generation | 構造化 + 自然文化 + S-3 出力ゲート + S-4 監査 | `assessment` | `formatted_output` | BaseNode |
| finalize | response_metadata / total_time_ms | — | — | FinalizeNode (default) |

### Data Flow

```
START → initialize → query_classification → suitability_retrieval
        → regulatory_compliance → answer_generation → finalize → END
```

---

## 4. State 定義

> **flat TypedDict・プリミティブのみ**（msgpack 互換）。Pydantic / dataclass / 任意 Python オブジェクト禁止。
> **構造化データ（複数項目）は `dict` / `list[dict]` を State に直接置かず、`json.dumps()` で `str` 化して格納する**
> （ノード境界で `json.loads()` して使う）。
> **顧客プロファイル（年齢・投資経験・リスク許容度）は State に入れず、`config["configurable"]` の
> invocation context で渡す**（金商法の顧客データ保護 + checkpoint 漏洩防止 + ADR ルール）。

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| `user_input` | str | 適合性照会の自然文 | ✅（AgentState 継承） |
| `classified_query` | str | 照会意図・対象 product_id（JSON-serialized dict） | |
| `retrieved_rules` | str | 金商法 §40 ルール + 商品リスク区分の検索結果（JSON-serialized list[dict]） | |
| `product_meta` | str | product_id / risk_category(1-5) / suitable_for / unsuitable_for（JSON-serialized dict） | |
| `assessment` | str | 判定区分 + 根拠 + 必須ディスクロージャー（JSON-serialized dict） | |
| `escalate_flag` | bool | 高リスク商品の人間エスカレーション要否 | |
| `formatted_output` | str | 最終応答（構造化 + 自然文 + 免責） | |

**State 制約（必須）:**
- 顧客 PII（年齢・リスク許容度・顧客 ID）を State に保存しない（invocation context 経由）
- JWT / API キー / credential を State に入れない
- InvocationContext は `config["configurable"]` 経由のみ

---

## 5. セキュリティ設計（5 層）

| 層 | 実装 | 本テンプレでの内容 |
|---|---|---|
| S-1 | 各ノードの `required_trust_level` 宣言（`BaseNode.__call__()` が自動強制。ADR-006 で `__pre_invoke__` 廃止） | `VERIFIED_EXTERNAL` — 認証済み窓口システムからのみ呼び出し可。顧客プロファイル不在時は default-deny |
| S-2 | `_security_gate_input()` | 入力 PII 検出ゲート。顧客プロファイルがログ・State に漏れないことを保証 |
| S-3 | `_security_gate_output()` | **MANDATORY**。推奨化（"この商品を買うべき"）をブロックし、`compliance_disclaimer` を全応答に強制注入。免責なし応答は出力しない |
| S-4 | `_emit_trace_event()` | **MANDATORY**。全適合性照会を監査ログ化（timestamp / staff ID / 擬似化顧客 ID / 照会商品）— 金商法の記録保存義務 |
| S-5 | `__init_subclass__` | import 時の JWT / credential パターン検出（framework 自動） |

> **S-1 拒否時の挙動（明記）**: トラスト不足・InvocationContext 不在（= default-deny）の場合、
> ノードの `BaseNode.__call__()` は **`SecurityViolationError` を raise** する（fail-closed。state に
> `error_code` を立てて継続する方式ではない）。実装・テスト（`test_pb_s1_*` / `test_s1_*`）はこの
> raise 挙動を前提とする。
> S-2/S-3（入力/出力ゲート）は raise ではなく `error` / `error_code` を state に立てて早期終了する。

**ComplianceGate（regulatory_compliance ノード + S-3 の二段防御）**
- 「reference」と「recommendation」の境界を守る最後の砦。
- `risk_category >= escalate_risk_threshold`（既定 4）の商品は `escalate_flag=True` → 人間担当者へ。
- 出力に必ず `compliance_disclaimer`（"投資判断は必ず担当アドバイザーにご相談ください。元本割れリスクがあります。"）を含める。

**適合性判定マトリクス**（`src/services/knowledge_base.py` `SUITABILITY_MATRIX` / `assess_suitability()`）

リスク許容度 × 商品リスク区分(1-5) → 判定（S=Suitable / R=Requires Review / N=Not Suitable）。
未知のリスク許容度・プロファイル欠落は安全側に倒し **Requires Review + エスカレーション**。

| リスク許容度 \ 区分 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| Conservative | S | S | R | N | N |
| Moderate | S | S | S | R | N |
| Aggressive | S | S | S | S | R |
| Speculative | S | S | S | S | S |

> マトリクスはシード（合成）であり、実運用では金融機関の社内適合性基準を config / KB で上書きする。

---

## 6. Config Surface（config/agent.yaml 上書き）

```yaml
kb_path: data/products_kb/
suitability_rules_path: data/kisho_ho_section40_rules.yaml
risk_categories: [Conservative, Moderate, Aggressive, Speculative]
escalate_risk_threshold: 4          # risk_category >= 4 → 人間エスカレーション
hybrid_search: true                 # 商品コード・ID を含むため
compliance_disclaimer: |
  この情報は参考目的のみです。投資判断は必ず担当アドバイザーにご相談ください。
  金融商品の購入は元本割れリスクがあります。
```

### KB の作り込み範囲（テンプレ vs 顧客デプロイ）

| KB | 中身 | 作成責任 | テンプレ同梱範囲 |
|---|---|---|---|
| **金商法 §40 ルール KB** | 適合性原則の規制テキスト | 我々（FSA Suite の姉妹テンプレートで共有） | 共有 KB を参照（suite で 1 回構築） |
| **商品 KB** | 商品カタログ・`risk_category` ・`suitable_for[]` ・`unsuitable_for[]` ・`regulatory_notes` | **顧客（金融機関ごとに異なる）** | **スキーマ定義＋小さなシード（合成・商品 5〜10 件）のみ** |

- テンプレが提供するもの：**KB スキーマ/フォーマットの定義**（顧客が何を入れるかの契約）、**テスト & STG invoke デモが回る最小シード KB**、`kb_path` での実カタログ差し込み手順。
- テンプレ対象外：**実商品カタログ全件・金商法の網羅全文**（顧客のデプロイ時データ整備）。各機関で商品が異なるためテンプレに固定しない。

---

## 7. Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | 固定パイプラインの RAG ワークフロー。自律ループ不要 |
| パターン | VectorRAG（検索→判定） | DocGen | **VectorRAG** | KB grounded Q&A。VectorRAGAgent 概念参照 |
| 顧客プロファイル | State 格納 | invocation context | **invocation context** | 金商法の顧客データ保護 + checkpoint 漏洩防止 |
| コンプラ境界 | プロンプトのみ | 専用ノード + S-3 二段 | **ノード + S-3** | compliance-critical のため reference と recommendation の境界を多層で担保 |
| 高リスク商品 | そのまま回答 | 自動エスカレーション | **エスカレーション** | 金商法 + ミスセリング防止 |

---

## 8. Definition of Done（テストフェーズ以降）

- [ ] `src/`（state / 4 nodes / graph / agent）+ `config/agent.yaml`（適合性設定）
- [ ] TC 8 件以上（適合 / 不適合 / 高リスクエスカレーション / 免責検証 / リスクプロファイル欠落）
- [ ] PB 4 件以上（免責なし advice ブロック / 高リスク未エスカレ ブロック / State 内 PII 拒否 / 未認証拒否）
- [ ] S-3（免責強制）+ S-4（監査ログ）を PB で検証
- [ ] CI pipeline green

> ⚠️ **compliance-critical 案件のため、S-3（出力ゲート）+ S-4（監査ログ）は実装レビューで重点確認する。**
