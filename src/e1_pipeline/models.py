from dataclasses import dataclass
import gc
from pathlib import Path
import time

from .datasets import EvaluationSample
from .guard import GuardDecision, parse_guard_output


@dataclass(frozen=True)
class GenerationResult:
    response: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    peak_vram_bytes: int


@dataclass(frozen=True)
class ModerationResult:
    decision: GuardDecision
    raw_output: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    peak_vram_bytes: int


def build_vlm_messages(sample: EvaluationSample) -> list[dict]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": str(sample.image_path.resolve())},
                {"type": "text", "text": sample.prompt},
            ],
        }
    ]


class Qwen3VLRunner:
    def __init__(
        self,
        model_path: Path,
        *,
        max_new_tokens: int = 512,
        min_pixels: int = 256 * 28 * 28,
        max_pixels: int = 1280 * 28 * 28,
    ):
        self.model_path = Path(model_path).resolve()
        self.model_id = self.model_path.name
        self.max_new_tokens = max_new_tokens
        self.min_pixels = min_pixels
        self.max_pixels = max_pixels
        self.model = None
        self.processor = None

    def load(self) -> None:
        if self.model is not None:
            return
        import torch
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is required for the E1 VLM run")
        self.processor = AutoProcessor.from_pretrained(
            str(self.model_path),
            local_files_only=True,
            min_pixels=self.min_pixels,
            max_pixels=self.max_pixels,
        )
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(
            str(self.model_path),
            local_files_only=True,
            dtype=torch.bfloat16,
            device_map="cuda",
            low_cpu_mem_usage=True,
        )
        self.model.eval()

    def generate(self, sample: EvaluationSample) -> GenerationResult:
        import torch

        self.load()
        messages = build_vlm_messages(sample)
        inputs = self.processor.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        ).to(self.model.device)
        input_tokens = int(inputs.input_ids.shape[-1])
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.inference_mode():
            generated = self.model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
            )
        torch.cuda.synchronize()
        latency_ms = (time.perf_counter() - started) * 1000
        trimmed = generated[:, input_tokens:]
        response = self.processor.batch_decode(
            trimmed,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0]
        return GenerationResult(
            response=response,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=int(trimmed.shape[-1]),
            peak_vram_bytes=int(torch.cuda.max_memory_allocated()),
        )

    def close(self) -> None:
        import torch

        self.model = None
        self.processor = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


class Qwen3GuardRunner:
    def __init__(self, model_path: Path, *, max_new_tokens: int = 64):
        self.model_path = Path(model_path).resolve()
        self.model_id = self.model_path.name
        self.max_new_tokens = max_new_tokens
        self.model = None
        self.tokenizer = None

    def load(self) -> None:
        if self.model is not None:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is required for the E1 moderation run")
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(self.model_path), local_files_only=True
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            str(self.model_path),
            local_files_only=True,
            dtype=torch.bfloat16,
            device_map="cuda",
            low_cpu_mem_usage=True,
        )
        self.model.eval()

    def moderate(self, prompt: str, response: str) -> ModerationResult:
        import torch

        self.load()
        text = self.tokenizer.apply_chat_template(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": response},
            ],
            tokenize=False,
        )
        inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)
        input_tokens = int(inputs.input_ids.shape[-1])
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.inference_mode():
            generated = self.model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
            )
        torch.cuda.synchronize()
        latency_ms = (time.perf_counter() - started) * 1000
        output_ids = generated[0, input_tokens:]
        raw_output = self.tokenizer.decode(output_ids, skip_special_tokens=True)
        return ModerationResult(
            decision=parse_guard_output(raw_output, require_refusal=True),
            raw_output=raw_output,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=int(output_ids.shape[-1]),
            peak_vram_bytes=int(torch.cuda.max_memory_allocated()),
        )

    def close(self) -> None:
        import torch

        self.model = None
        self.tokenizer = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
