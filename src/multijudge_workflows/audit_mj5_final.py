from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def audit_commit(
    evidence: dict[str, Any] | None,
    tx_ids: set[str],
    location: str,
) -> None:
    require(evidence is not None, f"{location}: missing commit evidence")
    require(
        evidence["validation_status"] == "VALID",
        f"{location}: non-VALID transaction",
    )
    require(
        len(evidence["endorsing_peers"]) == 2,
        f"{location}: expected two peer confirmations",
    )
    tx_id = evidence["tx_id"]
    require(tx_id not in tx_ids, f"{location}: duplicate transaction ID")
    tx_ids.add(tx_id)


def audit(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.resolve()
    repo_root = args.repo_root.resolve()
    aggregate = read_json(session / "aggregate.json")
    provenance = read_json(session / "provenance.json")
    runtime_snapshot = read_json(
        session / "runtime_source_snapshot.json"
    )
    label_provenance = read_json(session / "label_provenance.json")
    manifest = read_json(session / "session_manifest.json")
    require(aggregate["all_entries_complete"], "aggregate is incomplete")
    require(
        all(
            "sequential_component_total" not in entry
            and entry["decision_joined_component_total"][
                "composition_method"
            ].startswith("exact decision_id join")
            for entry in aggregate["entries"]
        ),
        "aggregate contains an approximate composed-latency field",
    )
    require(len(manifest["entries"]) == 18, "formal matrix is not 18 entries")
    require(
        "Version: 4.1, Sequence: 5"
        in provenance["runtime"]["fabric_chaincode_definition"],
        "unexpected Fabric chaincode definition",
    )
    require(
        provenance["runtime"]["fabric_chaincode_package_id"].startswith(
            "mj5_4.1:"
        ),
        "unexpected Fabric package ID",
    )

    for section in ("source", "workload"):
        recorded = provenance[section]["files"]
        for relative, expected in recorded.items():
            path = repo_root / relative
            require(path.is_file(), f"{section}: missing {relative}")
            actual = sha256_file(path)
            if actual != expected and section == "source":
                archived = runtime_snapshot["files"].get(relative)
                require(
                    archived is not None
                    and archived["sha256"] == expected
                    and sha256_file(
                        session / archived["snapshot_path"]
                    )
                    == expected,
                    f"{section}: hash mismatch without snapshot {relative}",
                )
            else:
                require(
                    actual == expected,
                    f"{section}: hash mismatch {relative}",
                )
        encoded = json.dumps(
            recorded, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        require(
            hashlib.sha256(encoded).hexdigest()
            == provenance[section]["manifest_sha256"],
            f"{section}: manifest hash mismatch",
        )

    tx_ids: set[str] = set()
    totals = {
        "stage_a_rows": 0,
        "stage_c_rows": 0,
        "protocol_certificates": 0,
        "driver_results": 0,
        "fabric_transactions": 0,
    }
    for entry in manifest["entries"]:
        run_id = entry["run_id"]
        run_dir = session / "runs" / run_id
        stage_a = read_jsonl(run_dir / "stage_a.jsonl")
        stage_c = read_jsonl(run_dir / "stage_c.jsonl")
        certificates = read_json(run_dir / "protocol_certificates.json")
        events = read_jsonl(run_dir / "rgg/events.jsonl")
        summary = read_json(run_dir / "rgg/summary.json")
        require(
            len(stage_a) == len(stage_c) == len(certificates) == 96,
            f"{run_id}: expected 96 aligned decisions",
        )
        a_by_id = {row["decision_id"]: row for row in stage_a}
        c_by_id = {row["decision_id"]: row for row in stage_c}
        cert_by_id = {
            certificate["decision_id"]: certificate
            for certificate in certificates
        }
        require(
            set(a_by_id) == set(c_by_id) == set(cert_by_id),
            f"{run_id}: decision join mismatch",
        )

        driver_results = [
            event
            for event in events
            if event.get("type") == "DRIVER_RESULT"
        ]
        require(
            len(driver_results) == 96,
            f"{run_id}: missing DRIVER_RESULT events",
        )
        driver_by_id = {
            event["data"]["decision_id"]: event["data"]
            for event in driver_results
        }
        require(
            set(driver_by_id) == set(a_by_id),
            f"{run_id}: driver decision join mismatch",
        )
        for decision_id in sorted(a_by_id):
            row_a = a_by_id[decision_id]
            row_c = c_by_id[decision_id]
            certificate = cert_by_id[decision_id]
            driver = driver_by_id[decision_id]
            encoded_certificate = json.dumps(
                certificate["protocol_certificate"],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            protocol_hash = hashlib.sha256(
                encoded_certificate
            ).hexdigest()
            require(
                protocol_hash
                == certificate["protocol_certificate_sha256"]
                == row_c["protocol_certificate_sha256"],
                f"{decision_id}: protocol certificate hash mismatch",
            )
            require(
                driver["success"]
                and driver["observed_commit_count"] == 16
                and driver["required_commit_count"] == 16,
                f"{decision_id}: incomplete RGG commit",
            )
            require(
                driver["latency_ms"]
                == driver["required_commit_latency_ms"],
                f"{decision_id}: primary latency is not completion latency",
            )
            require(
                driver["first_commit_latency_ms"]
                <= driver["required_commit_latency_ms"],
                f"{decision_id}: impossible commit timing",
            )
            audit_commit(
                row_a["fabric_commits"]["freeze"],
                tx_ids,
                f"{decision_id}:freeze",
            )
            for index, evidence in enumerate(
                row_a["fabric_commits"]["committee_votes"]
            ):
                audit_commit(
                    evidence,
                    tx_ids,
                    f"{decision_id}:vote:{index}",
                )
            audit_commit(
                row_c["fabric_commits"]["certify"],
                tx_ids,
                f"{decision_id}:certify",
            )
            audit_commit(
                row_c["fabric_commits"]["settle"],
                tx_ids,
                f"{decision_id}:settle",
            )
        require(
            summary["driver_failure_count"] == 0
            and summary["conflicting_commit_count"] == 0
            and summary["safety_violation_events"] == 0
            and summary["node_commit_completeness"] == 1.0,
            f"{run_id}: RGG safety gate failed",
        )
        totals["stage_a_rows"] += len(stage_a)
        totals["stage_c_rows"] += len(stage_c)
        totals["protocol_certificates"] += len(certificates)
        totals["driver_results"] += len(driver_results)

    totals["fabric_transactions"] = len(tx_ids)
    require(
        totals
        == {
            "stage_a_rows": 1728,
            "stage_c_rows": 1728,
            "protocol_certificates": 1728,
            "driver_results": 1728,
            "fabric_transactions": 8640,
        },
        f"unexpected totals: {totals}",
    )
    require(
        all(
            int(entry["settlement_mvcc_retries"]) == 0
            for run_dir in (session / "runs").iterdir()
            if run_dir.is_dir()
            for entry in read_jsonl(run_dir / "stage_c.jsonl")
        ),
        "nonzero MVCC retry found",
    )
    package = runtime_snapshot["chaincode_package"]
    require(
        package["fabric_package_id"]
        == provenance["runtime"]["fabric_chaincode_package_id"]
        and sha256_file(session / package["path"])
        == package["sha256"]
        and package["fabric_package_id"].endswith(package["sha256"]),
        "archived chaincode package does not match the Fabric package ID",
    )
    require(
        label_provenance["record_count"] == 96
        and len(label_provenance["records"]) == 96
        and all(
            record["independent_of_judge_prediction"]
            and not record["new_human_annotation_by_this_study"]
            for record in label_provenance["records"]
        ),
        "label provenance is incomplete or overstated",
    )
    return {
        "schema": "mj5-final-integrity-audit-v1",
        "session_id": manifest["session_id"],
        "verdict": "PASS",
        "checks": {
            "matrix_complete": True,
            "source_and_workload_hashes_match": True,
            "runtime_source_snapshot_matches": True,
            "chaincode_version_and_package_recorded": True,
            "chaincode_package_archived_and_reproducible": True,
            "exact_decision_joined_composition_recorded": True,
            "dataset_label_provenance_recorded": True,
            "raw_protocol_certificate_hashes_match_chain_state": True,
            "rgg_required_commit_completion_used": True,
            "fabric_transactions_all_unique_and_valid": True,
            "rgg_safety_gates_pass": True,
            "mvcc_retries_zero": True,
        },
        "totals": totals,
        "scope_notes": [
            "The three repeats are sequential local repeats, not independent deployments.",
            "The experiment uses dataset-provided MMDS labels, including augmented records.",
            "Validator keys and reputation ordering are deterministic controlled-experiment identities.",
            "The measurements are from one Docker Desktop/WSL2 host.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    default_repo = Path(__file__).resolve().parents[2]
    parser.add_argument("session", type=Path)
    parser.add_argument("--repo-root", type=Path, default=default_repo)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = audit(args)
    output = args.session.resolve() / "FINAL_INTEGRITY_AUDIT.json"
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
