# Convertee

Convert files while you drag them, on Linux.

- **Shift** + drag files: a wheel of formats opens at the cursor. Drop on one.
- **Alt + Shift** + drag: tools (compress, crop, trim, redact, …).
- Results are saved next to the original. The original is never changed.

## Install

```bash
sudo apt install ffmpeg libxcb-cursor0 python3-venv
./install.sh
```

Tested on Ubuntu 22.04, GNOME on Xorg. On Wayland, use right-click → Scripts → Convertee.

## Use

| | |
| --- | --- |
| Convert | Shift + drag |
| Tools | Alt + Shift + drag |
| Without dragging | right-click → Scripts → Convertee |
| Quit | tray icon → Quit, or `convertee --quit` |
| Start at login | tray icon → Settings → General |
| Uninstall | `./uninstall.sh` |

## Credits

Linux port of [Tangerine for Windows](https://github.com/CristianO2601/tangerine-windows) (MIT),
inspired by the macOS app [Tangerine](https://tangerineformac.com). Not affiliated with either.
Font: [Inter](https://github.com/rsms/inter) (SIL OFL). License: MIT.
