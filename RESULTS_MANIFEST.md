# Results Manifest

The paths below contain the frozen artifacts used by the manuscript. Historical, interrupted, and superseded v15 runs are excluded.

| Repository path | Evidence role |
|---|---|
| `results/multijudge/formal/` | Frozen validation and test outputs from four multimodal judge services |
| `results/multijudge/analysis/mj1_mj2_results.json` | Per-model results, disagreement, and initial aggregation analyses |
| `results/multijudge/analysis/mj2_extended_results.json` | Primary same-committee majority-versus-likelihood comparison, bootstrap intervals, and McNemar test |
| `results/multijudge/analysis/mj3_mj4_results.json` | Attack perturbations and committee diagnostics |
| `results/cross_layer/workload/` | Deterministic J=3/J=4 evidence bundles and 18-run system matrix |
| `results/cross_layer/formal/` | Final 18-run Fabric/IPFS/RGG-PBFT session, aggregate statistics, source snapshots, and integrity audit |
| `results/e1/` | Final 2062-record E1 generation and guard output after selective 2048-token top-up |
| `results/b1_evidence_anchor/` | IPFS/Fabric evidence anchoring and negative mutation tests |
| `results/b2_replay/` | Full 2062-record Fabric/IPFS and signed-log replay |
| `results/b2_storage_ablation/` | Batch-size and storage-path ablation |
| `results/b3_qualification/` | Ten consensus qualification runs |
| `results/b4_fault_recovery/` | 200 protocol-fault and recovery runs |
| `results/b5_performance/` | 100 paired PBFT/RGG-PBFT normal-path runs |
| `results/b6_grouping_ablation/` | 120 structural grouping mappings |
| `results/e7_evaluator_data/` | Controlled evaluator-reliability reports and score order |
| `results/e7_fabric/` | 22 Fabric state-path runs and identity bindings |
| `results/e7_consensus/` | Ten Fabric-derived 20-round RGG-PBFT runs |
| `results/e8_protocol_ablation/` | 132 strategy-specific fault runs, including qualification cases |
| `results/e9_netem/` | 160 statistical single-host netem runs |
| `results/e9_qualification/` | 16 netem ordering, qdisc, RTT, and cleanup qualification runs |
| `results/e10_capacity/` | 12 Fabric/IPFS capacity runs with resource sampling |
| `results/e11_temporal/` | 3500 deterministic and 37800 probabilistic temporal-reputation simulations |

The `results/multijudge/` and `results/cross_layer/` directories support the v15 paper's primary aggregation and system claims. The remaining directories support the secondary evidence-management, consensus, grouping, network, capacity, and temporal analyses retained from the preceding experimental series. Directories ending in `_aggregate` contain their frozen tables and validation reports.

Run `python src/verify_release.py` from the repository root to check the primary numerical claims and cross-layer integrity totals.
