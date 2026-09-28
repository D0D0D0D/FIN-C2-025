# FIN-C2-025 — Test Specification

対象: InvestmentSuitabilityQAAgent（Cat 2 / FIN・VectorRAG・L1 直接継承）。
framework パッケージ未公開のため `tests/conftest.py` の framework.* スタブで実行する
（公開後は実 framework に切替。STG invoke が実環境検証）。

## Test Strategy
- Test types: Unit / Integration / Proof-of-Boundary
- 合計 19 件（Unit 9 + Integration 4 + PB 4 + scaffold PB 2）。全件 PASS がテスト → STG 検証の移行条件。

## Unit Tests（TC） — `tests/unit/test_nodes.py`

| TC-ID | 対象ノード | シナリオ | 期待 |
|-------|-----------|---------|------|
| TC-01 | QueryClassification | 空入力 | `EMPTY_INPUT` で early-break |
| TC-02 | QueryClassification | 正常入力 | `classified_query`(JSON) 生成 |
| TC-03 | SuitabilityRetrieval | KB ヒット（MMF-001） | `product_meta` / `retrieved_rules` 生成 |
| TC-04 | SuitabilityRetrieval | KB 未ヒット | `PRODUCT_NOT_FOUND` |
| TC-05 | RegulatoryCompliance | Conservative × risk1 | verdict=Suitable・非エスカレ |
| TC-06 | RegulatoryCompliance | Conservative × risk5 | verdict=Not Suitable・escalate=True・advisory_only |
| TC-07 | RegulatoryCompliance | プロファイル欠落 | verdict=Requires Review・escalate=True |
| TC-08 | AnswerGeneration | 通常 | 免責文を必ず含む |
| TC-09 | AnswerGeneration | escalate_flag | 人間引き継ぎ注記 + 免責 |

## Integration Tests — `tests/integration/test_agent.py`

| IT-ID | シナリオ | 期待 |
|-------|---------|------|
| IT-01 | 正常 invoke（Moderate × BAL-201） | verdict=Suitable・免責あり・error なし |
| IT-02 | 入力に credential | `S2_INJECTION_DETECTED`・出力 None（S-2） |
| IT-03 | InvocationContext 不在 | `SecurityViolationError`（S-1 default-deny） |
| IT-04 | trace emitter 注入 | メタのみ emit（PII・本文を含まない・S-4） |

## Proof-of-Boundary Tests（必須） — `tests/proof_of_boundary/`

| PB-ID | 境界 | 期待 | ファイル |
|-------|------|------|---------|
| PB-S1 | S-1 トラスト | 未認証/ANONYMOUS のノード呼び出しは `SecurityViolationError` | test_pb_compliance.py |
| PB-S2 | 顧客 PII 分離 | 年齢・顧客 ID が State / 出力に現れない | test_pb_compliance.py |
| PB-S3 | S-3 出力ゲート | 成功応答は必ず免責を含む / credential はブロック | test_pb_compliance.py |
| PB-ESC | エスカレーション | risk_category ≥ threshold で escalate_flag=True | test_pb_compliance.py |
| PB-IMPORT | Import 分離 | src/ に Level 0 (agenticstar/platform) import なし | test_import_isolation.py |
| PB-STATE | State 安全 | State に credential フィールド / 禁止型なし | test_state_safety.py |

## 実行

```bash
python -m pytest tests/ -v
python -m pytest tests/proof_of_boundary/ -v
```

## 既知の限界
- framework スタブ依存のため「実 framework との契約一致」は STG invoke で検証する。
- retrieval は決定論シードルックアップ。実カタログ・ベクタ/ハイブリッド検索は本番で差し替え。
