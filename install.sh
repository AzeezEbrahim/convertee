#!/usr/bin/env bash
# Install Convertee for the current user (no root needed).
#
#   ./install.sh                 install / update, start at login, start it
#   ./install.sh --no-autostart
#   ./install.sh --no-start
#
# Layout:
#   ~/.local/share/convertee/{venv,app,bin/convertee}   (+ ~/.local/bin/convertee)
#   ~/.config/convertee                                 settings and log
set -euo pipefail

autostart=1
start=1
for arg in "$@"; do
  case "$arg" in
    --no-autostart) autostart=0 ;;
    --no-start) start=0 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
root="$data/convertee"
legacy="$data/tangerine-linux"   # install location before the rename
venv="$root/venv"
app="$root/app"
launcher="$root/bin/convertee"

say() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; }

[[ "$(uname -s)" == Linux ]] || { warn "This installer is for Linux."; exit 1; }
command -v python3 >/dev/null || { warn "python3 is required: sudo apt install python3 python3-venv"; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
  || { warn "Python 3.10 or newer is required."; exit 1; }
python3 -c 'import venv, ensurepip' 2>/dev/null \
  || { warn "python3-venv is required: sudo apt install python3-venv"; exit 1; }

has_lib() {  # ldconfig lives in /sbin, often outside a user's PATH
  { PATH="$PATH:/sbin:/usr/sbin" ldconfig -p 2>/dev/null | grep -q "$1"; } \
    || compgen -G "/usr/lib/*/$1" >/dev/null || compgen -G "/usr/lib64/$1" >/dev/null
}
missing=()
command -v ffmpeg >/dev/null || missing+=(ffmpeg)
has_lib 'libxcb-cursor.so.0' || missing+=(libxcb-cursor0)
if ((${#missing[@]})); then
  warn "Missing: ${missing[*]}  →  sudo apt install ${missing[*]}"
  [[ " ${missing[*]} " == *" libxcb-cursor0 "* ]] && exit 1
fi
[[ "${XDG_SESSION_TYPE:-}" == wayland ]] \
  && warn "Wayland: the drag wheel needs 'Ubuntu on Xorg'. Right-click → Scripts → Convertee still works."

stop() { pkill -f "$1/app/main.py" 2>/dev/null || true; }

if [[ -d "$legacy" ]]; then
  say "Replacing the old Tangerine install"
  stop "$legacy"
  if [[ -x "$legacy/venv/bin/python" && ! -e "$venv" ]]; then
    mkdir -p "$root"
    mv "$legacy/venv" "$venv"   # keep the downloaded packages
  fi
  [[ "$(readlink "$HOME/.local/bin/tangerine" 2>/dev/null)" == "$legacy/"* ]] && rm -f "$HOME/.local/bin/tangerine"
  rm -rf "$legacy"
fi

say "Python environment"
[[ -x "$venv/bin/python" ]] || python3 -m venv "$venv"
"$venv/bin/python" -m pip install --quiet --upgrade pip
"$venv/bin/python" -m pip install --quiet --no-cache-dir -r "$src/requirements.txt"

say "App files"
rm -rf "$app.new"
mkdir -p "$app.new"
cp -a "$src/main.py" "$src/tangerine" "$src/assets" "$app.new/"
find "$app.new" -name '__pycache__' -type d -prune -exec rm -rf {} +
stop "$root"
rm -rf "$app"
mv "$app.new" "$app"

mkdir -p "$(dirname "$launcher")" "$HOME/.local/bin"
cat > "$launcher" <<EOF
#!/bin/sh
# Managed by Convertee
export CONVERTEE_LAUNCHER="$launcher"
exec "$venv/bin/python" "$app/main.py" "\$@"
EOF
chmod +x "$launcher"
ln -sfn "$launcher" "$HOME/.local/bin/convertee"

say "Files scripts, app menu entry$([[ $autostart == 1 ]] && echo ', start at login')"
autostart_py=$([[ $autostart == 1 ]] && echo True || echo False)
(cd "$app" && "$venv/bin/python" -I -c "
import sys; sys.path.insert(0, '.')
from tangerine.linux_shell import register_shell
register_shell('$launcher', autostart=$autostart_py)
")

if ((start)); then
  nohup "$launcher" >/dev/null 2>&1 &
fi
say "Done. Shift+drag to convert, Alt+Shift+drag for tools. Quit: convertee --quit"
