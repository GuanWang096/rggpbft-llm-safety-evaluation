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


ADAPTER_VERSION = "minicpm-v4.5-binary-judge-v2"
MAX_SLICE_NUMS = 4
MAX_TOTAL_SLICES = 16


def slices_per_image(image_count: int) -> int:
    if image_count < 1:
        raise ValueError("MiniCPM requires at least one image")
    if image_count > MAX_TOTAL_SLICES:
        raise ValueError(
            f"MiniCPM image count exceeds slice budget: {image_count} > "
            f"{MAX_TOTAL_SLICES}"
        )
    return min(MAX_SLICE_NUMS, MAX_TOTAL_SLICES // image_count)


def build_minicpm_content(
    sample: CanonicalSample,
    policy_prompt: str,
) -> tuple[list[Any], str]:
    content: list[Any] = [
        policy_prompt
        + "\n\nDIALOGUE EVIDENCE follows. Image markers identify the visual "
        "evidence supplied at each turn."
    ]
    semantic: list[dict[str, str]] = [
        {"type": "text", "text": str(content[0])}
    ]
    image_index = 0
    for turn_index, turn in enumerate(sample.turns, start=1):
        indices = list(
            range(image_index + 1, image_index + 1 + len(turn.image_paths))
        )
        markers = " ".join(f"[IMAGE_{value:02d}]" for value in indices)
        header = (
            f"[TURN_{turn_index:02d}][{turn.role.upper()}] {markers}"
        ).rstrip()
        content.append(header)
        semantic.append({"type": "text", "text": header})
        for path in turn.image_paths:
            image_index += 1
            with Image.open(path) as source:
                image = source.convert("RGB").copy()
            content.append(image)
            semantic.append(
                {"type": "image", "image": f"IMAGE_BINARY_{image_index:02d}"}
            )
        text = turn.content.strip()
        content.append(text)
        semantic.append({"type": "text", "text": text})
    return content, sha256_text(canonical_json(semantic))


class MiniCPMJudgeAdapter(JudgeAdapter):
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
            organization="OpenBMB",
            model_id="OpenBMB/MiniCPM-V-4_5",
            model_revision=model_revision,
            policy_sha256=policy.sha256,
            adapter_version=ADAPTER_VERSION,
        )
        self._torch: Any | None = None
        self._tokenizer: Any | None = None
        self._processor: Any | None = None
        self._model: Any | None = None

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModel, AutoProcessor, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "MiniCPM requires torch and transformers"
            ) from exc
        if not torch.cuda.is_available():
            raise RuntimeError("MiniCPM qualification requires a CUDA GPU")
        self._model = (
            AutoModel.from_pretrained(
                str(self.model_path),
                trust_remote_code=True,
                attn_implementation=self.attn_implementation,
                torch_dtype=torch.bfloat16,
                local_files_only=True,
            )
            .eval()
            .cuda()
        )
        self._tokenizer = AutoTokenizer.from_pretrained(
            str(self.model_path),
            trust_remote_code=True,
            local_files_only=True,
        )
        self._processor = AutoProcessor.from_pretrained(
            str(self.model_path),
            trust_remote_code=True,
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
            raise ValueError("MiniCPM supports only native multi-image input")
        self.load()
        assert self._torch is not None
        assert self._tokenizer is not None
        assert self._processor is not None
        assert self._model is not None
        torch = self._torch

        content, prompt_sha256 = build_minicpm_content(
            sample,
            self.policy_prompt,
        )
        slice_budget = slices_per_image(len(sample.image_paths))
        messages = [{"role": "user", "content": content}]
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        with torch.inference_mode():
            raw_output = self._model.chat(
                msgs=messages,
                tokenizer=self._tokenizer,
                processor=self._processor,
                max_new_tokens=self.max_new_tokens,
                sampling=False,
                num_beams=1,
                repetition_penalty=1.0,
                stream=False,
                max_slice_nums=slice_budget,
                enable_thinking=False,
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
                "do_sample": False,
                "num_beams": 1,
                "repetition_penalty": 1.0,
                "max_new_tokens": self.max_new_tokens,
                "attn_implementation": self.attn_implementation,
                "dtype": "bfloat16",
                "max_slice_nums": MAX_SLICE_NUMS,
                "effective_max_slice_nums": slice_budget,
                "max_total_slices": MAX_TOTAL_SLICES,
                "enable_thinking": False,
            },
            prompt_sha256=prompt_sha256,
            raw_output=str(raw_output),
            parsed=parsed,
            latency_ms=latency_ms,
            peak_vram_gib=torch.cuda.max_memory_allocated() / (1024**3),
        )

    def close(self) -> None:
        self._tokenizer = None
        self._processor = None
        self._model = None
        if self._torch is not None and self._torch.cuda.is_available():
            self._torch.cuda.empty_cache()
        self._torch = None
        gc.collect()
