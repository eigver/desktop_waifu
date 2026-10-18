#!/usr/bin/python
"""A transparent, animated desktop pet for Hyprland + GTK4 Layer Shell."""
from __future__ import annotations

import json
import math
import mimetypes
import os
import random
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import http.server
from urllib.parse import unquote, urlsplit
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cairo
from PIL import Image, ImageChops, ImageOps, ImageSequence
# gtk4-layer-shell must be loaded before GDK/GTK initialize Wayland.
from ctypes import CDLL
try:
    CDLL("libgtk4-layer-shell.so")
except OSError:
    pass

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Gio, Gtk, Gtk4LayerShell  # noqa: E402

APP_NAME = "desktop-pet"
APP_DIR = Path(__file__).resolve().parent
PACKS_DIR = APP_DIR / "packs"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/desktop-pet-{os.getuid()}"))
SOCKET_PATH = RUNTIME_DIR / f"{APP_NAME}-{os.getuid()}.sock"
IMAGE_SUFFIXES = {".png", ".gif", ".webp", ".apng", ".bmp", ".jpg", ".jpeg"}

DEFAULT_CONFIG: dict[str, Any] = {
    "pack_path": str(PACKS_DIR / "slime"),
    "size_px": 112,
    "speed": 138.0,
    "gravity": 1180.0,
    "jump_power": 640.0,
    "hide_in_fullscreen": True,
    "paused": False,
}


def say_error(message: str) -> None:
    print(f"{APP_NAME}: {message}", file=sys.stderr)


def read_config() -> dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    try:
        user_cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(user_cfg, dict):
            cfg.update(user_cfg)
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as exc:
        say_error(f"could not read {CONFIG_FILE}: {exc}")
    return cfg


def write_config(cfg: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temp = CONFIG_FILE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(CONFIG_FILE)


def run_hyprctl(command: str, timeout: float = 0.45) -> Any | None:
    try:
        proc = subprocess.run(
            ["hyprctl", "-j", command],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def make_socket_path() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    try:
        RUNTIME_DIR.chmod(0o700)
    except OSError:
        pass


def ask_running(command: dict[str, Any] | None = None, timeout: float = 0.5) -> dict[str, Any] | None:
    payload = command or {"cmd": "ping"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(SOCKET_PATH))
            client.sendall(json.dumps(payload).encode("utf-8") + b"\n")
            data = b""
            while b"\n" not in data and len(data) < 16384:
                chunk = client.recv(4096)
                if not chunk:
                    break
                data += chunk
            if data:
                return json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return None


@dataclass
class Frame:
    surface: cairo.ImageSurface
    width: int
    height: int
    duration: float
    _buffer: bytearray


class PetPack:
    """Loads an animation pack from state directories and/or animated files."""

    STATES = ("idle", "walk", "jump", "fall", "land", "sleep")
    ALIASES = {
        "idle": ("idle", "stand", "standing", "rest"),
        "walk": ("walk", "walking", "run", "running", "move"),
        "jump": ("jump", "jumping", "hop"),
        "fall": ("fall", "falling", "drop"),
        "land": ("land", "landing"),
        "sleep": ("sleep", "sleeping", "nap"),
    }

    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        if not self.path.is_dir():
            raise ValueError(f"animation pack is not a directory: {self.path}")
        self.name = self.path.name
        self.turn_duration = 0.0
        try:
            meta = json.loads((self.path / "pack.json").read_text(encoding="utf-8"))
            if isinstance(meta, dict) and meta.get("name"):
                self.name = str(meta["name"])
        except (OSError, json.JSONDecodeError):
            pass

        source: dict[str, list[tuple[Image.Image, float]]] = {state: [] for state in self.STATES}
        total_frames = 0
        # Explicit state folders (walk/000.png) are easiest for hand-made sprite packs.
        for child in sorted(self.path.iterdir()):
            if child.is_dir():
                state = self._state_from_name(child.name)
                if state is None:
                    continue
                files = sorted(p for p in child.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
                for file in files[:180]:
                    try:
                        image = Image.open(file)
                        image.seek(0)
                        source[state].append((ImageOps.exif_transpose(image.copy()).convert("RGBA"), 0.10))
                        total_frames += 1
                    except (OSError, ValueError):
                        continue
            elif child.is_file() and child.suffix.lower() in IMAGE_SUFFIXES:
                state = self._state_from_name(child.stem)
                if state is None:
                    continue
                try:
                    image = Image.open(child)
                    frame_count = min(getattr(image, "n_frames", 1), 180)
                    for idx in range(frame_count):
                        image.seek(idx)
                        frame = ImageOps.exif_transpose(image.copy()).convert("RGBA")
                        duration = max(0.04, float(image.info.get("duration", 100)) / 1000.0)
                        source[state].append((frame, duration))
                        total_frames += 1
                except (OSError, ValueError):
                    continue
            if total_frames >= 700:
                break

        # Support a simple folder containing one pet.png / sprite.png / character.gif.
        if not any(source.values()):
            for file in sorted(self.path.iterdir()):
                if file.is_file() and file.suffix.lower() in IMAGE_SUFFIXES:
                    try:
                        image = Image.open(file)
                        for idx in range(min(getattr(image, "n_frames", 1), 120)):
                            image.seek(idx)
                            frame = ImageOps.exif_transpose(image.copy()).convert("RGBA")
                            duration = max(0.04, float(image.info.get("duration", 100)) / 1000.0)
                            source["idle"].append((frame, duration))
                        break
                    except (OSError, ValueError):
                        continue

        if not any(source.values()):
            raise ValueError(f"no supported images found in {self.path}")

        # Any animation not provided falls back to the closest available state.
        if not source["idle"]:
            source["idle"] = source["walk"][:] or source["land"][:] or next(v for v in source.values() if v)
        fallbacks = {"walk": "idle", "jump": "walk", "fall": "jump", "land": "idle", "sleep": "idle"}
        for state, fallback in fallbacks.items():
            if not source[state]:
                source[state] = source[fallback][:]

        all_sizes = [(im.width, im.height) for seq in source.values() for im, _ in seq]
        canvas_w = max(w for w, _ in all_sizes)
        canvas_h = max(h for _, h in all_sizes)
        if max(canvas_w, canvas_h) > 512:
            ratio = 512 / max(canvas_w, canvas_h)
            canvas_w = max(1, int(canvas_w * ratio))
            canvas_h = max(1, int(canvas_h * ratio))
        self.canvas_width = canvas_w
        self.canvas_height = canvas_h
        self.ratio = canvas_w / max(1, canvas_h)
        self.frames: dict[str, list[Frame]] = {}
        for state, sequence in source.items():
            made: list[Frame] = []
            for img, duration in sequence:
                img = ImageOps.contain(img, (canvas_w, canvas_h), method=Image.Resampling.LANCZOS)
                canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
                canvas.alpha_composite(img, ((canvas_w - img.width) // 2, canvas_h - img.height))
                made.append(self._to_cairo(canvas, duration))
            if made:
                self.frames[state] = made
        # If the user supplied a minimal pack with only one state, retain all aliases.
        for state in self.STATES:
            if state not in self.frames:
                self.frames[state] = self.frames["idle"]

    @classmethod
    def _state_from_name(cls, name: str) -> str | None:
        lowered = name.lower().replace("-", "_").replace(" ", "_")
        for state, aliases in cls.ALIASES.items():
            if lowered in aliases:
                return state
        if lowered in {"pet", "sprite", "character", "avatar", "model"}:
            return "idle"
        return None

    @staticmethod
    def _to_cairo(image: Image.Image, duration: float) -> Frame:
        rgba = image.convert("RGBA")
        red, green, blue, alpha = rgba.split()
        # Cairo ARGB32 is premultiplied BGRA in memory on little-endian systems.
        red = ImageChops.multiply(red, alpha)
        green = ImageChops.multiply(green, alpha)
        blue = ImageChops.multiply(blue, alpha)
        bgra = Image.merge("RGBA", (blue, green, red, alpha))
        buf = bytearray(bgra.tobytes())
        w, h = image.size
        surface = cairo.ImageSurface.create_for_data(buf, cairo.FORMAT_ARGB32, w, h, w * 4)
        return Frame(surface, w, h, duration, buf)

    def get_frames(self, state: str) -> list[Frame]:
        return self.frames.get(state, self.frames["idle"])



class ModelPack:
    """A 3D character pack backed by VRM/GLB/GLTF and optional VRMA animations."""
    is_3d = True
    MODEL_SUFFIXES = {".vrm", ".glb", ".gltf"}

    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        if not self.path.is_dir():
            raise ValueError(f"3D pack is not a directory: {self.path}")
        meta_path = self.path / "pack.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"3D pack needs a valid pack.json: {exc}") from exc
        if not isinstance(meta, dict):
            raise ValueError("pack.json must contain a JSON object")
        model = meta.get("model")
        if not model:
            candidates = sorted(p for p in self.path.iterdir() if p.is_file() and p.suffix.lower() in self.MODEL_SUFFIXES)
            if len(candidates) != 1:
                raise ValueError("set 'model' in pack.json, or put exactly one .vrm/.glb/.gltf file in the pack root")
            model = candidates[0].name
        self.model_path = (self.path / str(model)).resolve()
        try:
            self.model_path.relative_to(self.path)
        except ValueError as exc:
            raise ValueError("model path must stay inside the pack folder") from exc
        if not self.model_path.is_file() or self.model_path.suffix.lower() not in self.MODEL_SUFFIXES:
            raise ValueError(f"model not found or unsupported: {self.model_path}")
        self.model_rel = self.model_path.relative_to(self.path).as_posix()
        self.meta = meta
        self.name = str(meta.get("name") or self.path.name)
        try:
            self.ratio = min(1.5, max(0.25, float(meta.get("width_ratio", 0.66))))
        except (TypeError, ValueError):
            self.ratio = 0.66
        facing = meta.get("facing", {})
        try:
            self.turn_duration = float(facing.get("turnDuration", 0.75))
        except (TypeError, ValueError):
            self.turn_duration = 0.75
        animations = meta.get("animations", {})
        self.animations = animations if isinstance(animations, dict) else {}
        interactions = meta.get("interactions", {})
        self.interactions = interactions if isinstance(interactions, dict) else {}

    def get_frames(self, state: str) -> list[Frame]:
        return []


def load_character_pack(path: Path) -> PetPack | ModelPack:
    """Recognize a 3D pack by its manifest; otherwise load a classic sprite pack."""
    path = path.expanduser().resolve()
    manifest = path / "pack.json"
    if manifest.is_file():
        try:
            meta = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(meta, dict) and (
                str(meta.get("type", "")).lower() in {"3d", "vrm", "glb", "gltf"}
                or bool(meta.get("model"))
            ):
                return ModelPack(path)
        except (OSError, json.JSONDecodeError):
            pass
    return PetPack(path)

def pack_folders() -> list[Path]:
    found: list[Path] = []
    roots = [PACKS_DIR, Path.home() / ".local" / "share" / APP_NAME / "packs"]
    for root in roots:
        if root.is_dir():
            for path in sorted(root.iterdir()):
                if not path.is_dir():
                    continue
                try:
                    pack = load_character_pack(path)
                except (OSError, ValueError):
                    continue
                is_usable = bool(getattr(pack, "is_3d", False)) or any(
                    child.is_dir() and PetPack._state_from_name(child.name)
                    or child.is_file() and child.suffix.lower() in IMAGE_SUFFIXES
                    for child in path.iterdir()
                )
                if is_usable:
                    resolved = path.resolve()
                    if resolved not in found:
                        found.append(resolved)
    return found


class SocketHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            raw = self.rfile.readline(16384)
            payload = json.loads(raw.decode("utf-8")) if raw else {"cmd": "ping"}
            command = str(payload.get("cmd", "ping"))
            if command == "ping":
                response = {"ok": True, "running": True, "app": APP_NAME}
            else:
                GLib.idle_add(self.server.pet_app.execute_command, payload)  # type: ignore[attr-defined]
                response = {"ok": True, "queued": True, "message": f"{command} command accepted"}
            self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")
        except (OSError, ValueError, TypeError):
            try:
                self.wfile.write(b'{"ok":false,"message":"invalid command"}\n')
            except OSError:
                pass


class ThreadedUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True



class ViewerHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Loopback-only web server used by the transparent WebKit 3D canvas."""
    server_version = "DesktopPetViewer/1.0"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:  # noqa: N802
        app: DesktopPet = self.server.pet_app  # type: ignore[attr-defined]
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if path in ("/", "/viewer3d.html"):
            try:
                body = (APP_DIR / "viewer3d.html").read_bytes()
            except OSError:
                self._send(500, b"viewer3d.html missing", "text/plain; charset=utf-8")
                return
            self._send(200, body, "text/html; charset=utf-8")
            return
        if path == "/state":
            body = json.dumps(app.viewer_state(), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path == "/pack.json":
            pack = app.pack
            if not getattr(pack, "is_3d", False):
                self._send(409, b"current pack is not 3D", "text/plain; charset=utf-8")
                return
            manifest = dict(pack.meta)
            manifest["model"] = pack.model_rel
            body = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        prefix = "/packfiles/"
        if path.startswith(prefix):
            pack = app.pack
            if not getattr(pack, "is_3d", False):
                self._send(409, b"current pack is not 3D", "text/plain; charset=utf-8")
                return
            rel = path[len(prefix):]
            target = (pack.path / rel).resolve()
            try:
                target.relative_to(pack.path)
            except ValueError:
                self._send(403, b"path outside model pack", "text/plain; charset=utf-8")
                return
            if not target.is_file():
                self._send(404, b"pack file not found", "text/plain; charset=utf-8")
                return
            try:
                body = target.read_bytes()
            except OSError:
                self._send(500, b"unable to read pack file", "text/plain; charset=utf-8")
                return
            mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, body, mime)
            return
            
        # --- Раздача локальных библиотек из папки vendor ---
        if path.startswith("/vendor/"):
            target = (APP_DIR / path.lstrip("/")).resolve()
            try:
                target.relative_to(APP_DIR)
            except ValueError:
                self._send(403, b"forbidden", "text/plain; charset=utf-8")
                return
            if not target.is_file():
                self._send(404, b"vendor file not found", "text/plain; charset=utf-8")
                return
            try:
                body = target.read_bytes()
            except OSError:
                self._send(500, b"unable to read vendor file", "text/plain; charset=utf-8")
                return
            
            # Принудительно задаём правильные MIME-типы для JS-модулей
            if target.suffix.lower() in ('.js', '.mjs'):
                mime = 'application/javascript'
            else:
                mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            
            self._send(200, body, mime)
            return
        # -------------------------------------------------------------

        self._send(404, b"not found", "text/plain; charset=utf-8")

class DesktopPet:
    def __init__(self, app: Gtk.Application):
        self.app = app
        self.cfg = read_config()
        self.window: Gtk.ApplicationWindow | None = None
        self.area: Gtk.Widget | None = None
        self.webview: Any | None = None
        self.viewer_http: http.server.ThreadingHTTPServer | None = None
        self.viewer_port: int | None = None
        self.viewer_interaction_seq = 0
        self.viewer_interaction = ""
        self.viewer_pointer_x = 0.0
        self.viewer_pointer_y = 0.0
        self.viewer_pointer_active = False
        self.server: ThreadedUnixServer | None = None
        self.server_thread: threading.Thread | None = None
        self.gdk_monitor: Gdk.Monitor | None = None
        self.monitor_name: str | None = None
        self.monitor_index = 0
        self.monitor_x = 0
        self.monitor_y = 0
        self.monitor_width = 0
        self.monitor_height = 0
        self.screen_w = 0
        self.screen_h = 0
        self.spawned = False
        self.platforms: list[dict[str, Any]] = []
        self.platforms_checked = 0.0
        self.suppressed = False
        self.paused = bool(self.cfg.get("paused", False))
        self.pack = self._load_pack_or_default(Path(str(self.cfg.get("pack_path", DEFAULT_CONFIG["pack_path"]))))
        self.size_px = int(self.cfg.get("size_px", 112))
        self.speed = float(self.cfg.get("speed", 138.0))
        self.gravity = float(self.cfg.get("gravity", 1180.0))
        self.jump_power = float(self.cfg.get("jump_power", 640.0))
        self.x = 240.0
        self.y = 0.0
        self.vx = self.speed
        self.vy = 0.0
        self.direction = 1
        self.grounded = True
        self.support_id = "floor"
        self.last_tick = time.monotonic()
        self.state = "idle"
        self.frame_index = 0
        self.frame_elapsed = 0.0
        self.land_until = 0.0
        self.jump_timer = random.uniform(10.0, 18.0)
        self.idle_until = 0.0
        # Short wander bursts separated by longer rests; less frantic as a default.
        self.next_idle_at = time.monotonic() + random.uniform(2.0, 4.0)
        self.jump_windup_until = 0.0
        self.pending_jump_vy = 0.0
        self.pending_jump_vx = 0.0
        self.turn_windup_until = 0.0  # <--- Добавлено для синхронизации поворота
        self.pointer_button = 0
        self.speech = ""
        self.speech_until = 0.0
        self.pointer_down = False
        self.dragging = False
        self.press_x = 0.0
        self.press_y = 0.0
        self.drag_dx = 0.0
        self.drag_dy = 0.0
        self.last_pointer_x = 0.0
        self.last_pointer_y = 0.0
        self.last_region: tuple[int, int, int, int] | None = None

    def _load_pack_or_default(self, path: Path) -> PetPack | ModelPack:
        try:
            return load_character_pack(path)
        except (OSError, ValueError) as exc:
            default_path = PACKS_DIR / "slime"
            say_error(f"could not load pack {path}: {exc}; using built-in slime")
            return PetPack(default_path)

    def _save_settings(self) -> None:
        self.cfg.update({
            "pack_path": str(self.pack.path),
            "size_px": int(self.size_px),
            "speed": float(self.speed),
            "gravity": float(self.gravity),
            "jump_power": float(self.jump_power),
            "hide_in_fullscreen": bool(self.cfg.get("hide_in_fullscreen", True)),
            "paused": bool(self.paused),
        })
        try:
            write_config(self.cfg)
        except OSError as exc:
            say_error(f"could not save settings: {exc}")

    def _select_monitor(self) -> None:
        display = Gdk.Display.get_default()
        monitors = display.get_monitors() if display else None
        if not monitors or monitors.get_n_items() == 0:
            raise RuntimeError("GTK could not find a monitor")
        hypr_monitors = run_hyprctl("monitors") or []
        focused = next((m for m in hypr_monitors if m.get("focused")), None)
        wanted_name = focused.get("name") if focused else None
        selected = None
        for idx in range(monitors.get_n_items()):
            mon = monitors.get_item(idx)
            connector = mon.get_connector() if hasattr(mon, "get_connector") else None
            model = mon.get_model() if hasattr(mon, "get_model") else None
            if wanted_name and wanted_name in (connector, model):
                selected = mon
                break
        if selected is None:
            selected = monitors.get_item(0)
        self.gdk_monitor = selected
        connector = selected.get_connector() if hasattr(selected, "get_connector") else None
        selected_name = connector or (selected.get_model() if hasattr(selected, "get_model") else None)
        hypr_match = next((m for m in hypr_monitors if m.get("name") == selected_name), None)
        if hypr_match is None and focused:
            hypr_match = focused
        if hypr_match is None and hypr_monitors:
            hypr_match = hypr_monitors[0]
        if hypr_match:
            self.monitor_name = str(hypr_match.get("name", selected_name or ""))
            self.monitor_index = int(hypr_match.get("id", 0))
            self.monitor_x = int(hypr_match.get("x", 0))
            self.monitor_y = int(hypr_match.get("y", 0))
            self.monitor_width = int(hypr_match.get("width", 0))
            self.monitor_height = int(hypr_match.get("height", 0))

    def on_activate(self, _app: Gtk.Application) -> None:
        if not Gtk4LayerShell.is_supported():
            say_error("gtk4-layer-shell is not supported by this session. Start the app inside a Wayland session with layer-shell support.")
            self.app.quit()
            return

        self._select_monitor()
        # GTK was previously allowed to create a zero-sized layer surface. The
        # four anchors alone did not reliably force a full-screen child size.
        if self.gdk_monitor is not None:
            try:
                geometry = self.gdk_monitor.get_geometry()
                if self.monitor_width <= 0:
                    self.monitor_width = int(geometry.width)
                if self.monitor_height <= 0:
                    self.monitor_height = int(geometry.height)
            except Exception:
                pass
        self.screen_w = max(1, int(self.monitor_width or 1920))
        self.screen_h = max(1, int(self.monitor_height or 1080))
        if not self.spawned:
            _pw, ph = self.dimensions()
            self.x = max(20.0, self.screen_w * 0.27)
            self.y = self.screen_h - ph
            self.spawned = True

        window = Gtk.ApplicationWindow(application=self.app)
        window.set_title("Desktop Pet")
        window.set_decorated(False)
        window.set_resizable(False)
        window.set_focusable(False)
        window.set_default_size(self.screen_w, self.screen_h)
        window.set_size_request(self.screen_w, self.screen_h)
        Gtk4LayerShell.init_for_window(window)
        Gtk4LayerShell.set_namespace(window, "desktop-pet")
        Gtk4LayerShell.set_layer(window, Gtk4LayerShell.Layer.OVERLAY)
        if self.gdk_monitor is not None:
            Gtk4LayerShell.set_monitor(window, self.gdk_monitor)
        for edge in (Gtk4LayerShell.Edge.TOP, Gtk4LayerShell.Edge.BOTTOM, Gtk4LayerShell.Edge.LEFT, Gtk4LayerShell.Edge.RIGHT):
            Gtk4LayerShell.set_anchor(window, edge, True)
        Gtk4LayerShell.set_exclusive_zone(window, 0)
        Gtk4LayerShell.set_keyboard_mode(window, Gtk4LayerShell.KeyboardMode.NONE)

        css = Gtk.CssProvider()
        css.load_from_data(b"window, drawingarea, webview { background-color: rgba(0,0,0,0); }\n")
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = window
        try:
            self._replace_content_for_pack()
        except RuntimeError as exc:
            say_error(str(exc))
            self.app.quit()
            return

        # Attach controllers to the full-screen top-level widget so changing
        # between a DrawingArea and WebKit WebView does not disconnect them.
        click = Gtk.GestureClick.new()
        click.set_button(0)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self.on_pressed)
        click.connect("released", self.on_released)
        window.add_controller(click)
        motion = Gtk.EventControllerMotion.new()
        motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        motion.connect("motion", self.on_motion)
        motion.connect("leave", self.on_leave)
        window.add_controller(motion)

        window.present()
        self._start_socket_server()
        GLib.timeout_add(16, self.tick)
        GLib.timeout_add(420, self.refresh_platforms)
        self.show_bubble(f"{self.pack.name} loaded — right-click to switch", 3.2)

    def _start_viewer_server(self) -> None:
        if self.viewer_http:
            return
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ViewerHTTPHandler)
        server.daemon_threads = True
        server.pet_app = self  # type: ignore[attr-defined]
        self.viewer_http = server
        self.viewer_port = int(server.server_address[1])
        threading.Thread(target=server.serve_forever, daemon=True, name="desktop-pet-3d-http").start()

    def _create_widget_for_pack(self) -> Gtk.Widget:
        if getattr(self.pack, "is_3d", False):
            try:
                gi.require_version("WebKit", "6.0")
                from gi.repository import WebKit
            except (ValueError, ImportError) as exc:
                raise RuntimeError("3D packs need WebKitGTK 6.0. Install it with: sudo pacman -S --needed webkitgtk-6.0") from exc
            self._start_viewer_server()
            view = WebKit.WebView()
            view.set_hexpand(True)
            view.set_vexpand(True)
            view.set_focusable(False)
            if hasattr(view, "set_background_color"):
                transparent = Gdk.RGBA()
                transparent.parse("rgba(0,0,0,0)")
                view.set_background_color(transparent)
            view.load_uri(f"http://127.0.0.1:{self.viewer_port}/")
            self.webview = view
            self.area = view
            return view

        area = Gtk.DrawingArea()
        area.set_content_width(self.screen_w)
        area.set_content_height(self.screen_h)
        area.set_size_request(self.screen_w, self.screen_h)
        area.set_hexpand(True)
        area.set_vexpand(True)
        area.set_draw_func(self.on_draw)
        self.webview = None
        self.area = area
        return area

    def _replace_content_for_pack(self) -> None:
        if not self.window:
            return
        widget = self._create_widget_for_pack()
        self.window.set_child(widget)
        self.last_region = None
        if isinstance(widget, Gtk.DrawingArea):
            widget.queue_draw()

    def viewer_state(self) -> dict[str, Any]:
        pet_w, pet_h = self.dimensions()
        return {
            "x": float(self.x), "y": float(self.y),
            "w": float(pet_w), "h": float(pet_h),
            "screenW": int(self.screen_w), "screenH": int(self.screen_h),
            "direction": int(self.direction), "state": str(self.state),
            "paused": bool(self.paused), "hidden": bool(self.suppressed),
            "pointerX": float(self.viewer_pointer_x), "pointerY": float(self.viewer_pointer_y),
            "pointerActive": bool(self.viewer_pointer_active),
            "interactionSeq": int(self.viewer_interaction_seq),
            "interaction": str(self.viewer_interaction),
            "speech": self.speech if time.monotonic() < self.speech_until else "",
        }

    def _trigger_3d_event(self, event: str, x: float | None = None, y: float | None = None) -> bool:
        if not getattr(self.pack, "is_3d", False):
            return False
        interactions = getattr(self.pack, "interactions", {})
        target = interactions.get(event)
        if not target:
            return False
        self.viewer_interaction = str(event)
        self.viewer_interaction_seq += 1
        if x is not None:
            self.viewer_pointer_x = float(x)
        if y is not None:
            self.viewer_pointer_y = float(y)
        self.viewer_pointer_active = True
        return True
    def _start_socket_server(self) -> None:
        make_socket_path()
        if SOCKET_PATH.exists():
            if ask_running():
                say_error("another instance is already running; use `petctl` to control it")
                self.app.quit()
                return
            try:
                SOCKET_PATH.unlink()
            except OSError:
                pass
        try:
            server = ThreadedUnixServer(str(SOCKET_PATH), SocketHandler)
            server.pet_app = self  # type: ignore[attr-defined]
            os.chmod(SOCKET_PATH, 0o600)
            self.server = server
            self.server_thread = threading.Thread(target=server.serve_forever, daemon=True, name="desktop-pet-control")
            self.server_thread.start()
        except OSError as exc:
            say_error(f"could not start control socket: {exc}")

    def on_shutdown(self, _app: Gtk.Application) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.viewer_http:
            self.viewer_http.shutdown()
            self.viewer_http.server_close()
        try:
            SOCKET_PATH.unlink(missing_ok=True)
        except OSError:
            pass

    def dimensions(self) -> tuple[float, float]:
        return max(1.0, self.size_px * self.pack.ratio), float(max(1, self.size_px))

    def show_bubble(self, text: str, seconds: float = 2.5) -> None:
        self.speech = text[:86]
        self.speech_until = time.monotonic() + seconds
        if self.area:
            self.area.queue_draw()

    def _local_active_workspace(self, monitor: dict[str, Any]) -> int | None:
        active = monitor.get("activeWorkspace") or {}
        try:
            return int(active.get("id"))
        except (TypeError, ValueError):
            return None

    def refresh_platforms(self) -> bool:
        if not self.window:
            return True
        now = time.monotonic()
        if not self.screen_w or not self.screen_h:
            return True
        monitors = run_hyprctl("monitors") or []
        clients = run_hyprctl("clients") or []
        current_monitor = next((m for m in monitors if int(m.get("id", -1)) == self.monitor_index), None)
        if current_monitor is None and self.monitor_name:
            current_monitor = next((m for m in monitors if m.get("name") == self.monitor_name), None)
        if current_monitor:
            self.monitor_x = int(current_monitor.get("x", self.monitor_x))
            self.monitor_y = int(current_monitor.get("y", self.monitor_y))
            self.monitor_width = int(current_monitor.get("width", self.monitor_width or self.screen_w))
            self.monitor_height = int(current_monitor.get("height", self.monitor_height or self.screen_h))
            self.monitor_index = int(current_monitor.get("id", self.monitor_index))
            workspace_id = self._local_active_workspace(current_monitor)
            self.suppressed = False
        else:
            workspace_id = None

        active_window = run_hyprctl("activewindow") or {}
        if self.cfg.get("hide_in_fullscreen", True):
            try:
                active_monitor_id = int(active_window.get("monitor", self.monitor_index))
            except (TypeError, ValueError):
                active_monitor_id = self.monitor_index
            self.suppressed = bool(active_window.get("fullscreen")) and active_monitor_id == self.monitor_index

        platforms: list[dict[str, Any]] = [{"id": "floor", "x1": 0.0, "x2": float(self.screen_w), "top": float(self.screen_h)}]
        if current_monitor is not None and workspace_id is not None and not self.suppressed:
            for client in clients:
                if not client.get("mapped", True) or client.get("hidden", False) or client.get("fullscreen", False):
                    continue
                try:
                    client_monitor = int(client.get("monitor", self.monitor_index))
                    client_ws = int((client.get("workspace") or {}).get("id", -1))
                except (TypeError, ValueError):
                    continue
                if client_monitor != self.monitor_index:
                    continue
                if client_ws != workspace_id and not client.get("pinned", False):
                    continue
                at = client.get("at") or [0, 0]
                size = client.get("size") or [0, 0]
                if len(at) < 2 or len(size) < 2:
                    continue
                try:
                    x1 = float(at[0]) - self.monitor_x
                    top = float(at[1]) - self.monitor_y
                    width = float(size[0])
                    height = float(size[1])
                except (TypeError, ValueError):
                    continue
                if width < 80 or height < 30 or top < 0 or top > self.screen_h - 5:
                    continue
                local_x = x1
                x1 = max(0.0, local_x)
                x2 = min(float(self.screen_w), local_x + width)
                if x2 - x1 < 60:
                    continue
                address = str(client.get("address", f"{x1}-{top}"))
                platforms.append({"id": address, "x1": x1, "x2": x2, "top": max(0.0, top)})
        self.platforms = sorted(platforms, key=lambda p: p["top"])
        self.platforms_checked = now
        if self.grounded and self.support_id not in {p["id"] for p in self.platforms}:
            self.grounded = False
            self.support_id = ""
        return True

    def on_draw(self, _area: Gtk.DrawingArea, cr: cairo.Context, width: int, height: int, _data: Any = None) -> None:
        if getattr(self.pack, "is_3d", False):
            return
        # Explicitly clear the whole surface so the overlay stays transparent.
        cr.save()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.restore()
        self.screen_w = max(1, width)
        self.screen_h = max(1, height)
        if not self.spawned and self.grounded:
            _pw, ph = self.dimensions()
            self.x = max(20.0, self.screen_w * 0.27)
            self.y = self.screen_h - ph
            self.spawned = True
        if self.suppressed:
            self._update_input_region()
            return

        pet_w, pet_h = self.dimensions()
        if self.grounded:
            # Small grounding shadow; it remains subtle on transparent desktop backgrounds.
            cr.save()
            cr.set_source_rgba(0.02, 0.04, 0.05, 0.20)
            cr.translate(self.x + pet_w * 0.5, self.y + pet_h - 1)
            cr.scale(max(8, pet_w * 0.29), max(2, pet_h * 0.035))
            cr.arc(0, 0, 1, 0, math.tau)
            cr.fill()
            cr.restore()

        frames = self.pack.get_frames(self.state)
        frame = frames[min(self.frame_index, len(frames) - 1)]
        cr.save()
        if self.direction < 0:
            cr.translate(self.x + pet_w, self.y)
            cr.scale(-1, 1)
            cr.scale(pet_w / frame.width, pet_h / frame.height)
            cr.set_source_surface(frame.surface, 0, 0)
        else:
            cr.translate(self.x, self.y)
            cr.scale(pet_w / frame.width, pet_h / frame.height)
            cr.set_source_surface(frame.surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_BILINEAR)
        cr.paint()
        cr.restore()

        if self.speech and time.monotonic() < self.speech_until:
            self._draw_bubble(cr, width, height, self.speech)
        self._update_input_region()

    def _draw_bubble(self, cr: cairo.Context, width: int, height: int, text: str) -> None:
        pet_w, _pet_h = self.dimensions()
        x = min(max(8.0, self.x + pet_w / 2 - 104), max(8.0, width - 216))
        y = max(8.0, self.y - 49)
        cr.save()
        cr.set_source_rgba(20 / 255, 22 / 255, 31 / 255, 0.94)
        cr.arc(x + 12, y + 7, 7, math.pi / 2, math.pi * 1.5)
        cr.arc(x + 204, y + 7, 7, -math.pi / 2, math.pi / 2)
        cr.close_path(); cr.fill()
        cr.rectangle(x + 12, y, 192, 14); cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.96)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(12)
        # Keep the bubble readable and clipped to its width.
        shown = text
        while cr.text_extents(shown).width > 190 and len(shown) > 8:
            shown = shown[:-4] + "…"
        cr.move_to(x + 12, y + 18)
        cr.show_text(shown)
        cr.restore()

    def _update_input_region(self) -> None:
        if not self.window:
            return
        surface = self.window.get_surface()
        if surface is None:
            return
        if self.suppressed or self.screen_w < 1 or self.screen_h < 1:
            rect = (0, 0, 0, 0)
        else:
            pet_w, pet_h = self.dimensions()
            # Slightly inset hit target means transparent pixels around the sprite remain click-through.
            pad_x = max(2, int(pet_w * 0.12))
            pad_y = max(2, int(pet_h * 0.10))
            x = max(0, int(self.x + pad_x))
            y = max(0, int(self.y + pad_y))
            w = max(1, min(self.screen_w - x, int(pet_w - 2 * pad_x)))
            h = max(1, min(self.screen_h - y, int(pet_h - 2 * pad_y)))
            rect = (x, y, w, h)
        if rect == self.last_region:
            return
        self.last_region = rect
        try:
            region = cairo.Region(cairo.RectangleInt(*rect)) if rect[2] > 0 and rect[3] > 0 else cairo.Region()
            surface.set_input_region(region)
        except (AttributeError, TypeError, cairo.Error) as exc:
            # Keep the app usable if input shaping isn't supported by an unusual GDK backend.
            say_error(f"input-region update failed (click-through may be unavailable): {exc}")

    def on_pressed(self, gesture: Gtk.GestureClick, _n_press: int, x: float, y: float) -> None:
        button = gesture.get_current_button()
        self.pointer_button = button
        # Only the left mouse button starts a drag. Previously all button presses
        # armed dragging and a missed WebView release could leave it latched.
        self.pointer_down = button == 1
        self.dragging = False
        self.press_x, self.press_y = x, y
        self.last_pointer_x, self.last_pointer_y = x, y
        self.drag_dx = x - self.x
        self.drag_dy = y - self.y
        if button == 3:
            self._cycle_pack()
            self.pointer_down = False
        elif button == 2:
            self.paused = not self.paused
            self.cfg["paused"] = self.paused
            self._save_settings()
            self.show_bubble("paused" if self.paused else "unpaused")
            self.pointer_down = False

    def on_motion(self, _controller: Gtk.EventControllerMotion, x: float, y: float) -> None:
        self.last_pointer_x, self.last_pointer_y = x, y
        if getattr(self.pack, "is_3d", False):
            self.viewer_pointer_x, self.viewer_pointer_y = float(x), float(y)
            self.viewer_pointer_active = True
        # WebKit sometimes consumes a release before Gtk.GestureClick sees it.
        # If GTK reports no primary-button mask on a later motion, clear the stale
        # press rather than dragging the pet just because the cursor passed over it.
        if self.pointer_down:
            try:
                event_state = _controller.get_current_event_state()
                button1_mask = getattr(Gdk.ModifierType, "BUTTON1_MASK", 0)
                if button1_mask and not (event_state & button1_mask):
                    self.pointer_down = False
                    self.dragging = False
                    self.pointer_button = 0
            except (AttributeError, TypeError):
                pass
        if not self.pointer_down:
            return
        if not self.dragging and math.hypot(x - self.press_x, y - self.press_y) > 5:
            self.dragging = True
            self.grounded = False
            self.support_id = ""
        if self.dragging:
            pet_w, pet_h = self.dimensions()
            self.x = min(max(-pet_w * 0.15, x - self.drag_dx), self.screen_w - pet_w * 0.85)
            self.y = min(max(0.0, y - self.drag_dy), self.screen_h - pet_h)
            self.vx = 0.0
            self.vy = 0.0
            if self.area:
                self.area.queue_draw()

    def on_released(self, gesture: Gtk.GestureClick, _n_press: int, x: float, y: float) -> None:
        button = gesture.get_current_button() or self.pointer_button
        was_dragging = self.dragging
        self.pointer_down = False
        self.pointer_button = 0
        self.dragging = False
        if was_dragging:
            self.vx = self.direction * self.speed
            self.vy = 0.0
            self.grounded = False
            self.support_id = ""
        elif button == 1 and not self.paused:
            if getattr(self.pack, "is_3d", False) and self._trigger_3d_event("click", x, y):
                pass
            else:
                self._jump(manual=True)

    def on_leave(self, _controller: Gtk.EventControllerMotion) -> None:
        self.viewer_pointer_active = False

    def _jump(self, manual: bool = False) -> None:
        # Start the jump animation while still grounded, then apply the impulse.
        # This gives clips with a crouch/take-off pose time to prepare before the
        # whole model begins moving upward, instead of producing a jump-in-a-jump.
        now = time.monotonic()
        self.jump_windup_until = now + (0.18 if getattr(self.pack, "is_3d", False) else 0.12)
        self.pending_jump_vy = -self.jump_power * random.uniform(0.90, 1.04)
        self.pending_jump_vx = self.direction * self.speed * random.uniform(0.85, 1.05)
        self.vx = 0.0
        self.vy = 0.0
        self.jump_timer = random.uniform(10.0, 18.0) if manual else random.uniform(10.0, 18.0)
        self.idle_until = 0.0
        self._set_state("jump")

    def _cycle_pack(self) -> None:
        candidates = pack_folders()
        if not candidates:
            self.show_bubble("petctl load /path/to/animation-pack")
            return
        paths = [str(path) for path in candidates]
        current = str(self.pack.path)
        if current not in paths:
            idx = -1
        else:
            idx = paths.index(current)
        self.execute_command({"cmd": "load", "path": paths[(idx + 1) % len(paths)]})

    def execute_command(self, payload: dict[str, Any]) -> bool:
        cmd = str(payload.get("cmd", "")).lower()
        try:
            if cmd == "load":
                path_text = str(payload.get("path", "")).strip()
                if not path_text:
                    self.show_bubble("usage: petctl load /path/to/pack")
                else:
                    path = Path(path_text).expanduser()
                    if not path.is_absolute():
                        # A plain name can refer to one of the built-in packs.
                        path = PACKS_DIR / path
                    new_pack = load_character_pack(path)
                    old_is_3d = bool(getattr(self.pack, "is_3d", False))
                    new_is_3d = bool(getattr(new_pack, "is_3d", False))
                    previous_pack = self.pack
                    self.pack = new_pack
                    try:
                        if self.window and old_is_3d != new_is_3d:
                            self._replace_content_for_pack()
                        elif self.window and new_is_3d and self.webview:
                            # Refresh the manifest/model loaded by the WebKit canvas.
                            self.webview.load_uri(f"http://127.0.0.1:{self.viewer_port}/?reload={time.time_ns()}")
                    except RuntimeError:
                        self.pack = previous_pack
                        raise
                    self.x = min(self.x, max(0, self.screen_w - self.dimensions()[0]))
                    self.frame_index = 0
                    self.frame_elapsed = 0.0
                    self.state = "idle"
                    self.idle_until = 0.0
                    self.next_idle_at = time.monotonic() + random.uniform(2.0, 4.0)
                    self.jump_windup_until = 0.0
                    self.jump_timer = random.uniform(10.0, 18.0)
                    self.viewer_interaction_seq += 1
                    self.viewer_interaction = ""
                    self._save_settings()
                    self.show_bubble(f"Loaded: {self.pack.name}")
            elif cmd == "next":
                self._cycle_pack()
            elif cmd == "size":
                self.size_px = int(payload.get("value", self.size_px))
                self.size_px = min(800, max(40, self.size_px))
                self._save_settings(); self.show_bubble(f"size: {self.size_px}px")
            elif cmd == "speed":
                self.speed = min(500.0, max(20.0, float(payload.get("value", self.speed))))
                self.vx = self.direction * self.speed
                self._save_settings(); self.show_bubble(f"speed: {self.speed:.0f}px/s")
            elif cmd == "pause":
                self.paused = True; self._save_settings(); self.show_bubble("paused")
            elif cmd == "resume":
                self.paused = False; self._save_settings(); self.show_bubble("resumed")
            elif cmd == "toggle":
                self.paused = not self.paused; self._save_settings(); self.show_bubble("paused" if self.paused else "resumed")
            elif cmd == "hidefullscreen":
                self.cfg["hide_in_fullscreen"] = bool(payload.get("value", True))
                self._save_settings(); self.show_bubble(f"hide in fullscreen: {self.cfg['hide_in_fullscreen']}")
            elif cmd == "quit":
                self.app.quit()
            elif cmd == "action":
                if not getattr(self.pack, "is_3d", False):
                    self.show_bubble("custom actions are available for 3D packs")
                else:
                    action_name = str(payload.get("name", "")).strip()
                    if not action_name:
                        self.show_bubble("usage: petctl action <animation-name>")
                    elif action_name not in getattr(self.pack, "animations", {}):
                        self.show_bubble(f"No animation in pack: {action_name}")
                    else:
                        self.viewer_interaction = "action:" + action_name
                        self.viewer_interaction_seq += 1
                        self.show_bubble(f"Action: {action_name}")
            elif cmd == "status":
                self.show_bubble(f"{self.pack.name} · {self.size_px}px · {self.speed:.0f}px/s")
            elif cmd == "ping":
                pass
            else:
                self.show_bubble("unknown command — run petctl help")
        except (OSError, ValueError, RuntimeError) as exc:
            self.show_bubble(f"Could not apply: {str(exc)[:65]}")
        if isinstance(self.area, Gtk.DrawingArea):
            self.area.queue_draw()
        return GLib.SOURCE_REMOVE

    def tick(self) -> bool:
        if not self.area or not self.window:
            return True
        now = time.monotonic()
        dt = min(0.045, max(0.001, now - self.last_tick))
        self.last_tick = now
        width, height = self.screen_w, self.screen_h
        if width > 1 and height > 1 and not self.suppressed and not self.paused and not self.dragging:
            self._physics(dt, now)
        elif self.paused:
            self._set_state("idle")

        # Sprite packs use frame timing; 3D model clips run in WebKit/Three.js.
        if not getattr(self.pack, "is_3d", False):
            frames = self.pack.get_frames(self.state)
            self.frame_elapsed += dt
            safety = 0
            while frames and self.frame_elapsed >= frames[min(self.frame_index, len(frames)-1)].duration and safety < 8:
                duration = frames[min(self.frame_index, len(frames)-1)].duration
                self.frame_elapsed -= duration
                self.frame_index = (self.frame_index + 1) % len(frames)
                safety += 1

        if self.speech and now >= self.speech_until:
            self.speech = ""
        self._update_input_region()
        if isinstance(self.area, Gtk.DrawingArea):
            self.area.queue_draw()
        return True

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.frame_index = 0
            self.frame_elapsed = 0.0

    def _physics(self, dt: float, now: float) -> None:
        pet_w, pet_h = self.dimensions()
        moved_horizontally = False
        if self.jump_windup_until > 0.0:
            if now < self.jump_windup_until:
                self.vx = 0.0
                self.vy = 0.0
                self._set_state("jump")
                return
            self.jump_windup_until = 0.0
            self.grounded = False
            self.support_id = ""
            self.vy = self.pending_jump_vy
            self.vx = self.pending_jump_vx
        if not self.platforms:
            self.platforms = [{"id": "floor", "x1": 0.0, "x2": float(self.screen_w), "top": float(self.screen_h)}]
        if self.grounded:
            support = next((p for p in self.platforms if p["id"] == self.support_id), None)
            if support is None:
                # Find a surface directly below the pet, otherwise let it fall.
                support = next((p for p in self.platforms if abs((self.y + pet_h) - p["top"]) < 8 and p["x1"] < self.x + pet_w * .55 < p["x2"]), None)
                if support:
                    self.support_id = support["id"]
                else:
                    self.grounded = False
            if self.grounded and support:
                self.y = support["top"] - pet_h
                if now >= self.next_idle_at and self.idle_until <= 0:
                    self.idle_until = now + random.uniform(3.0, 7.0)
                if self.idle_until and now >= self.idle_until:
                    self.idle_until = 0.0
                    self.next_idle_at = now + random.uniform(2.0, 4.5)
                if self.idle_until > now:
                    self.vx = 0.0
                    self._set_state("idle")
                else:
                    if now < self.turn_windup_until:
                        # Пауза перед поворотом: стоим на месте, пока JS разворачивает модель
                        self.vx = 0.0
                        self._set_state("walk")
                    else:
                        self.vx = self.direction * self.speed
                        self.x += self.vx * dt
                        moved_horizontally = True
                        if self.x < 0:
                            self.x = 0
                            if self.direction == -1:
                                self.direction = 1
                                self.turn_windup_until = now + getattr(self.pack, "turn_duration", 0.0)
                        elif self.x + pet_w > self.screen_w:
                            self.x = self.screen_w - pet_w
                            if self.direction == 1:
                                self.direction = -1
                                self.turn_windup_until = now + getattr(self.pack, "turn_duration", 0.0)
                        
                        center = self.x + pet_w * 0.5
                        if center < support["x1"] or center > support["x2"]:
                            self.grounded = False
                            self.support_id = ""
                        else:
                            self.y = support["top"] - pet_h
                        self.jump_timer -= dt
                        pack_behavior = getattr(self.pack, "meta", {}).get("behavior", {}) if getattr(self.pack, "is_3d", False) else {}
                        auto_jump = not getattr(self.pack, "is_3d", False) or bool(pack_behavior.get("autoJump", False))
                        if self.grounded and auto_jump and self.jump_timer <= 0 and random.random() < 0.02:
                            self._jump()
                        elif self.state != "land" or now >= self.land_until:
                            self._set_state("walk" if self.grounded and self.vx else "idle")
        if not self.grounded:
            old_bottom = self.y + pet_h
            self.vy += self.gravity * dt
            self.y += self.vy * dt
            if not moved_horizontally:
                self.x += self.vx * dt
            if self.x < 0:
                self.x = 0
                self.direction = 1
                self.vx = abs(self.vx)
            elif self.x + pet_w > self.screen_w:
                self.x = self.screen_w - pet_w
                self.direction = -1
                self.vx = -abs(self.vx)
            new_bottom = self.y + pet_h
            if self.vy >= 0:
                center = self.x + pet_w * 0.5
                candidates = [p for p in self.platforms
                              if old_bottom <= p["top"] + 2 and new_bottom >= p["top"]
                              and p["x1"] <= center <= p["x2"]]
                if candidates:
                    platform = min(candidates, key=lambda p: p["top"])
                    self.y = platform["top"] - pet_h
                    self.vy = 0.0
                    self.grounded = True
                    self.support_id = platform["id"]
                    self.land_until = now + 0.20
                    self.jump_timer = random.uniform(10.0, 18.0)
                    self._set_state("land")
                elif self.y > self.screen_h + pet_h * 2:
                    # Recover from windows disappearing mid-fall or workspace changes.
                    self.x = random.uniform(self.screen_w * .15, self.screen_w * .65)
                    floor = next((p for p in self.platforms if p["id"] == "floor"), None)
                    self.y = (floor["top"] - pet_h - 2) if floor else self.screen_h - pet_h - 2
                    self.vy = 0.0
                    self.grounded = True
                    self.support_id = "floor"
            if not self.grounded:
                self._set_state("jump" if self.vy < 40 else "fall")
        if self.grounded and self.state == "land" and now >= self.land_until:
            self._set_state("walk")


def main() -> int:
    make_socket_path()
    existing = ask_running()
    if existing and existing.get("running"):
        print("Desktop Pet is already running. Use `petctl` to control it.")
        return 0

    app = Gtk.Application(application_id="io.eigver.desktop-pet", flags=Gio.ApplicationFlags.NON_UNIQUE)
    pet = DesktopPet(app)
    app.connect("activate", pet.on_activate)
    app.connect("shutdown", pet.on_shutdown)
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    try:
        result = app.run(sys.argv[:1])
    except KeyboardInterrupt:
        result = 0
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())