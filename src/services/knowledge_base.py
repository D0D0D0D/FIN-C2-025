"""KnowledgeBase — 商品 KB + 金商法 §40 ルール KB（FIN-C2-025）。

docs/02_design.md §3/§6。テンプレは **スキーマ定義 ＋ 小さなシード（合成・商品 6 件）** のみ同梱する。
実商品カタログは顧客のデプロイ時データ（`kb_path` で差し込む）であり、本ファイルには含めない。

検索はシード KB に対する **決定論的ルックアップ**（product_id 完全一致 + 名称部分一致）。
本番では `from_path()` で顧客カタログを読み込み、必要に応じてベクタ/ハイブリッド検索へ差し替える
（テスト可能性のため標準テンプレは決定論実装）。

適合性判定はリスク許容度 × 商品リスク区分(1-5) のマトリクス（`SUITABILITY_MATRIX`）で行う。
顧客プロファイル（リスク許容度）は State でなく invocation context 経由で渡る（PII 保護）。
"""

from __future__ import annotations

import json
from typing import Optional

# リスク許容度 → リスク区分(1-5) ごとの判定。S=Suitable / R=Requires Review / N=Not Suitable。
SUITABILITY_MATRIX: dict[str, dict[int, str]] = {
    "Conservative": {1: "Suitable", 2: "Suitable", 3: "Requires Review", 4: "Not Suitable", 5: "Not Suitable"},
    "Moderate":     {1: "Suitable", 2: "Suitable", 3: "Suitable", 4: "Requires Review", 5: "Not Suitable"},
    "Aggressive":   {1: "Suitable", 2: "Suitable", 3: "Suitable", 4: "Suitable", 5: "Requires Review"},
    "Speculative":  {1: "Suitable", 2: "Suitable", 3: "Suitable", 4: "Suitable", 5: "Suitable"},
}

# 金商法 §40（適合性の原則）ルール KB（シード）。実運用は規制テキストの版管理 KB を参照。
SEED_RULES: list[dict] = [
    {"rule_id": "FIEA-40-1", "title": "適合性の原則",
     "text": "金融商品取引業者は、顧客の知識・経験・財産の状況・契約目的に照らして不適当な勧誘を行ってはならない（金商法第40条第1号）。"},
    {"rule_id": "FIEA-40-2", "title": "業務運営状況",
     "text": "顧客の利益に資する業務運営体制を整備しなければならない（金商法第40条第2号）。"},
    {"rule_id": "DISCLOSURE", "title": "リスク開示義務",
     "text": "元本割れリスク・手数料・流動性リスク等の重要事項を説明しなければならない。"},
]

# 商品 KB シード（合成・6 件）。risk_category: 1(低)〜5(高)。
SEED_PRODUCTS: list[dict] = [
    {"product_id": "MMF-001", "name": "国内MMF", "risk_category": 1,
     "suitable_for": ["Conservative", "Moderate", "Aggressive", "Speculative"], "unsuitable_for": [],
     "regulatory_notes": "元本割れリスク低。流動性高。"},
    {"product_id": "BOND-101", "name": "国内債券ファンド", "risk_category": 2,
     "suitable_for": ["Conservative", "Moderate", "Aggressive", "Speculative"], "unsuitable_for": [],
     "regulatory_notes": "金利変動リスクあり。"},
    {"product_id": "BAL-201", "name": "バランス型投資信託", "risk_category": 3,
     "suitable_for": ["Moderate", "Aggressive", "Speculative"], "unsuitable_for": [],
     "regulatory_notes": "株式・債券混合。中リスク。"},
    {"product_id": "EQ-301", "name": "国内株式ファンド", "risk_category": 4,
     "suitable_for": ["Aggressive", "Speculative"], "unsuitable_for": ["Conservative"],
     "regulatory_notes": "価格変動リスク大。"},
    {"product_id": "EM-401", "name": "新興国株式ファンド", "risk_category": 5,
     "suitable_for": ["Speculative"], "unsuitable_for": ["Conservative", "Moderate"],
     "regulatory_notes": "為替・カントリーリスク大。元本毀損可能性高。"},
    {"product_id": "DERIV-501", "name": "仕組債（参照型）", "risk_category": 5,
     "suitable_for": ["Speculative"], "unsuitable_for": ["Conservative", "Moderate", "Aggressive"],
     "regulatory_notes": "複雑な仕組み。早期償還・元本毀損リスク。適合性確認を厳格に。"},
]

RISK_TOLERANCES = ["Conservative", "Moderate", "Aggressive", "Speculative"]


class KnowledgeBase:
    """商品 KB + 金商法ルール KB。シード固定 or 顧客カタログ（kb_path）から構築。"""

    def __init__(self, products: Optional[list[dict]] = None, rules: Optional[list[dict]] = None) -> None:
        self._products = products if products is not None else list(SEED_PRODUCTS)
        self._rules = rules if rules is not None else list(SEED_RULES)
        self._by_id = {p["product_id"]: p for p in self._products}

    @classmethod
    def from_path(cls, kb_path: Optional[str]) -> "KnowledgeBase":
        """顧客カタログ（JSON: {"products":[...], "rules":[...]}）を読む。未指定ならシード。"""
        if not kb_path:
            return cls()
        try:
            with open(kb_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return cls()
        return cls(products=data.get("products"), rules=data.get("rules"))

    def get_product(self, product_id: str) -> Optional[dict]:
        return self._by_id.get(product_id)

    def find_product(self, query: str) -> Optional[dict]:
        """product_id 完全一致 → 名称部分一致の順に最初の 1 件を返す（決定論）。"""
        q = (query or "").strip()
        if not q:
            return None
        if q in self._by_id:
            return self._by_id[q]
        for p in self._products:
            if q in p["product_id"] or q in p["name"]:
                return p
        return None

    def rules(self) -> list[dict]:
        return list(self._rules)


def assess_suitability(risk_tolerance: str, risk_category: int) -> str:
    """リスク許容度 × 商品リスク区分 → Suitable / Requires Review / Not Suitable。

    未知のリスク許容度は安全側に倒し Requires Review（人間確認）とする。
    """
    row = SUITABILITY_MATRIX.get(risk_tolerance)
    if row is None:
        return "Requires Review"
    return row.get(int(risk_category), "Requires Review")
