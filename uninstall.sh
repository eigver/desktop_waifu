#!/usr/bin/env bash
set -euo pipefail
if [[ -x "$HOME/.local/bin/petctl" ]]; then "$HOME/.local/bin/petctl" quit >/dev/null 2>&1 || true; fi
rm -f "$HOME/.local/bin/desktop-pet" "$HOME/.local/bin/petctl"
rm -f "$HOME/.config/autostart/desktop-pet.desktop"
rm -rf "$HOME/.local/share/desktop-pet"
echo "Desktop Pet files removed. User settings in ~/.config/desktop-pet were kept."
