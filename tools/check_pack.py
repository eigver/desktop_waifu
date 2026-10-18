#!/usr/bin/env python3
"""Validate a Desktop Pet 3D pack's manifest, model, and animation file paths."""
from __future__ import annotations
import json
import sys
from pathlib import Path

FILE_SUFFIXES = {".vrma", ".glb", ".gltf"}


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {Path(sys.argv[0]).name} /path/to/3d-pack", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).expanduser().resolve()
    manifest_path = root / "pack.json"
    if not manifest_path.is_file():
        print(f"ERROR: missing {manifest_path}")
        return 1
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot parse pack.json: {exc}")
        return 1
    if not isinstance(manifest, dict):
        print("ERROR: pack.json must contain a JSON object")
        return 1

    bad = False
    model = manifest.get("model")
    if not model:
        print("ERROR: pack.json has no 'model' entry")
        bad = True
    else:
        path = (root / str(model)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            print(f"ERROR: model path missing/outside pack: {model}")
            bad = True
        else:
            print(f"OK model: {path.name} ({path.stat().st_size:,} bytes)")

    animations = manifest.get("animations", {})
    if not isinstance(animations, dict) or not animations:
        print("ERROR: 'animations' must be a non-empty object")
        return 1
    print("Animations in pack.json:")
    for key, spec in animations.items():
        source = spec.get("file") or spec.get("name") if isinstance(spec, dict) else spec
        if not isinstance(source, str) or not source:
            print(f"  ERROR {key}: no file/name")
            bad = True
            continue
        if Path(source).suffix.lower() in FILE_SUFFIXES:
            path = (root / source).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                print(f"  ERROR {key}: missing {source}")
                bad = True
            else:
                print(f"  OK    {key}: {source} ({path.stat().st_size:,} bytes)")
        else:
            print(f"  INFO  {key}: embedded clip name '{source}' (not checked against model) ")
    if "twerk" not in animations:
        print("NOTE: no 'twerk' entry exists. `petctl action twerk` cannot play it until it is added to pack.json.")
        bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
