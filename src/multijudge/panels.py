from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .schema import CanonicalSample, sha256_file


PANEL_VERSION = "mmds-panel-v1"


@dataclass(frozen=True)
class ImagePanel:
    panel_index: int
    image_indices: tuple[int, ...]
    path: Path
    sha256: str


def _sample_slug(sample_id: str) -> str:
    return hashlib.sha256(sample_id.encode("utf-8")).hexdigest()[:20]


def _render_tile(
    source_path: Path,
    image_index: int,
    tile_size: int,
    label_height: int,
) -> Image.Image:
    background = (244, 244, 241)
    border = (89, 96, 103)
    label_background = (91, 107, 120)
    tile = Image.new("RGB", (tile_size, tile_size), background)
    with Image.open(source_path) as opened:
        transposed = ImageOps.exif_transpose(opened)
        if transposed is None:
            raise RuntimeError(f"Failed to transpose image: {source_path}")
        image = transposed.convert("RGB")
        available = (tile_size - 24, tile_size - label_height - 24)
        contained = ImageOps.contain(image, available, Image.Resampling.LANCZOS)
        x = (tile_size - contained.width) // 2
        y = label_height + (tile_size - label_height - contained.height) // 2
        tile.paste(contained, (x, y))

    draw = ImageDraw.Draw(tile)
    draw.rectangle((0, 0, tile_size - 1, label_height - 1), fill=label_background)
    draw.rectangle((0, 0, tile_size - 1, tile_size - 1), outline=border, width=3)
    label = f"IMAGE {image_index:02d}"
    font = ImageFont.load_default(size=max(18, label_height // 2))
    box = draw.textbbox((0, 0), label, font=font)
    text_width = box[2] - box[0]
    text_height = box[3] - box[1]
    draw.text(
        ((tile_size - text_width) // 2, (label_height - text_height) // 2 - box[1]),
        label,
        fill=(255, 255, 255),
        font=font,
    )
    return tile


def build_image_panels(
    sample: CanonicalSample,
    output_dir: Path,
    *,
    max_images_per_panel: int = 4,
    tile_size: int = 896,
    label_height: int = 64,
) -> list[ImagePanel]:
    if max_images_per_panel != 4:
        raise ValueError("Panel v1 fixes max_images_per_panel at 4")
    if tile_size < 256 or label_height < 24 or label_height >= tile_size // 3:
        raise ValueError("Invalid panel dimensions")
    image_paths = list(sample.image_paths)
    if not image_paths:
        raise ValueError(f"Cannot build a panel for image-free sample {sample.sample_id}")

    output_dir.mkdir(parents=True, exist_ok=True)
    panels: list[ImagePanel] = []
    for start in range(0, len(image_paths), max_images_per_panel):
        paths = image_paths[start : start + max_images_per_panel]
        image_indices = tuple(range(start + 1, start + 1 + len(paths)))
        canvas = Image.new("RGB", (tile_size * 2, tile_size * 2), (250, 250, 248))
        for slot, (path, image_index) in enumerate(zip(paths, image_indices)):
            tile = _render_tile(path, image_index, tile_size, label_height)
            x = (slot % 2) * tile_size
            y = (slot // 2) * tile_size
            canvas.paste(tile, (x, y))

        panel_index = len(panels) + 1
        output_path = (
            output_dir
            / f"{_sample_slug(sample.sample_id)}-panel-{panel_index:02d}.png"
        )
        canvas.save(
            output_path,
            format="PNG",
            optimize=False,
            compress_level=6,
        )
        panels.append(
            ImagePanel(
                panel_index=panel_index,
                image_indices=image_indices,
                path=output_path,
                sha256=sha256_file(output_path),
            )
        )
    return panels
