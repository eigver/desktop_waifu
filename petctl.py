#!/usr/bin/python
"""Command-line controller for Desktop Pet."""
from __future__ import annotations
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

APP_NAME = "desktop-pet"
APP_DIR = Path(__file__).resolve().parent
PACKS_DIR = APP_DIR / "packs"
USER_PACKS_DIR = Path.home() / ".local" / "share" / APP_NAME / "packs"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/desktop-pet-{os.getuid()}"))
SOCKET_PATH = RUNTIME_DIR / f"{APP_NAME}-{os.getuid()}.sock"
DEFAULTS = {"pack_path": str(PACKS_DIR / "slime"), "size_px": 112, "speed": 138.0, "gravity": 1180.0, "jump_power": 640.0, "hide_in_fullscreen": True, "paused": False}

HELP = f"""Desktop Pet for Hyprland

Usage:
  petctl start                         Start the pet overlay
  petctl status                        Show current settings and status
  petctl list                          List built-in and user-installed packs
  petctl load <directory|pack-name>    Load an animation pack
  petctl next                          Switch to the next installed pack
  petctl size <40..320>                Set character height in pixels
  petctl speed <20..500>               Set running speed in pixels/second
  petctl pause | resume | toggle       Pause or resume movement
  petctl hidefullscreen on|off         Hide pet while a fullscreen app is active
  petctl quit                          Close the pet
  petctl help                          Show this help

Click the character to make it jump, drag to reposition it, right-click to
switch packs, and middle-click to pause/resume. Custom packs can be anywhere.
"""


def read_config():
    cfg = DEFAULTS.copy()
    try:
        value = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(value, dict): cfg.update(value)
    except (OSError, json.JSONDecodeError):
        pass
    return cfg


def write_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def send(command):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.7)
            client.connect(str(SOCKET_PATH))
            client.sendall(json.dumps(command).encode("utf-8") + b"\n")
            data = client.recv(16384)
        return json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def pack_dirs():
    paths = []
    for root in (PACKS_DIR, USER_PACKS_DIR):
        if root.is_dir():
            for path in sorted(root.iterdir()):
                if not path.is_dir():
                    continue
                try:
                    children = list(path.iterdir())
                    has_image = any(p.is_file() and p.suffix.lower() in {".png", ".gif", ".webp", ".apng", ".bmp", ".jpg", ".jpeg"} for p in children)
                    has_state_dir = any(p.is_dir() and p.name.lower() in {"idle", "stand", "walk", "run", "jump", "fall", "land", "sleep"} for p in children)
                    meta = {}
                    try:
                        candidate = json.loads((path / "pack.json").read_text(encoding="utf-8"))
                        if isinstance(candidate, dict): meta = candidate
                    except (OSError, json.JSONDecodeError):
                        pass
                    model_value = meta.get("model")
                    model_file = (path / str(model_value)).resolve() if model_value else None
                    try:
                        model_inside = model_file is not None and model_file.is_relative_to(path.resolve())
                    except (OSError, ValueError):
                        model_inside = False
                    has_3d_model = bool(model_inside and model_file and model_file.is_file() and model_file.suffix.lower() in {".vrm", ".glb", ".gltf"})
                    if not has_3d_model and str(meta.get("type", "")).lower() in {"3d", "vrm", "glb", "gltf"}:
                        has_3d_model = any(p.is_file() and p.suffix.lower() in {".vrm", ".glb", ".gltf"} for p in children)
                    if has_image or has_state_dir or has_3d_model:
                        if path.resolve() not in paths: paths.append(path.resolve())
                except OSError:
                    continue
    return paths


def resolve_pack(value):
    p = Path(value).expanduser()
    if p.is_dir():
        return p.resolve()
    # If it is not a directory path, interpret it as a pack name.
    for candidate in pack_dirs():
        if candidate.name == value or _pack_name(candidate) == value:
            return candidate
    if not p.is_absolute():
        bundled = PACKS_DIR / value
        if bundled.is_dir(): return bundled.resolve()
    return None


def _pack_name(path):
    try:
        return str(json.loads((path / "pack.json").read_text(encoding="utf-8")).get("name", path.name))
    except (OSError, json.JSONDecodeError, AttributeError):
        return path.name


def main(argv):
    args = list(argv)
    if not args or args[0] in ("help", "-h", "--help"):
        print(HELP.rstrip()); return 0
    cmd = args.pop(0).lower()
    cfg = read_config()
    if cmd == "start":
        if send({"cmd": "ping"}):
            print("Desktop Pet is already running."); return 0
        exe = Path.home() / ".local" / "bin" / "desktop-pet"
        if not exe.exists():
            # Source-tree use is supported without installing.
            exe = APP_DIR / "desktop-pet"
        try:
            subprocess.Popen([str(exe)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            print("Starting Desktop Pet…")
            return 0
        except OSError as exc:
            print(f"Could not start: {exc}", file=sys.stderr); return 1
    if cmd == "list":
        paths = pack_dirs()
        current = str(Path(str(cfg.get("pack_path", ""))).expanduser().resolve())
        if not paths: print("No packs found.")
        for p in paths:
            flag = " *" if str(p) == current else ""
            print(f"{_pack_name(p)}{flag}\n  {p}")
        print("\n* = currently selected")
        return 0
    if cmd == "status":
        online = bool(send({"cmd": "ping"}))
        print(f"Status: {'running' if online else 'not running'}")
        print(f"Pack:   {_pack_name(Path(str(cfg.get('pack_path', ''))).expanduser())}")
        print(f"Path:   {cfg.get('pack_path')}")
        print(f"Size:   {cfg.get('size_px', 112)} px")
        print(f"Speed:  {float(cfg.get('speed', 138)):.0f} px/s")
        print(f"Motion: {'paused' if cfg.get('paused') else 'active'}")
        print(f"Hide in fullscreen: {'yes' if cfg.get('hide_in_fullscreen', True) else 'no'}")
        return 0
    payload = {"cmd": cmd}
    if cmd == "load":
        if not args: print("Usage: petctl load <directory|pack-name>", file=sys.stderr); return 2
        pack = resolve_pack(" ".join(args))
        if not pack:
            print(f"Pack not found: {' '.join(args)}\nUse petctl list or provide a directory.", file=sys.stderr); return 2
        payload["path"] = str(pack)
        cfg["pack_path"] = str(pack)
    elif cmd == "next":
        paths = pack_dirs()
        if not paths: print("No packs found.", file=sys.stderr); return 1
        current = str(Path(str(cfg.get("pack_path", ""))).expanduser().resolve())
        strings = [str(p) for p in paths]
        idx = strings.index(current) if current in strings else -1
        payload = {"cmd": "load", "path": strings[(idx + 1) % len(strings)]}
        cfg["pack_path"] = payload["path"]
    elif cmd == "size":
        if not args: print("Usage: petctl size <40..320>", file=sys.stderr); return 2
        try: value = min(800, max(40, int(args[0])))
        except ValueError: print("Size must be an integer.", file=sys.stderr); return 2
        payload["value"] = value; cfg["size_px"] = value
    elif cmd == "speed":
        if not args: print("Usage: petctl speed <20..500>", file=sys.stderr); return 2
        try: value = min(500.0, max(20.0, float(args[0])))
        except ValueError: print("Speed must be a number.", file=sys.stderr); return 2
        payload["value"] = value; cfg["speed"] = value
    elif cmd == "action":
        if not args:
            print("Usage: petctl action <animation-name>", file=sys.stderr); return 2
        payload["name"] = " ".join(args)
    elif cmd in ("pause", "resume", "toggle", "quit"):
        if cmd == "pause": cfg["paused"] = True
        elif cmd == "resume": cfg["paused"] = False
        elif cmd == "toggle": cfg["paused"] = not bool(cfg.get("paused", False))
    elif cmd == "hidefullscreen":
        if not args or args[0].lower() not in ("on", "off"):
            print("Usage: petctl hidefullscreen on|off", file=sys.stderr); return 2
        value = args[0].lower() == "on"
        payload = {"cmd": "hidefullscreen", "value": value}
        cfg["hide_in_fullscreen"] = value
    else:
        print(f"Unknown command: {cmd}\n\n{HELP.rstrip()}", file=sys.stderr); return 2

    response = send(payload)
    # If the pet isn't running, persist the chosen settings for next launch.
    if not response:
        if cmd == "action":
            print("Desktop Pet is not running. Start it and load a 3D pack first.", file=sys.stderr)
            return 1
        try: write_config(cfg)
        except OSError as exc: print(f"Could not save config: {exc}", file=sys.stderr); return 1
        if cmd == "quit": print("Desktop Pet is not running.")
        else: print("Saved for next start (Desktop Pet is not running).")
        return 0
    if response.get("ok"):
        print(response.get("message", f"{cmd} command sent"))
        return 0
    print(response.get("message", "Command failed"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))