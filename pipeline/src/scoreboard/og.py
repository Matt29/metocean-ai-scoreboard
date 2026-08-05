"""`scoreboard og-images` — one 1200x630 Open Graph PNG per station in `data/scores.json`.

Pillow-only, no embedded font file: `ImageFont.load_default(size=...)` (Pillow
>= 10) ships its own bitmap font. Read-only over `scores.json`/`stations.toml` —
writes exactly `data/<id>/og.png`, nothing else in the JSON contract.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from scoreboard.config import load_stations
from scoreboard.publish import UNIT

WIDTH, HEIGHT = 1200, 630
BG = (14, 26, 36)  # #0E1A24
FG = (255, 255, 255)
ACCENT = (110, 220, 180)
MUTED = (150, 172, 194)
MARGIN = 80


def _ascii(text: str) -> str:
    """Fold to ASCII: `ImageFont.load_default()`'s bitmap font has no accent/em-dash
    glyphs (they render as tofu boxes), and no embedded font file is allowed here."""
    text = text.replace("—", "-").replace("’", "'")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _display_name(station_id: str, stations: dict) -> tuple[str, str | None]:
    """(name, unit) from `stations.toml`, falling back to the capitalized id."""
    s = stations.get(station_id)
    if s is not None:
        return s.name, UNIT.get(s.kind)
    return station_id.replace("-", " ").title(), None


def _mae_line(entry: dict, unit: str | None) -> str:
    ia = entry.get("mae_ia_7d")
    baseline = entry.get("mae_baseline_7d")
    if entry.get("status") == "missing" or not _is_finite(ia) or not _is_finite(baseline):
        return _ascii("Données en cours de collecte")
    suffix = f" {unit}" if unit else ""
    return _ascii(f"MAE 7 j — IA {ia:.2f}{suffix} vs prévision physique {baseline:.2f}{suffix}")


def _is_finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    line = ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def render(name: str, date_str: str, mae_line: str) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    font_title = ImageFont.load_default(size=60)
    font_body = ImageFont.load_default(size=34)
    font_small = ImageFont.load_default(size=24)

    max_width = WIDTH - 2 * MARGIN
    y = 90
    for line in _wrap(draw, _ascii(name), font_title, max_width):
        draw.text((MARGIN, y), line, font=font_title, fill=FG)
        y += 72

    draw.text((MARGIN, y + 20), date_str, font=font_small, fill=MUTED)
    y += 90

    for line in _wrap(draw, mae_line, font_body, max_width):
        draw.text((MARGIN, y), line, font=font_body, fill=ACCENT)
        y += 44

    draw.text(
        (MARGIN, HEIGHT - 60),
        _ascii("Metocean AI Scoreboard — Ocean Data Consulting"),
        font=font_small,
        fill=MUTED,
    )
    return img


def _atomic_write_png(path: Path, img: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            img.save(f, format="PNG")
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def run(scores_path: Path, out_dir: Path) -> list[Path]:
    """Reads `scores_path` (`data/scores.json`), writes `<out_dir>/<id>/og.png` for each station."""
    payload = json.loads(scores_path.read_text()) if scores_path.exists() else {"stations": []}
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        stations = {s.id: s for s in load_stations()}
    except (OSError, ValueError):
        stations = {}
    written = []
    for entry in payload.get("stations", []):
        station_id = entry["id"]
        name, unit = _display_name(station_id, stations)
        img = render(name, date_str, _mae_line(entry, unit))
        path = out_dir / station_id / "og.png"
        _atomic_write_png(path, img)
        written.append(path)
    return written
