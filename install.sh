#!/usr/bin/env bash
# Install Tangerine for Linux for the current user (no root needed).
#
#   ./install.sh               install / update, enable start at login, start it
#   ./install.sh --no-autostart
#   ./install.sh --no-start
#
# Layout:
#   ~/.local/share/tangerine-linux/venv   Python environment (PySide6, Pillow, ...)
#   ~/.local/share/tangerine-linux/app    application files copied from this folder
#   ~/.local/share/tangerine-linux/bin/tangerine   launcher (also ~/.local/bin/tangerine)
#   ~/.config/tangerine                    settings and log (kept by uninstall.sh)
set -euo pipefail

autostart=1
start=1
for arg in "$@"; do
  case "$arg" in
    --no-autostart) autostart=0 ;;
    --no-start) start=0 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="${XDG_DATA_HOME:-$HOME/.local/share}/tangerine-linux"
venv="$root/venv"
app="$root/app"
launcher="$root/bin/tangerine"

say() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; }

[[ "$(uname -s)" == Linux ]] || { warn "This installer is for Linux."; exit 1; }
command -v python3 >/dev/null || { warn "python3 is required: sudo apt install python3 python3-venv"; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
  || { warn "Python 3.10 or newer is required."; exit 1; }
python3 -c 'import venv, ensurepip' 2>/dev/null \
  || { warn "python3-venv is required: sudo apt install python3-venv"; exit 1; }

missing=()
command -v ffmpeg >/dev/null || missing+=(ffmpeg)
has_lib() {  # ldconfig lives in /sbin, often outside a user's PATH
  { PATH="$PATH:/sbin:/usr/sbin" ldconfig -p 2>/dev/null | grep -q "$1"; } \
    || compgen -G "/usr/lib/*/$1" >/dev/null || compgen -G "/usr/lib64/$1" >/dev/null
}
has_lib 'libxcb-cursor.so.0' || missing+=(libxcb-cursor0)
if ((${#missing[@]})); then
  warn "Missing system packages: ${missing[*]}"
  warn "Install them with:  sudo apt install ${missing[*]}"
  [[ " ${missing[*]} " == *" libxcb-cursor0 "* ]] && { warn "Qt cannot open windows without libxcb-cursor0."; exit 1; }
fi
if [[ "${XDG_SESSION_TYPE:-}" == wayland ]]; then
  warn "Wayland session: the Shift-drag wheel needs an X11 session ('Ubuntu on Xorg' at login)."
  warn "Right-click → Scripts → Tangerine still works on Wayland."
fi

say "Python environment: $venv"
if [[ ! -x "$venv/bin/python" ]]; then
  python3 -m venv "$venv"
fi
"$venv/bin/python" -m pip install --quiet --upgrade pip
"$venv/bin/python" -m pip install --quiet --no-cache-dir -r "$src/requirements.txt"

say "Application files: $app"
mkdir -p "$app.new"
cp -a "$src/main.py" "$src/tangerine" "$src/assets" "$app.new/"
find "$app.new" -name '__pycache__' -type d -prune -exec rm -rf {} +
if [[ -d "$app" ]]; then
  mv "$app" "$app.old"
fi
mv "$app.new" "$app"
rm -rf "$app.old"

say "Launcher: $launcher"
mkdir -p "$(dirname "$launcher")" "$HOME/.local/bin"
cat > "$launcher" <<EOF
#!/bin/sh
# Managed by Tangerine for Linux
export TANGERINE_LAUNCHER="$launcher"
exec "$venv/bin/python" "$app/main.py" "\$@"
EOF
chmod +x "$launcher"
ln -sfn "$launcher" "$HOME/.local/bin/tangerine"

say "Files integration (right-click → Scripts → Tangerine), app menu entry$([[ $autostart == 1 ]] && echo ', start at login')"
autostart_py=$([[ $autostart == 1 ]] && echo True || echo False)
(cd "$app" && "$venv/bin/python" -I -c "
import sys; sys.path.insert(0, '.')
from tangerine.linux_shell import register_shell
register_shell('$launcher', autostart=$autostart_py)
")

if ((start)); then
  if pgrep -f "$app/main.py" >/dev/null; then
    say "Restarting Tangerine"
    pkill -f "$app/main.py" || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do pgrep -f "$app/main.py" >/dev/null || break; sleep 0.3; done
  else
    say "Starting Tangerine"
  fi
  nohup "$launcher" >/dev/null 2>&1 &
fi

say "Done. Tangerine lives in the top bar (tray)."
echo "    Shift + drag files            → convert"
echo "    Alt + Shift + drag files      → tools (compress, crop, trim, ...)"
echo "    Right-click → Scripts → Tangerine  → same wheels without dragging"
