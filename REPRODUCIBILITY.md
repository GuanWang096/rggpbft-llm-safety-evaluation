# Reproducibility Guide

This guide separates fast verification from costly reruns. Reviewing the frozen results does not require model inference or a running Docker environment.

## 1. Tested environment

- Windows 11, 32 GiB RAM
- WSL2 with Ubuntu 24.04
- Docker Desktop with the WSL2 backend
- Python 3.10 or newer
- Go 1.22
- Hyperledger Fabric 2.5.16 and Fabric CA 1.5.21
- IPFS Kubo 0.42.0
- GPU run: NVIDIA RTX 5090, PyTorch with CUDA support

The exact component versions are frozen in `src/fabric/setup/versions.env`. Container image records are in `src/fabric/setup/IMAGE_DIGESTS.txt` and the archived run environments.

## 2. Install Python dependencies

For CPU-side analysis, simulations, figures, and tests:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

For E1 GPU inference, install the optional inference dependencies using a PyTorch build compatible with the target CUDA driver:

```bash
python -m pip install -e '.[gpu,test]'
```

## 3. Fast verification

Run the complete Python test collection:

```powershell
python -m pytest -q src
```

Run the Go chaincode tests:

```bash
cd src/fabric/chaincode
go test -count=1 ./...
```

These tests exercise the evidence parsers, deterministic seeds, grouping rules, certificate validation, view-change rules, aggregation logic, and experiment stop gates. They do not rerun the full Docker matrices.

## 4. Audit the archived E1 workload

The authoritative E1 output is in `results/e1/`. It contains 2062 records from MM-SafetyBench-Tiny and VLGuard-test together with the generation and moderation provenance.

```powershell
python src/analyze_e1_results.py `
  --final-run results/e1 `
  --output-dir results/reproduced_e1_audit
```

The archived model combination is:

- Generator: `Qwen/Qwen3-VL-4B-Instruct`
- Guard: `Qwen/Qwen3Guard-Gen-4B`
- Datasets: MM-SafetyBench-Tiny and VLGuard test split

Model weights and full benchmark distributions must be downloaded from their official publishers. Place the datasets in a user-selected directory and pass that path through `--dataset-root`. Do not commit weights or dataset copies to this repository.

## 5. Rerun E1 inference

The formal generation budget is 512 tokens. Only records that reached that limit are regenerated with a 2048-token budget by the top-up stage.

```bash
python -u src/run_e1.py generate \
  --run-dir /path/to/results/e1-base \
  --dataset both \
  --dataset-root /path/to/datasets \
  --model-path /path/to/Qwen3-VL-4B-Instruct \
  --seed 20260704 \
  --max-new-tokens 512

python -u src/run_e1.py moderate \
  --run-dir /path/to/results/e1-base \
  --model-path /path/to/Qwen3Guard-Gen-4B \
  --max-new-tokens 64

python src/run_e1.py summarize --run-dir /path/to/results/e1-base

python src/run_e1_topup.py \
  --base-run /path/to/results/e1-base \
  --run-dir /path/to/results/e1-final \
  --dataset-root /path/to/datasets \
  --model-path /path/to/Qwen3-VL-4B-Instruct \
  --guard-model-path /path/to/Qwen3Guard-Gen-4B \
  --base-limit 512 \
  --max-new-tokens 2048
```

## 6. Rebuild controlled evaluator-reliability inputs

E7 keeps model-safety observations separate from evaluator reliability. Sixteen controlled evaluator profiles report unsafe/safe and refusal/non-refusal decisions over all 2062 records.

```powershell
python src/generate_e7_evaluator_reliability.py `
  --e1-dir results/e1 `
  --output-dir results/reproduced_e7_evaluator_data `
  --seed-base 20260705
```

The expected order is:

```text
9,3,0,6,4,11,13,14,5,2,10,7,1,12,8,15
```

## 7. Consensus and grouping experiments

Start Docker Desktop before running these commands. The consensus implementation is under `src/rggpbft_distributed/`.

Smoke test:

```powershell
python src/rggpbft_distributed/run_v2.py `
  --mode rgg --nodes 16 --groups 4 --rounds 2 --delay-ms 5 `
  --fault-scenario none --run-dir results/smoke_consensus
```

Generate and run the B3-B5 matrices:

```powershell
python src/generate_consensus_matrices.py `
  --output-dir results\reproduced_matrices `
  --corrective

python src/run_consensus_matrix.py `
  --matrix results\reproduced_matrices\b3_corrective_groupingv2.json `
  --output-dir results\reproduced_consensus
```

Run the CPU-only structural grouping ablation using a generated B6 matrix:

```powershell
python src/run_grouping_ablation.py `
  --config results\reproduced_matrices\b6_grouping_ablation.json
```

For the E8 protocol-level grouping ablation:

```powershell
python src/generate_e8_protocol_ablation.py `
  --output-dir results\reproduced_e8_matrices `
  --fault-m16-main-only

python src/run_consensus_matrix.py `
  --matrix results\reproduced_e8_matrices\e8_fault_matrix.json `
  --output-dir results\reproduced_e8
```

Every run stores its realized group map, leader sequence, seed material, status, and summary. Check the generated filenames before launching a full matrix; the generator is the source of truth for matrix names.

## 8. Fabric and IPFS experiments

Install the frozen Fabric components from WSL:

```bash
cd src/fabric
bash setup/install-fabric.sh --fabric-version 2.5.16 --ca-version 1.5.21 docker binary samples
```

The downloaded `fabric-samples` directory must be available as `src/fabric/fabric-samples`. The repository does not vendor the upstream Fabric sample repository.

The chaincode is in `src/fabric/chaincode`, clients are in `src/fabric/client`, and formal benchmark drivers are in `src/fabric/benchmarks`. Network helper scripts are in `src/fabric/network/scripts`.

The complete E7 Fabric runner requires the three-organization test network, evaluator certificates, deployed chaincode, and IPFS to be running:

```powershell
python src/run_e7_fabric.py `
  --all `
  --score-file results/e7_evaluator_data/scores.json `
  --output-dir results/reproduced_e7_fabric
```

Use the resulting `e7_all_results.json` to configure consensus:

```powershell
python src/run_e7_consensus.py `
  --fabric-results results/reproduced_e7_fabric/e7_all_results.json `
  --output-dir results/reproduced_e7_consensus
```

## 9. Network, capacity, and temporal experiments

Verified netem qualification requires Linux traffic-control support and Docker containers with `NET_ADMIN` capability:

```powershell
python src/run_e9_netem.py `
  --output-dir results/reproduced_e9_qualification `
  --qualification-only
```

Fabric/IPFS capacity matrix:

```powershell
python src/run_e10_capacity.py --resource-only --repeats 3
```

Temporal reputation simulation:

```powershell
python src/run_e11_temporal_reputation.py `
  --output-dir results/reproduced_e11 `
  --nodes 16 --groups 4 --rounds 100 --seeds 100

python src/aggregate_e11_temporal_reputation.py results/reproduced_e11
```

## 10. Result interpretation limits

- E1 guard decisions are operational proxy labels, not human ground truth.
- Fabric, IPFS, consensus, capacity, and netem measurements are single-host multi-container results.
- Measured consensus scale is 16 to 24 nodes. M=32 performance is not claimed.
- Reputation ranking changes placement and leader exposure. The grouped protocol topology, not reputation quality, supplies the communication reduction.
- The temporal study compares policies within the tested simulation matrix and does not establish a universally optimal policy.
