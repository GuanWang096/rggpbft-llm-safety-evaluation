# Reproducibility Guide

This guide separates three tasks: checking the published artifacts, repeating the multimodal judge inference, and rerunning the Docker-based system experiments. Artifact checks require neither a GPU nor Docker.

## 1. Frozen evidence

The v15 primary evidence is organized as follows:

| Path | Contents |
|---|---|
| `results/multijudge/formal/` | Frozen validation and 330-sample MMDS test outputs |
| `results/multijudge/analysis/` | Aggregation metrics, bootstrap intervals, McNemar tests, and attack diagnostics |
| `results/cross_layer/workload/` | Deterministic J=3/J=4 evidence bundles and system matrix |
| `results/cross_layer/formal/` | Final Fabric/IPFS/RGG-PBFT run, aggregates, and integrity audit |

`RESULTS_MANIFEST.md` maps the secondary evidence-management and protocol results retained from the earlier experimental series.

## 2. Tested environments

CPU and Docker experiments:

- Windows 11 with 32 GiB RAM
- WSL2, Ubuntu 24.04
- Docker Desktop with the WSL2 backend
- Python 3.10 or newer
- Go 1.22
- Hyperledger Fabric 2.5.16 and Fabric CA 1.5.21
- IPFS Kubo 0.42.0

Judge inference:

- NVIDIA RTX 5090 with 32 GiB VRAM
- PyTorch 2.8.0 with CUDA 12.8
- Transformers 4.57.6

The frozen result directories retain model fingerprints, runtime metadata, source snapshots, and configuration files.

## 3. Install the CPU environment

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

Run the release checker before any costly rerun:

```powershell
python src/verify_release.py
python -m pytest -q src
```

The expected release-check output is:

```text
RELEASE_VERIFICATION_PASS
```

The checker reads the frozen JSON files and verifies the headline majority-versus-likelihood metrics, bootstrap intervals, McNemar counts, and final cross-layer integrity totals.

## 4. Dataset and model layout

The GPU scripts use this dataset layout:

```text
/root/datasets/
  MMDS/
    mmds.jsonl
    images/
```

Model weights are stored separately:

```text
/root/autodl-tmp/model/
  Qwen3-VL-8B-Instruct/
  SafeWork-RM-Safety-7B/
  InternVL3_5-8B-Instruct/
  MiniCPM-V-4_5/
```

The pinned revisions are:

| Model | Revision |
|---|---|
| `Qwen/Qwen3-VL-8B-Instruct` | `5d854aab08710c16b980ec6d603d863b3821b915` |
| `AI45Research/SafeWork-RM-Safety-7B` | `be345f29425fe94586c0598785a143703bbbc4fc` |
| `OpenGVLab/InternVL3_5-8B-Instruct` | `6c2034f6f3d22bbbff919b11b91c5721bba84f8d` |
| `OpenBMB/MiniCPM-V-4_5` | `2626e837a54905aab70fae9325153ef3454387ab` |

The repository does not redistribute model weights or complete dataset copies. Obtain them from their original providers and retain their license terms.

## 5. Repeat the judge inference

The AutoDL scripts assume the repository is checked out at `/root/src`. Install the GPU dependencies once:

```bash
cd /root/src
python -m pip install -r requirements-gpu.txt
python -m pip install --no-deps -e .
```

Download or verify each model with the scripts under `src/multijudge_workflows/`. Run the 100-sample qualification gates before the formal split:

```bash
cd /root/src
./src/multijudge_workflows/autodl_run_qwen3vl_smoke.sh
./src/multijudge_workflows/autodl_run_qwen_repeat.sh
./src/multijudge_workflows/autodl_run_native_qualification.sh safework
./src/multijudge_workflows/autodl_run_native_qualification.sh internvl
./src/multijudge_workflows/autodl_run_native_qualification.sh minicpm
```

Run validation, freeze the selected aggregation settings, and then unlock the test split:

```bash
cd /root/src
./src/multijudge_workflows/autodl_run_mj1.sh val all

python src/multijudge_workflows/freeze_mj1_validation.py \
  --validation-dir /root/result/multijudge-v15/formal/val \
  --manifest src/multijudge_workflows/configs/mmds_val_formal.json \
  --output-dir /root/result/multijudge-v15/formal/freeze

cp /root/result/multijudge-v15/formal/freeze/validation_frozen.json \
  /root/result/multijudge-v15/formal/validation_frozen.json

./src/multijudge_workflows/autodl_run_mj1.sh test all
```

Generate the primary and diagnostic analyses:

```bash
cd /root/src
python src/multijudge_workflows/analyze_mj1_mj2.py \
  --test-dir /root/result/multijudge-v15/formal/test \
  --manifest src/multijudge_workflows/configs/mmds_test_formal.json \
  --freeze /root/result/multijudge-v15/formal/validation_frozen.json \
  --output-dir /root/result/multijudge-v15/analysis

python src/multijudge_workflows/analyze_mj2_extended.py \
  --test-dir /root/result/multijudge-v15/formal/test \
  --test-manifest src/multijudge_workflows/configs/mmds_test_formal.json \
  --validation-dir /root/result/multijudge-v15/formal/val \
  --validation-manifest src/multijudge_workflows/configs/mmds_val_formal.json \
  --freeze /root/result/multijudge-v15/formal/validation_frozen.json \
  --output-dir /root/result/multijudge-v15/analysis

python src/multijudge_workflows/run_mj3_mj4.py \
  --test-dir /root/result/multijudge-v15/formal/test \
  --test-manifest src/multijudge_workflows/configs/mmds_test_formal.json \
  --validation-dir /root/result/multijudge-v15/formal/val \
  --validation-manifest src/multijudge_workflows/configs/mmds_val_formal.json \
  --freeze /root/result/multijudge-v15/formal/validation_frozen.json \
  --output-dir /root/result/multijudge-v15/analysis
```

The primary comparison uses the same Qwen, SafeWork, and MiniCPM committee for both methods. It compares class-conditional reliability likelihood aggregation with unweighted 2-of-3 majority voting.

## 6. Audit the frozen multi-judge results

The raw model decisions are in:

```text
results/multijudge/formal/test/<model>/judgments.jsonl
```

The primary reported values are under `headline_same_committee_comparison` in:

```text
results/multijudge/analysis/mj2_extended_results.json
```

The release checker verifies these values without rerunning inference:

```powershell
python src/verify_release.py
```

## 7. Build the cross-layer workload

Build deterministic evidence bundles from the frozen test judgments:

```powershell
python src/multijudge_workflows/build_mj5_workload.py `
  --formal-test results/multijudge/formal/test `
  --freeze results/multijudge/formal/validation_frozen.json `
  --output results/cross_layer/reproduced_workload `
  --system-sample-count 96
```

The generated summary must report 330 source samples, 660 J=3/J=4 bundles, and 18 matrix runs.

## 8. Rerun the Fabric/IPFS/RGG-PBFT matrix

Start Docker Desktop before this stage. The full run also requires the Hyperledger Fabric test network, channel `trustchannel`, IPFS, and the Fabric client identities expected by `mj5_fabric_client.py`.

Install the frozen Fabric binaries and samples:

```bash
cd src/fabric
bash setup/install-fabric.sh --fabric-version 2.5.16 --ca-version 1.5.21 docker binary samples
```

Deploy the v15 chaincode after the network and channel are ready:

```bash
cd /path/to/repository
bash src/multijudge_workflows/deploy_mj5_chaincode.sh
```

Run a smoke test before the formal matrix:

```powershell
python src/multijudge_workflows/run_mj5_smoke.py `
  --repo-root . `
  --workload results/cross_layer/workload `
  --output results/cross_layer/smoke
```

Start the formal 18-run matrix only after the smoke gate passes:

```bash
bash src/multijudge_workflows/start_mj5_formal.sh mj5-reproduction
```

The formal output is written to `results/cross_layer/runs/mj5-reproduction/`. Analyze and audit it:

```powershell
python src/multijudge_workflows/analyze_mj5_formal.py `
  results/cross_layer/runs/mj5-reproduction

python src/multijudge_workflows/audit_mj5_final.py `
  --repo-root . `
  results/cross_layer/runs/mj5-reproduction
```

The frozen final audit is `results/cross_layer/formal/FINAL_INTEGRITY_AUDIT.json`. It records 1,728 Stage A rows, 1,728 Stage C rows, 1,728 protocol certificates, 8,640 unique valid Fabric transactions, zero MVCC retries, and a `PASS` verdict.

## 9. Secondary protocol experiments

The earlier experiment series remains available because the paper uses it to characterize RGG-PBFT safety, communication, grouping, network sensitivity, capacity, and temporal reputation.

Run all Python tests before launching a matrix:

```powershell
python -m pytest -q src
```

Representative entry points include:

```powershell
python src/generate_consensus_matrices.py --output-dir results/reproduced_matrices --corrective
python src/run_consensus_matrix.py --matrix results/reproduced_matrices/b3_corrective_groupingv2.json --output-dir results/reproduced_consensus
python src/run_e9_netem.py --output-dir results/reproduced_e9 --qualification-only
python src/run_e11_temporal_reputation.py --output-dir results/reproduced_e11 --nodes 16 --groups 4 --rounds 100 --seeds 100
```

The generated matrix filenames are authoritative. Inspect them before a full Docker run.

## 10. Interpretation boundaries

- MMDS supplies the reference labels used for judge evaluation.
- The primary accuracy result concerns the frozen 330-sample test split and the stated three-judge committee.
- The 96-sample cross-layer replay measures infrastructure behavior; it does not replace the 330-sample accuracy evaluation.
- The Fabric, IPFS, consensus, capacity, and netem results come from one Docker Desktop/WSL2 host.
- The three formal system repeats are sequential local repeats, not independent deployments.
- Reputation ranking changes validator placement and leader exposure. The grouped topology supplies the communication reduction.
