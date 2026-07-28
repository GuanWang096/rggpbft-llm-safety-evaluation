from __future__ import annotations

import gc
import time
from pathlib import Path
from typing import Any

from PIL import Image

from .adapters import InputMode, JudgeAdapter
from .parsers import parse_canonical_decision
from .policy import CanonicalPolicy, render_policy_prompt
from .recording import build_native_record
from .schema import (
    CanonicalSample,
    JudgeServiceIdentity,
    canonical_json,
    sha256_text,
)


ADAPTER_VERSION = "internvl3.5-binary-judge-v3"
IMAGE_SIZE = 448
MAX_TILES_PER_IMAGE = 4
MAX_TOTAL_TILES = 16
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def _closest_ratio(
    aspect_ratio: float,
    target_ratios: list[tuple[int, int]],
    width: int,
    height: int,
    image_size: int,
) -> tuple[int, int]:
    best = (1, 1)
    best_difference = float("inf")
    area = width * height
    for ratio in target_ratios:
        candidate = ratio[0] / ratio[1]
        difference = abs(aspect_ratio - candidate)
        if difference < best_difference:
            best_difference = difference
            best = ratio
        elif difference == best_difference:
            threshold = 0.5 * image_size * image_size * ratio[0] * ratio[1]
            if area > threshold:
                best = ratio
    return best


def dynamic_tiles(
    image: Image.Image,
    *,
    image_size: int = IMAGE_SIZE,
    max_tiles: int = MAX_TILES_PER_IMAGE,
    use_thumbnail: bool = True,
) -> list[Image.Image]:
    if max_tiles < 1:
        raise ValueError("max_tiles must be positive")
    rgb = image.convert("RGB")
    width, height = rgb.size
    ratios = sorted(
        {
            (columns, rows)
            for tile_count in range(1, max_tiles + 1)
            for columns in range(1, tile_count + 1)
            for rows in range(1, tile_count + 1)
            if 1 <= columns * rows <= max_tiles
        },
        key=lambda value: (value[0] * value[1], value[0], value[1]),
    )
    columns, rows = _closest_ratio(
        width / height,
        ratios,
        width,
        height,
        image_size,
    )
    resized = rgb.resize(
        (image_size * columns, image_size * rows),
        Image.Resampling.BICUBIC,
    )
    tiles = [
        resized.crop(
            (
                (index % columns) * image_size,
                (index // columns) * image_size,
                ((index % columns) + 1) * image_size,
                ((index // columns) + 1) * image_size,
            )
        )
        for index in range(columns * rows)
    ]
    if use_thumbnail and len(tiles) > 1 and len(tiles) < max_tiles:
        tiles.append(
            rgb.resize((image_size, image_size), Image.Resampling.BICUBIC)
        )
    return tiles


def tiles_per_image(image_count: int) -> int:
    if image_count < 1:
        raise ValueError("InternVL requires at least one image")
    if image_count > MAX_TOTAL_TILES:
        raise ValueError(
            f"InternVL image count exceeds tile budget: {image_count} > "
            f"{MAX_TOTAL_TILES}"
        )
    return min(MAX_TILES_PER_IMAGE, MAX_TOTAL_TILES // image_count)


def configure_language_attention(
    model: Any,
    implementation: str,
) -> None:
    if implementation not in {"eager", "sdpa"}:
        raise ValueError(
            "InternVL attention implementation must be eager or sdpa"
        )
    model.config.llm_config._attn_implementation = implementation
    model.language_model.config._attn_implementation = implementation


def build_internvl_question(
    sample: CanonicalSample,
    policy_prompt: str,
) -> str:
    lines = [
        policy_prompt,
        "",
        "DIALOGUE EVIDENCE follows. Each numbered image placeholder appears "
        "at the turn where that image was supplied.",
    ]
    image_index = 0
    for turn_index, turn in enumerate(sample.turns, start=1):
        markers = []
        for _ in turn.image_paths:
            image_index += 1
            markers.append(f"IMAGE_{image_index:02d}: <image>")
        header = f"[TURN_{turn_index:02d}][{turn.role.upper()}]"
        if markers:
            lines.append(f"{header} {' '.join(markers)}")
        else:
            lines.append(header)
        lines.append(turn.content.strip())
    return "\n".join(lines)


def _load_pixel_values(
    image_paths: tuple[Path, ...],
    torch_module: Any,
) -> tuple[Any, list[int]]:
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("InternVL preprocessing requires numpy") from exc

    tensors = []
    patch_counts = []
    per_image_budget = tiles_per_image(len(image_paths))
    mean = torch_module.tensor(IMAGENET_MEAN).view(3, 1, 1)
    std = torch_module.tensor(IMAGENET_STD).view(3, 1, 1)
    for path in image_paths:
        with Image.open(path) as source:
            tiles = dynamic_tiles(source, max_tiles=per_image_budget)
        patch_counts.append(len(tiles))
        for tile in tiles:
            array = np.asarray(tile, dtype="float32") / 255.0
            tensor = torch_module.from_numpy(array).permute(2, 0, 1)
            tensors.append((tensor - mean) / std)
    if not tensors:
        raise ValueError("InternVL requires at least one image")
    if sum(patch_counts) > MAX_TOTAL_TILES:
        raise ValueError(
            f"InternVL tile budget exceeded: {sum(patch_counts)} > "
            f"{MAX_TOTAL_TILES}"
        )
    return torch_module.stack(tensors), patch_counts


class InternVLJudgeAdapter(JudgeAdapter):
    def __init__(
        self,
        *,
        model_path: Path,
        model_revision: str,
        policy: CanonicalPolicy,
        max_new_tokens: int = 32,
        attn_implementation: str = "sdpa",
    ) -> None:
        self.model_path = model_path.resolve()
        self.policy = policy
        self.policy_prompt = render_policy_prompt(policy)
        self.max_new_tokens = max_new_tokens
        self.attn_implementation = attn_implementation
        self.identity = JudgeServiceIdentity(
            organization="OpenGVLab",
            model_id="OpenGVLab/InternVL3_5-8B-Instruct",
            model_revision=model_revision,
            policy_sha256=policy.sha256,
            adapter_version=ADAPTER_VERSION,
        )
        self._torch: Any | None = None
        self._tokenizer: Any | None = None
        self._model: Any | None = None

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "InternVL requires torch and transformers"
            ) from exc
        if not torch.cuda.is_available():
            raise RuntimeError("InternVL qualification requires a CUDA GPU")
        self._model = (
            AutoModel.from_pretrained(
                str(self.model_path),
                dtype=torch.bfloat16,
                low_cpu_mem_usage=True,
                use_flash_attn=False,
                trust_remote_code=True,
                local_files_only=True,
            )
            .eval()
            .cuda()
        )
        configure_language_attention(
            self._model,
            self.attn_implementation,
        )
        self._tokenizer = AutoTokenizer.from_pretrained(
            str(self.model_path),
            trust_remote_code=True,
            use_fast=False,
            local_files_only=True,
        )
        self._torch = torch

    def judge(
        self,
        sample: CanonicalSample,
        *,
        input_mode: InputMode,
        panel_dir: Path,
    ) -> dict[str, Any]:
        del panel_dir
        if input_mode != "native":
            raise ValueError("InternVL supports only native multi-image input")
        self.load()
        assert self._torch is not None
        assert self._tokenizer is not None
        assert self._model is not None
        torch = self._torch

        question = build_internvl_question(sample, self.policy_prompt)
        pixel_values, patch_counts = _load_pixel_values(
            sample.image_paths,
            torch,
        )
        pixel_values = pixel_values.to(dtype=torch.bfloat16, device="cuda")
        generation_config = {
            "max_new_tokens": self.max_new_tokens,
            "do_sample": False,
            "num_beams": 1,
        }
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        with torch.inference_mode():
            raw_output = self._model.chat(
                self._tokenizer,
                pixel_values,
                question,
                generation_config,
                num_patches_list=patch_counts,
                history=None,
                return_history=False,
            )
        torch.cuda.synchronize()
        latency_ms = (time.perf_counter() - started) * 1000.0
        parsed = parse_canonical_decision(
            str(raw_output),
            self.policy.categories,
        )
        return build_native_record(
            sample=sample,
            identity=self.identity,
            policy=self.policy,
            generation={
                **generation_config,
                "dtype": "bfloat16",
                "attn_implementation": self.attn_implementation,
                "image_size": IMAGE_SIZE,
                "max_tiles_per_image": MAX_TILES_PER_IMAGE,
                "max_total_tiles": MAX_TOTAL_TILES,
                "patch_counts": patch_counts,
            },
            prompt_sha256=sha256_text(question),
            raw_output=str(raw_output),
            parsed=parsed,
            latency_ms=latency_ms,
            peak_vram_gib=torch.cuda.max_memory_allocated() / (1024**3),
        )

    def close(self) -> None:
        self._tokenizer = None
        self._model = None
        if self._torch is not None and self._torch.cuda.is_available():
            self._torch.cuda.empty_cache()
        self._torch = None
        gc.collect()
