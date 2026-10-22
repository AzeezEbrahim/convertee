#!/usr/bin/env bash
# Remove Convertee. Settings (~/.config/convertee) stay unless --purge.
set -euo pipefail

purge=0
[[ "${1:-}" == --purge ]] && purge=1

data="${XDG_DATA_HOME:-$HOME/.local/share}"
root="$data/convertee"

pkill -f "$root/app/main.py" 2>/dev/null || true
pkill -f "$data/tangerine-linux/app/main.py" 2>/dev/null || true

if [[ -x "$root/venv/bin/python" && -d "$root/app" ]]; then
  (cd "$root/app" && "$root/venv/bin/python" -I -c "
import sys; sys.path.insert(0, '.')
from tangerine.linux_shell import unregister_shell
unregister_shell()
") || echo "Could not remove the Files scripts; delete ~/.local/share/nautilus/scripts/Convertee by hand." >&2
fi

for name in convertee tangerine; do
  target="$(readlink "$HOME/.local/bin/$name" 2>/dev/null || true)"
  [[ "$target" == "$root/"* || "$target" == "$data/tangerine-linux/"* ]] && rm -f "$HOME/.local/bin/$name"
done
rm -rf "$root" "$data/tangerine-linux"

config="${XDG_CONFIG_HOME:-$HOME/.config}"
if ((purge)); then
  rm -rf "$config/convertee" "$config/tangerine"
  echo "Convertee and its settings were removed."
else
  echo "Convertee was removed. Settings kept in $config/convertee (--purge deletes them)."
fi
