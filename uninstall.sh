#!/usr/bin/env bash
# Remove Tangerine for Linux. Settings in ~/.config/tangerine are kept
# unless --purge is given; converted files are never touched.
set -euo pipefail

purge=0
[[ "${1:-}" == --purge ]] && purge=1

root="${XDG_DATA_HOME:-$HOME/.local/share}/tangerine-linux"
app="$root/app"

pkill -f "$app/main.py" 2>/dev/null || true

if [[ -x "$root/venv/bin/python" && -d "$app" ]]; then
  (cd "$app" && "$root/venv/bin/python" -I -c "
import sys; sys.path.insert(0, '.')
from tangerine.linux_shell import unregister_shell
unregister_shell()
") || echo "Could not remove the Files scripts; delete ~/.local/share/nautilus/scripts/Tangerine by hand." >&2
fi

if [[ -L "$HOME/.local/bin/tangerine" && "$(readlink "$HOME/.local/bin/tangerine")" == "$root/bin/tangerine" ]]; then
  rm -f "$HOME/.local/bin/tangerine"
fi
rm -rf "$root"

if ((purge)); then
  rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/tangerine"
  echo "Tangerine and its settings were removed."
else
  echo "Tangerine was removed. Settings are kept in ${XDG_CONFIG_HOME:-$HOME/.config}/tangerine (use --purge to delete)."
fi
