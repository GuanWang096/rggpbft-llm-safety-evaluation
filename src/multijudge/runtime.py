from __future__ import annotations

import hashlib
import importlib.metadata
import os
import platform
import sys
from pathlib import Path
from typing import Any


def sha256_file_streaming(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_environment() -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": "multijudge-environment-v1",
        "python": sys.version,
        "platform": platform.platform(),
        "executable": sys.executable,
        "environment_variables": {
            key: os.environ.get(key)
            for key in (
                "OMP_NUM_THREADS",
                "TOKENIZERS_PARALLELISM",
                "PYTORCH_CUDA_ALLOC_CONF",
            )
        },
        "packages": {},
    }
    for package in (
        "accelerate",
        "einops",
        "numpy",
        "Pillow",
        "safetensors",
        "timm",
        "torch",
        "torchvision",
        "transformers",
    ):
        try:
            result["packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result["packages"][package] = None

    try:
        import torch
    except ImportError:
        result["cuda"] = {"available": False, "reason": "torch_not_installed"}
        return result

    cuda: dict[str, Any] = {
        "available": torch.cuda.is_available(),
        "torch_cuda_version": torch.version.cuda,
        "cudnn_version": (
            torch.backends.cudnn.version()
            if torch.backends.cudnn.is_available()
            else None
        ),
        "device_count": torch.cuda.device_count(),
        "devices": [],
    }
    if torch.cuda.is_available():
        for index in range(torch.cuda.device_count()):
            properties = torch.cuda.get_device_properties(index)
            cuda["devices"].append(
                {
                    "index": index,
                    "name": properties.name,
                    "total_memory_bytes": properties.total_memory,
                    "compute_capability": [
                        properties.major,
                        properties.minor,
                    ],
                }
            )
    result["cuda"] = cuda
    return result


def fingerprint_model(model_path: Path) -> dict[str, Any]:
    model_path = model_path.resolve()
    if not model_path.is_dir():
        raise FileNotFoundError(f"Model directory does not exist: {model_path}")
    suffixes = {
        ".bin",
        ".json",
        ".model",
        ".py",
        ".safetensors",
        ".txt",
    }
    files = sorted(
        path
        for path in model_path.rglob("*")
        if path.is_file()
        and path.suffix.lower() in suffixes
        and ".cache" not in path.parts
    )
    if not files:
        raise RuntimeError(f"No model files found under {model_path}")
    entries = []
    total_bytes = 0
    for path in files:
        size = path.stat().st_size
        total_bytes += size
        entries.append(
            {
                "path": path.relative_to(model_path).as_posix(),
                "size_bytes": size,
                "sha256": sha256_file_streaming(path),
            }
        )
    manifest_digest = hashlib.sha256()
    for entry in entries:
        manifest_digest.update(
            (
                f"{entry['path']}\\0{entry['size_bytes']}\\0"
                f"{entry['sha256']}\\n"
            ).encode("utf-8")
        )
    return {
        "schema": "multijudge-model-fingerprint-v1",
        "model_path": str(model_path),
        "file_count": len(entries),
        "total_bytes": total_bytes,
        "manifest_sha256": manifest_digest.hexdigest(),
        "files": entries,
    }
