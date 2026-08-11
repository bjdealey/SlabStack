"""Run the server's decision engine over the parity cases and print JSON.

Paired with ``demo.ts``, which runs the same cases through the browser port.
``compare.py`` diffs the two. See README.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "backend"))

from app.money import allocate  # noqa: E402
from app.services import decision  # noqa: E402


def label_for(company_code: str, grade: float) -> str:
    return f"{company_code} {grade:g}"


def run(case: dict) -> dict:
    thresholds = decision.Thresholds.from_settings(case["settings"])
    inputs = decision.DecisionInputs(
        raw_net_minor=case["inputs"]["rawNetMinor"],
        raw_value_minor=case["inputs"]["rawValueMinor"],
        liquidity_score=case["inputs"]["liquidityScore"],
        trend_direction=case["inputs"]["trendDirection"],
        trend_confidence=case["inputs"]["trendConfidence"],
        market_confidence=case["inputs"]["marketConfidence"],
        grade_confidence=case["inputs"]["gradeConfidence"],
        sales_by_label=case["inputs"]["salesByLabel"],
        market_recognition=case["inputs"]["marketRecognition"],
    )
    route = decision.evaluate_route(
        company_id="c1",
        company_code=case["companyCode"],
        tier_id="t1",
        tier_name="Economy",
        cost_minor=case["costMinor"],
        probabilities={float(grade): value for grade, value in case["probabilities"].items()},
        net_by_label=case["netByLabel"],
        gross_by_label=case["grossByLabel"],
        label_for=label_for,
        inputs=inputs,
        thresholds=thresholds,
        batch_size=case["batchSize"],
    )
    result = decision.decide(
        [route], inputs=inputs, thresholds=thresholds, batch_size=case["batchSize"]
    )
    return {
        "name": case["name"],
        "decision": result.decision,
        "confidence": result.confidence,
        "headline": result.headline,
        "reasons": result.reasons,
        "blockers": result.blockers,
        "review_in_days": result.review_in_days,
        "route": {
            "expected_net_minor": route.expected_net_minor,
            "expected_profit_minor": route.expected_profit_minor,
            "roi_pct": route.roi_pct,
            "probability_of_profit": route.probability_of_profit,
            "probability_of_target": route.probability_of_target,
            "minimum_profitable_grade": route.minimum_profitable_grade,
            "probability_at_or_above_minimum": route.probability_at_or_above_minimum,
            "downside_minor": route.downside_minor,
            "upside_minor": route.upside_minor,
            "coverage": route.coverage,
            "slab_liquidity": route.slab_liquidity,
            "slab_sales": route.slab_sales,
            "opportunity_score": route.opportunity_score,
            "score_parts": route.score_parts,
            "confidence": route.confidence,
            "notes": route.notes,
            "rows": [
                {
                    "grade": item.grade,
                    "label": item.label,
                    "probability": item.probability,
                    "gross_minor": item.gross_minor,
                    "net_minor": item.net_minor,
                    "profit_minor": item.profit_minor,
                }
                for item in route.distribution.outcomes
            ],
        },
    }


def run_allocation(case: dict) -> dict:
    """The split itself, where a penny is easiest to lose."""
    parts = allocate(case["totalMinor"], case["weights"])
    return {
        "name": case["name"],
        "parts": parts,
        "sum": sum(parts),
        "exact": sum(parts) == case["totalMinor"],
    }


if __name__ == "__main__":
    data = json.loads((HERE / "cases.json").read_text())
    print(
        json.dumps(
            {
                "decisions": [run(case) for case in data["cases"]],
                "allocations": [run_allocation(case) for case in data["allocations"]],
            },
            indent=2,
        )
    )
