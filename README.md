# Reliability-Aware Aggregation of Multimodal LLM Safety Judges

This repository contains the source code and non-proprietary result artifacts for the paper:

> A Trust-Evidence System for Reliability-Aware Aggregation of Multimodal LLM Safety Judges

The system combines class-conditional reliability aggregation with evidence anchoring, committee confirmation, reputation settlement, and reputation-guided grouped PBFT (RGG-PBFT). The frozen release contains the multimodal judge outputs, aggregation analyses, cross-layer system runs, and secondary protocol experiments reported in the manuscript.

## Repository layout

| Path | Contents |
|---|---|
| `src/` | All executable source code, orchestration scripts, and tests |
| `src/multijudge/` | Judge adapters, input construction, parsers, and policy logic |
| `src/multijudge_workflows/` | Formal inference, aggregation, MJ5 orchestration, and tests |
| `src/fabric_chaincode/` | Go chaincode used by the v15 cross-layer experiment |
| `src/rggpbft/` | RGG-PBFT implementation used by the v15 cross-layer experiment |
| `src/fabric/`, `src/rggpbft_distributed/` | Supporting Fabric and protocol experiment code |
| `results/` | Frozen raw and aggregate artifacts used by the paper |
| `REPRODUCIBILITY.md` | Installation, verification, and rerun instructions |
| `RESULTS_MANIFEST.md` | Mapping from paper evidence to retained result directories |

All project code is under `src/`. There is intentionally no separate `experiments/` or `docs/` directory.

## Quick verification

Python 3.10 or newer is required. From the repository root:

```powershell
python -m pip install -e ".[test]"
python src/verify_release.py
python -m pytest -q src
```

`verify_release.py` checks the headline aggregation metrics and the final cross-layer integrity totals directly from the frozen JSON files. Full inference requires a CUDA GPU. Fabric and consensus reruns require Docker, WSL2 or Linux, Go, Hyperledger Fabric, and IPFS.

## Data and model policy

The repository includes the non-proprietary records needed to audit the reported results. Model weights and complete copies of MMDS are not redistributed. The guide records each model revision and the expected dataset layout.

The formal judge set contains Qwen3-VL-8B-Instruct, SafeWork-RM-Safety-7B, InternVL3.5-8B-Instruct, and MiniCPM-V-4.5. The primary same-committee comparison uses Qwen, SafeWork, and MiniCPM against an unweighted 2-of-3 majority baseline on the frozen 330-sample MMDS test split.

## Scope

The archived Fabric, IPFS, PBFT, and netem measurements were collected on one Windows 11 host using Docker Desktop and WSL2. The results support paired single-host comparisons and local capacity analysis. They do not measure multi-host deployment performance.

## License

Source code is released under the MIT License. Retained third-party datasets and models remain subject to their original licenses.

## Citation

Jinxin Zhang, Guan Wang, Zhipeng Ruan, and Changsheng Wan, "A Trust-Evidence System for Reliability-Aware Aggregation of Multimodal LLM Safety Judges," manuscript submitted to *Concurrency and Computation: Practice and Experience*, 2026.
