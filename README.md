# Blockchain Trust-Evidence Architecture for Multimodal LLM Safety Evaluation

This repository contains the source code and non-proprietary result artifacts for the paper:

> A Blockchain Trust-Evidence Architecture With Threshold Confirmation and Reputation-Guided PBFT for Multimodal LLM Safety Evaluation

The implementation connects multimodal safety-evaluation evidence, IPFS storage, Hyperledger Fabric confirmation and reputation settlement, and reputation-guided grouped PBFT (RGG-PBFT). The repository also contains the controlled evaluator-reliability, network-sensitivity, capacity, grouping-ablation, and temporal-reputation experiments reported in the paper.

## Repository layout

| Path | Contents |
|---|---|
| `src/` | All executable source code, orchestration scripts, and tests |
| `src/e1_pipeline/` | Multimodal generation and guard-model pipeline |
| `src/rggpbft_distributed/` | Signed PBFT and RGG-PBFT Docker implementation |
| `src/fabric/` | Fabric chaincode, clients, benchmarks, and setup scripts |
| `src/tests/` | Unit and experiment-integrity tests |
| `results/` | Frozen raw and aggregate artifacts used by the paper |
| `REPRODUCIBILITY.md` | Installation, verification, and rerun instructions |
| `RESULTS_MANIFEST.md` | Mapping from paper evidence to retained result directories |

All project code is under `src/`. There is intentionally no separate `experiments/` or `docs/` directory.

## Quick verification

Python 3.10 or newer is required. From the repository root:

```powershell
python -m pip install -e ".[test]"
python -m pytest -q src
```

The full Fabric and consensus experiments additionally require Docker Desktop, WSL2, Go, Hyperledger Fabric, and IPFS. GPU inference is optional when reviewing the archived results. See [REPRODUCIBILITY.md](REPRODUCIBILITY.md) for exact commands and scope.

## Data and model policy

The repository includes the non-proprietary records needed to audit the reported results. Public benchmark images, complete benchmark distributions, and model weights are not redistributed. Their source identifiers, versions, expected layout, and download instructions are listed in `REPRODUCIBILITY.md`.

Qwen3Guard outputs are used as operational proxy labels for the infrastructure workload. They are not presented as human ground truth or as a general estimate of model-safety accuracy.

## Scope

The archived Fabric, IPFS, PBFT, and netem measurements were collected on one Windows 11 host using Docker Desktop and WSL2. They support the paired single-host comparisons reported in the paper, not claims about geographically distributed or production deployment performance.

## License

Source code is released under the MIT License. Retained third-party datasets and models remain subject to their original licenses.

## Citation

Jinxin Zhang, Guan Wang, Zhipeng Ruan, and Changsheng Wan, "A Blockchain Trust-Evidence Architecture With Threshold Confirmation and Reputation-Guided PBFT for Multimodal LLM Safety Evaluation," manuscript submitted to *Concurrency and Computation: Practice and Experience*, 2026.
