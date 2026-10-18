#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/desktop-pet"
BIN_DIR="$HOME/.local/bin"
AUTOSTART_DIR="$HOME/.config/autostart"

if ! command -v pacman >/dev/null 2>&1; then
  echo "This installer targets Arch Linux. Install the dependencies manually first."
  exit 1
fi
if ! /usr/bin/python -c 'import gi, cairo; from PIL import Image; gi.require_version("Gtk", "4.0"); gi.require_version("Gtk4LayerShell", "1.0"); from gi.repository import Gtk4LayerShell' >/dev/null 2>&1; then
  echo "Missing runtime dependencies. Install them with:"
  echo "  sudo pacman -S --needed gtk4 gtk4-layer-shell python-gobject python-cairo python-pillow"
  exit 1
fi

mkdir -p "$APP_DIR" "$BIN_DIR"
# Stop a running copy before replacing its Python/HTML files; otherwise its
# loaded Python code can serve the newly copied viewer template with old logic.
if [[ -x "$BIN_DIR/petctl" ]]; then
  "$BIN_DIR/petctl" quit >/dev/null 2>&1 || true
fi
# Copy into the versioned user-local app directory; no system files are changed.
cp -a "$SOURCE_DIR"/. "$APP_DIR"/

cat > "$BIN_DIR/desktop-pet" <<EOF
#!/usr/bin/env bash
exec /usr/bin/python "$APP_DIR/desktop_pet.py" "\$@"
EOF
cat > "$BIN_DIR/petctl" <<EOF
#!/usr/bin/env bash
exec /usr/bin/python "$APP_DIR/petctl.py" "\$@"
EOF
chmod +x "$BIN_DIR/desktop-pet" "$BIN_DIR/petctl"

if [[ "${1:-}" == "--autostart" ]]; then
  mkdir -p "$AUTOSTART_DIR"
  cat > "$AUTOSTART_DIR/desktop-pet.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Desktop Pet
Comment=Animated pet overlay for Hyprland
Exec=$BIN_DIR/desktop-pet
Terminal=false
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF
  echo "Autostart enabled: $AUTOSTART_DIR/desktop-pet.desktop"
fi

echo "Installed to: $APP_DIR"
echo "Commands: desktop-pet, petctl"
echo "Start with: desktop-pet"
echo "Run 'petctl help' for controls."
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  echo "Note: add $BIN_DIR to PATH if it is not already there."
fi
