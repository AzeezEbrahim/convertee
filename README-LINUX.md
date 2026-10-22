# Tangerine for Linux

A Linux port of [Tangerine for Windows](https://github.com/CristianO2601/tangerine-windows)
(MIT), the drag-to-convert wheel inspired by the macOS app Tangerine. Tested on
Ubuntu 22.04 (GNOME on Xorg).

| Gesture | Wheel |
| --- | --- |
| **Shift** + drag files (from Files, the desktop, a browser…) | Convert: JPG, PNG, WEBP, AVIF, HEIC, PDF, MP4, MP3, DOCX, ZIP… |
| **Alt + Shift** + drag files | Tools: compress, crop, trim, redact, annotate, collage, metadata, QR, split/merge PDF… |
| Right-click → **Scripts → Tangerine** → Convert… / Tools… | Same wheels, pinned at the cursor, without dragging |

Results are saved next to the originals; originals are never changed.

## Install

```bash
sudo apt install ffmpeg libxcb-cursor0 python3-venv   # usually already present
./install.sh                # or: ./install.sh --no-autostart
```

The installer is per-user (no root): a Python environment and the app in
`~/.local/share/tangerine-linux`, the `tangerine` command in `~/.local/bin`,
Files scripts in `~/.local/share/nautilus/scripts/Tangerine`, an app-menu entry
and (optionally) start at login. Run it again to update. `./uninstall.sh` removes
everything except settings (`~/.config/tangerine`; add `--purge` for those too).

Optional: `sudo apt install unrar` or `p7zip-full` to extract RAR, `rar` to create it.

## Run, stop, start at login

| | |
| --- | --- |
| Start | `tangerine` in a terminal, or **Tangerine** in the app grid (Super key) |
| Stop | tray icon in the top bar → **Quit**, or `tangerine --quit` |
| Start at login | on by default; tray icon → **Settings → General → Start Tangerine when I log in** |
| Settings | tray icon → **Settings** (appearance light/dark/system, sounds, tool options…) |
| Log | `~/.config/tangerine/tangerine.log` |

## Look and feel

The wheel follows the macOS app's proportions and materials: wide segment
petals around a small hub, constant-width gaps, a translucent rind over the
blurred desktop, a deep tangerine hover, the dragged file's preview in the hub
with its size in a frosted pill (the hovered option's full name replaces it),
and the drag icon kept above the wheel. Text uses Inter (bundled, SIL OFL),
the closest open design to SF Pro. Light or dark follows the system unless
Settings → General → Appearance says otherwise.

## How the port works

The Windows build samples Win32 state; on Linux `tangerine/x11.py` answers the
same questions without hooks or grabs:

- pointer button and Shift/Ctrl/Alt come from `XQueryPointer`;
- an XFixes watch on `XdndSelection` tells a real drag-and-drop apart from other
  Shift+mouse movement (text selection, rubber bands), and the dragged files are
  read from it early, like the OLE `CF_HDROP` read on Windows;
- the game/full-screen gate reads `_NET_ACTIVE_WINDOW` / `_NET_WM_STATE`.

GTK apps (Files, the desktop) offer a Shift-drag as *move-only*. On X11 the drop
target decides whether the source deletes anything, and Tangerine never asks, so
the wheel accepts those drops and the originals stay in place (Windows keeps
rejecting move-only drops, where Explorer would delete the source).

Explorer's "Send to" entries and Shift+Enter become Files scripts, which reach the
running instance through the same local socket (`--wheel conversion|tools`,
`--images-to-pdf`).

## Limits

- The drag gesture needs an X11 session ("Ubuntu on Xorg"). On Wayland, use the
  right-click scripts.
- The keyboard selection trigger (Shift+Enter in Explorer) has no Files
  equivalent; the scripts cover it.

## Tests

```bash
~/.local/share/tangerine-linux/venv/bin/python -m pytest -q
```

Windows-only probes are skipped on Linux; `tests/test_x11.py` covers the port.
