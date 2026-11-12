from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

SECTIONS = ("Build", "Resources", "Holdout")


@dataclass(frozen=True)
class Resource:
    name: str
    contested: bool


@dataclass(frozen=True)
class SpecParts:
    build: str       # includes its '## Build' heading
    resources: str
    holdout: str
    missing: tuple[str, ...] = ()


def split_spec(text: str) -> SpecParts:
    """Split by top-level '## ' headings only, never by content."""
    chunks: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^## (.+?)\s*$", line)
        if m:
            current = m.group(1) if m.group(1) in SECTIONS else None
            if current:
                chunks.setdefault(current, []).append(line)
            continue
        if current:
            chunks[current].append(line)
    join = lambda name: "\n".join(chunks.get(name, [])).rstrip() + "\n" if name in chunks else ""
    return SpecParts(join("Build"), join("Resources"), join("Holdout"),
                     tuple(s for s in SECTIONS if s not in chunks))


def parse_resources(section: str) -> list[Resource]:
    """Rows of the Resources markdown table: first cell is `name`, last cell says yes/no."""
    out = []
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        m = re.fullmatch(r"`([^`]+)`", cells[0]) if cells else None
        if m and len(cells) >= 2:
            out.append(Resource(m.group(1), "yes" in cells[-1].lower()))
    return out


def holdout_yaml(section: str) -> str:
    """Text of the first ```yaml block in the Holdout section that defines `contracts:`."""
    for block in re.findall(r"```yaml\n(.*?)```", section, flags=re.DOTALL):
        data = yaml.safe_load(block)
        if isinstance(data, dict) and "contracts" in data:
            return block
    return ""
