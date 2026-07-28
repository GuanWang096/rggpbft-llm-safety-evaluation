from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from analyze_mj1_mj2 import (
    MODELS,
    binary_metrics,
    bootstrap_delta,
    image_clusters,
    likelihood_score,
    read_json,
    selective_metrics,
)
from run_mj3_mj4 import dynamic_trace, static_decisions, validate_and_load


TARGET_COVERAGES = (1.0, 0.95, 0.90, 0.80)
DS_MAX_ITERATIONS = 200
DS_TOLERANCE = 1e-10


def score_from_votes(matrix: list[dict[str, str]]) -> list[float]:
    return [
        sum(row[model] == "unsafe" for model in MODELS) / len(MODELS)
        for row in matrix
    ]


def class_conditional_scores(
    matrix: list[dict[str, str]],
    freeze: dict[str, Any],
) -> list[float]:
    reliability = {
        model: (
            float(freeze["class_conditional_reliability"][model]["g_unsafe"]),
            float(freeze["class_conditional_reliability"][model]["g_safe"]),
        )
        for model in MODELS
    }
    prior = float(freeze["class_prior_unsafe"])
    return [likelihood_score(row, reliability, prior) for row in matrix]


def decisions_at_coverage(
    scores: list[float],
    target_coverage: float,
) -> tuple[list[str], float, float]:
    if not 0 < target_coverage <= 1:
        raise ValueError("target coverage must be in (0, 1]")
    confidence = [abs(score - 0.5) for score in scores]
    if target_coverage == 1.0:
        threshold = min(confidence)
    else:
        limit = math.floor(target_coverage * len(scores) + 1e-12)
        candidates = sorted(set(confidence), reverse=True)
        feasible = [
            value
            for value in candidates
            if sum(item >= value for item in confidence) <= limit
        ]
        threshold = min(feasible) if feasible else float("inf")
    selected = [value >= threshold for value in confidence]
    decisions = [
        ("unsafe" if score >= 0.5 else "safe") if keep else "review"
        for score, keep in zip(scores, selected)
    ]
    return decisions, sum(selected) / len(scores), threshold


def risk_coverage(
    expected: list[str],
    scores: list[float],
) -> dict[str, Any]:
    result = {}
    for target in TARGET_COVERAGES:
        decisions, actual, threshold = decisions_at_coverage(scores, target)
        result[str(target)] = {
            "target_coverage": target,
            "actual_coverage": actual,
            "confidence_threshold": threshold,
            **selective_metrics(expected, decisions),
        }
    return result


def ds_fit(
    matrix: list[dict[str, str]],
    *,
    initial: dict[str, Any] | None = None,
) -> dict[str, Any]:
    labels = [
        {model: 1 if row[model] == "unsafe" else 0 for model in MODELS}
        for row in matrix
    ]
    if initial is None:
        posterior = [
            (sum(row.values()) + 0.5) / (len(MODELS) + 1.0)
            for row in labels
        ]
        prior = sum(posterior) / len(posterior)
        reliability = {
            model: {"g_unsafe": 0.75, "g_safe": 0.75} for model in MODELS
        }
    else:
        prior = float(initial["prior_unsafe"])
        reliability = {
            model: {
                "g_unsafe": float(initial["reliability"][model]["g_unsafe"]),
                "g_safe": float(initial["reliability"][model]["g_safe"]),
            }
            for model in MODELS
        }
        posterior = ds_apply(matrix, prior, reliability)

    converged = False
    iterations = 0
    for iterations in range(1, DS_MAX_ITERATIONS + 1):
        previous = list(posterior)
        prior = (sum(posterior) + 1.0) / (len(posterior) + 2.0)
        for model in MODELS:
            unsafe_mass = sum(posterior)
            safe_mass = len(posterior) - unsafe_mass
            true_unsafe_votes = sum(
                probability * row[model]
                for probability, row in zip(posterior, labels)
            )
            true_safe_votes = sum(
                (1.0 - probability) * (1 - row[model])
                for probability, row in zip(posterior, labels)
            )
            reliability[model]["g_unsafe"] = (
                true_unsafe_votes + 1.0
            ) / (unsafe_mass + 2.0)
            reliability[model]["g_safe"] = (
                true_safe_votes + 1.0
            ) / (safe_mass + 2.0)
        posterior = ds_apply(matrix, prior, reliability)
        if max(abs(a - b) for a, b in zip(posterior, previous)) < DS_TOLERANCE:
            converged = True
            break
    return {
        "prior_unsafe": prior,
        "reliability": reliability,
        "posterior_unsafe": posterior,
        "iterations": iterations,
        "converged": converged,
    }


def ds_apply(
    matrix: list[dict[str, str]],
    prior: float,
    reliability: dict[str, dict[str, float]],
) -> list[float]:
    mapped = {
        model: (
            max(1e-6, min(1 - 1e-6, values["g_unsafe"])),
            max(1e-6, min(1 - 1e-6, values["g_safe"])),
        )
        for model, values in reliability.items()
    }
    return [likelihood_score(row, mapped, prior) for row in matrix]


def orient_ds(
    fitted: dict[str, Any],
    expected: list[str],
) -> dict[str, Any]:
    posterior = list(fitted["posterior_unsafe"])
    direct = binary_metrics(
        expected,
        ["unsafe" if value >= 0.5 else "safe" for value in posterior],
    )["macro_f1"]
    flipped = binary_metrics(
        expected,
        ["unsafe" if value < 0.5 else "safe" for value in posterior],
    )["macro_f1"]
    if direct >= flipped:
        fitted["orientation_flipped_on_validation"] = False
        return fitted
    fitted["prior_unsafe"] = 1.0 - float(fitted["prior_unsafe"])
    fitted["reliability"] = {
        model: {
            "g_unsafe": 1.0 - float(values["g_safe"]),
            "g_safe": 1.0 - float(values["g_unsafe"]),
        }
        for model, values in fitted["reliability"].items()
    }
    fitted["posterior_unsafe"] = [1.0 - value for value in posterior]
    fitted["orientation_flipped_on_validation"] = True
    return fitted


def exact_mcnemar(
    expected: list[str],
    proposed: list[str],
    baseline: list[str],
) -> dict[str, Any]:
    proposed_only = sum(
        p == truth and b != truth
        for truth, p, b in zip(expected, proposed, baseline)
    )
    baseline_only = sum(
        p != truth and b == truth
        for truth, p, b in zip(expected, proposed, baseline)
    )
    discordant = proposed_only + baseline_only
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(
            math.comb(discordant, index)
            for index in range(min(proposed_only, baseline_only) + 1)
        ) / 2**discordant
        p_value = min(1.0, 2.0 * tail)
    return {
        "proposed_correct_baseline_wrong": proposed_only,
        "proposed_wrong_baseline_correct": baseline_only,
        "discordant_pairs": discordant,
        "exact_two_sided_p": p_value,
    }


def holm_adjust(comparisons: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        comparisons.items(),
        key=lambda item: item[1]["exact_two_sided_p"],
    )
    running = 0.0
    count = len(ordered)
    adjusted = {}
    for rank, (name, value) in enumerate(ordered):
        candidate = min(1.0, (count - rank) * value["exact_two_sided_p"])
        running = max(running, candidate)
        adjusted[name] = {**value, "holm_adjusted_p": running}
    return adjusted


def oracle_union(
    expected: list[str],
    matrix: list[dict[str, str]],
) -> dict[str, float]:
    predictions = []
    for truth, row in zip(expected, matrix):
        if any(row[model] == truth for model in MODELS):
            predictions.append(truth)
        else:
            predictions.append("safe" if truth == "unsafe" else "unsafe")
    return binary_metrics(expected, predictions)


def committee_majority(
    matrix: list[dict[str, str]],
    committee: tuple[str, ...],
) -> list[str]:
    if len(committee) % 2 == 0:
        raise ValueError("The headline majority baseline requires an odd committee")
    quorum = len(committee) // 2 + 1
    return [
        (
            "unsafe"
            if sum(row[model] == "unsafe" for model in committee) >= quorum
            else "safe"
        )
        for row in matrix
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-dir", type=Path, required=True)
    parser.add_argument("--test-manifest", type=Path, required=True)
    parser.add_argument("--validation-dir", type=Path, required=True)
    parser.add_argument("--validation-manifest", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    freeze = read_json(args.freeze)
    test_manifest = read_json(args.test_manifest)
    validation_manifest = read_json(args.validation_manifest)
    expected, matrix, test_records = validate_and_load(
        args.test_dir,
        test_manifest,
        freeze,
    )
    validation_expected, validation_matrix, _ = validate_and_load(
        args.validation_dir,
        validation_manifest,
        freeze,
    )

    validation_ds = orient_ds(
        ds_fit(validation_matrix),
        validation_expected,
    )
    ds_validation_scores = ds_apply(
        matrix,
        float(validation_ds["prior_unsafe"]),
        validation_ds["reliability"],
    )
    transductive_ds = ds_fit(matrix, initial=validation_ds)
    ds_transductive_scores = list(transductive_ds["posterior_unsafe"])

    scalar = dynamic_trace(
        expected,
        matrix,
        freeze,
        mode="scalar",
        feedback_scope="all",
    )
    cumulative = dynamic_trace(
        expected,
        matrix,
        freeze,
        decay=1.0,
        feedback_scope="all",
    )
    decay = dynamic_trace(
        expected,
        matrix,
        freeze,
        feedback_scope="all",
    )
    score_methods = {
        "unweighted_vote": score_from_votes(matrix),
        "class_conditional_static": class_conditional_scores(matrix, freeze),
        "dawid_skene_validation_fitted": ds_validation_scores,
        "dawid_skene_test_transductive": ds_transductive_scores,
        "scalar_beta_decay": scalar["scores"],
        "class_conditional_cumulative": cumulative["scores"],
        "class_conditional_decay": decay["scores"],
    }
    curves = {
        method: risk_coverage(expected, scores)
        for method, scores in score_methods.items()
    }

    proposed_forced = static_decisions(matrix, freeze, review=False)
    best_single_name = str(freeze["best_single_judge"])
    best_single = [
        str(record["decision"]["label"])
        for record in test_records[best_single_name]
    ]
    majority_forced = [
        "unsafe" if score >= 0.5 else "safe"
        for score in score_methods["unweighted_vote"]
    ]
    ds_validation_forced = [
        "unsafe" if score >= 0.5 else "safe"
        for score in ds_validation_scores
    ]
    mcnemar = holm_adjust(
        {
            f"versus_best_single_{best_single_name}": exact_mcnemar(
                expected,
                proposed_forced,
                best_single,
            ),
            "versus_unweighted_vote": exact_mcnemar(
                expected,
                proposed_forced,
                majority_forced,
            ),
            "versus_dawid_skene_validation_fitted": exact_mcnemar(
                expected,
                proposed_forced,
                ds_validation_forced,
            ),
        }
    )
    bootstrap = bootstrap_delta(
        expected,
        proposed_forced,
        best_single,
        image_clusters(test_manifest["entries"]),
    )
    headline_committee = ("qwen", "safework", "minicpm")
    headline_proposed = static_decisions(
        matrix,
        freeze,
        committee=headline_committee,
        review=False,
    )
    headline_majority = committee_majority(matrix, headline_committee)
    headline_bootstrap = bootstrap_delta(
        expected,
        headline_proposed,
        headline_majority,
        image_clusters(test_manifest["entries"]),
    )
    headline_mcnemar = exact_mcnemar(
        expected,
        headline_proposed,
        headline_majority,
    )
    headline_proposed_metrics = binary_metrics(expected, headline_proposed)
    headline_majority_metrics = binary_metrics(expected, headline_majority)

    result = {
        "schema": "mj2-extended-analysis-v1",
        "status": "secondary_and_diagnostic",
        "sample_count": len(expected),
        "target_coverages": list(TARGET_COVERAGES),
        "dawid_skene": {
            "validation_fitted": {
                key: value
                for key, value in validation_ds.items()
                if key != "posterior_unsafe"
            },
            "test_transductive": {
                key: value
                for key, value in transductive_ds.items()
                if key != "posterior_unsafe"
            },
            "warning": (
                "The test-transductive variant uses the unlabeled test response "
                "matrix during EM fitting and is not a deployable inductive baseline."
            ),
        },
        "risk_coverage": curves,
        "oracle_union": oracle_union(expected, matrix),
        "full_coverage_comparison": {
            "proposed": "class_conditional_static_forced",
            "baseline": f"validation_selected_{best_single_name}",
            "proposed_metrics": binary_metrics(expected, proposed_forced),
            "baseline_metrics": binary_metrics(expected, best_single),
            "cluster_bootstrap": bootstrap,
            "mcnemar_holm_family": mcnemar,
        },
        "headline_same_committee_comparison": {
            "status": "revised_v15_primary_candidate",
            "committee": list(headline_committee),
            "sample_count": len(expected),
            "automatic_coverage": 1.0,
            "baseline": "unweighted_2_of_3_majority",
            "proposed": "class_conditional_reliability_likelihood",
            "baseline_metrics": headline_majority_metrics,
            "proposed_metrics": headline_proposed_metrics,
            "proposed_minus_baseline": {
                metric: (
                    headline_proposed_metrics[metric]
                    - headline_majority_metrics[metric]
                )
                for metric in (
                    "accuracy",
                    "balanced_accuracy",
                    "macro_f1",
                    "mcc",
                    "unsafe_recall",
                    "safe_specificity",
                    "false_safe_rate",
                )
            },
            "cluster_bootstrap": headline_bootstrap,
            "mcnemar": headline_mcnemar,
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "mj2_extended_results.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output_path.resolve()),
                "schema": result["schema"],
                "method_count": len(score_methods),
                "target_coverage_count": len(TARGET_COVERAGES),
                "ds_validation_converged": validation_ds["converged"],
                "ds_transductive_converged": transductive_ds["converged"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
