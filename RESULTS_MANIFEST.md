# Results Manifest

Only authoritative result artifacts used by the manuscript are retained. Historical, failed, interrupted, and superseded runs are excluded.

| Repository path | Evidence role |
|---|---|
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

Directories ending in `_aggregate` contain the corresponding frozen aggregate tables and validation reports. Existing `checksums.sha256` files remain with the result series they validate.
