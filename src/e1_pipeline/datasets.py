from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class EvaluationSample:
    sample_id: str
    dataset: str
    image_path: Path
    prompt: str
    expected_input_safe: bool
    risk_category: str
    variant: str
    reference_response: str | None = None


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _image_root(root: Path) -> Path:
    direct = root / "imgs"
    return direct if direct.is_dir() else root / "data" / "imgs"


def load_mm_safetybench_tiny(root: Path) -> list[EvaluationSample]:
    root = Path(root)
    tiny_manifest = _read_json(root / "TinyVersion_ID_List.json")
    question_root = root / "data" / "processed_questions"
    image_root = _image_root(root)
    variant_prompts = {
        "SD": "Rephrased Question(SD)",
        "SD_TYPO": "Rephrased Question",
        "TYPO": "Rephrased Question",
    }

    samples: list[EvaluationSample] = []
    for scenario_entry in tiny_manifest:
        scenario = scenario_entry["Scenario"]
        questions = _read_json(question_root / f"{scenario}.json")
        for question_id in scenario_entry["Sampled_ID_List"]:
            question = questions[str(question_id)]
            for variant, prompt_key in variant_prompts.items():
                image_path = image_root / scenario / variant / f"{question_id}.jpg"
                if not image_path.is_file():
                    raise FileNotFoundError(f"Missing MM-SafetyBench image: {image_path}")
                samples.append(
                    EvaluationSample(
                        sample_id=f"mm-safetybench:{scenario}:{question_id}:{variant}",
                        dataset="MM-SafetyBench-Tiny",
                        image_path=image_path.resolve(),
                        prompt=question[prompt_key],
                        expected_input_safe=False,
                        risk_category=scenario,
                        variant=variant,
                    )
                )
    return samples


def load_vlguard(root: Path) -> list[EvaluationSample]:
    root = Path(root)
    image_root = root / "test"
    metadata_path = root / "test.json"
    if not metadata_path.is_file():
        metadata_path = image_root / "test.json"
    metadata = _read_json(metadata_path)
    samples: list[EvaluationSample] = []

    for item in metadata:
        image_path = image_root / item["image"]
        if not image_path.is_file():
            raise FileNotFoundError(f"Missing VLGuard image: {image_path}")
        for index, pair in enumerate(item["instr-resp"]):
            if "safe_instruction" in pair:
                prompt_key = "safe_instruction"
                variant = "safe_instruction"
                expected_safe = True
            elif "unsafe_instruction" in pair:
                prompt_key = "unsafe_instruction"
                variant = "unsafe_instruction"
                expected_safe = False
            else:
                prompt_key = "instruction"
                variant = "unsafe_image"
                expected_safe = False

            samples.append(
                EvaluationSample(
                    sample_id=f"vlguard:{item['id']}:{index}:{variant}",
                    dataset="VLGuard-test",
                    image_path=image_path.resolve(),
                    prompt=pair[prompt_key],
                    expected_input_safe=expected_safe,
                    risk_category="safe" if expected_safe else "unsafe",
                    variant=variant,
                    reference_response=pair.get("response"),
                )
            )
    return samples
