#!/usr/bin/env python3
"""Configure a Desktop Pet VRM pack to use the six animation files from the user's pack."""
from __future__ import annotations
import argparse
import json
import shutil
from pathlib import Path

ANIMATION_FILES = {
    "idle": ("animations/idle.vrma", False, True),
    "walk_start": ("animations/female_walk_start.vrma", True, True),
    "walk": ("animations/Walking.vrma", True, True),
    "turn_left": ("animations/left_turn.vrma", False, True),
    "turn_right": ("animations/right_turn.vrma", False, True),
    "jump": ("animations/jump.vrma", True, True),
    # These two are optional and are picked up when present in the pack.
    "fall": ("animations/falling.vrma", True, False),
    "twerk": ("animations/twerk.vrma", False, False),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path, help="directory containing pack.json, model and animations/")
    parser.add_argument("--front-yaw", type=float, default=None, help="model's forward-facing yaw in degrees (default: preserve pack.json, otherwise 0)")
    parser.add_argument("--side-yaw", type=float, default=None, help="signed profile yaw for rightward movement (default: preserve pack.json, otherwise 90; use -90 if she walks backwards)")
    parser.add_argument("--click-action", choices=("twerk", "jump", "none"), default="none", help="optional interaction on click; default keeps current pack setting")
    args = parser.parse_args()
    pack_dir = args.pack_dir.expanduser().resolve()
    manifest_path = pack_dir / "pack.json"
    if not manifest_path.is_file():
        parser.error(f"{manifest_path} does not exist; select your 3D character pack folder")
    try:
        meta = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"cannot read pack.json: {exc}")
    if not isinstance(meta, dict):
        parser.error("pack.json must contain a JSON object")

    missing = [rel for rel, _strip, required in ANIMATION_FILES.values()
               if required and not (pack_dir / rel).is_file()]
    if missing:
        parser.error("missing required animation files:\n  " + "\n  ".join(missing))

    backup = manifest_path.with_suffix(".json.before-desktop-pet-v4.bak")
    if not backup.exists():
        shutil.copy2(manifest_path, backup)

    meta["format"] = 2
    meta["type"] = "3d"
    meta.setdefault("name", pack_dir.name)
    meta.setdefault("scale", 1.0)
    meta.setdefault("width_ratio", 0.66)
    facing = dict(meta.get("facing")) if isinstance(meta.get("facing"), dict) else {}
    front_yaw = args.front_yaw if args.front_yaw is not None else float(facing.get("frontYawDegrees", 0.0))
    side_yaw = args.side_yaw if args.side_yaw is not None else float(facing.get("sideYawDegrees", 90.0))
    facing.update({
        "frontYawDegrees": front_yaw,
        "sideYawDegrees": side_yaw,
        "turnDuration": 0.75,
    })
    meta["facing"] = facing
    meta.setdefault("groundOffsetPixels", 8)
    meta.setdefault("groundOffsetRatio", 0.06)
    meta.setdefault("pointerLookAt", False)
    behavior = meta.get("behavior") if isinstance(meta.get("behavior"), dict) else {}
    behavior.setdefault("autoJump", False)
    meta["behavior"] = behavior
    animations = meta.get("animations") if isinstance(meta.get("animations"), dict) else {}
    for key, (rel, strip_root, required) in ANIMATION_FILES.items():
        if not (pack_dir / rel).is_file():
            continue
        animations[key] = {"file": rel, "stripRootMotion": True} if strip_root else rel
    meta["animations"] = animations
    interactions = meta.get("interactions") if isinstance(meta.get("interactions"), dict) else {}
    if args.click_action == "none":
        # Don't invent or overwrite a click interaction: the default is a deliberate click action only.
        pass
    else:
        if args.click_action == "twerk" and "twerk" not in animations:
            parser.error("--click-action twerk requires animations/twerk.vrma")
        interactions["click"] = args.click_action
    if interactions:
        meta["interactions"] = interactions
    manifest_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Configured: {manifest_path}")
    print(f"Backup:     {backup}")
    print("Animations: idle -> turn -> walk_start -> Walking; jump/fall root translation is stripped.")
    print("Optional animations preserved/loaded: falling.vrma as fall, twerk.vrma as twerk.")
    print("3D defaults: auto-jump off, pointer gaze off, feet offset uses groundOffsetPixels + groundOffsetRatio.")
    print(f"Facing:     front={front_yaw:g}°, side={side_yaw:g}°")
    if side_yaw == 90:
        print("If she still walks backwards, rerun with --side-yaw -90.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
