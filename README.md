# Tangerine for Windows

> **Linux:** this branch also runs on Ubuntu (X11). See [README-LINUX.md](README-LINUX.md).

A drag-to-convert desktop tray utility for Windows 11. Hold **Shift** while
dragging files out of File Explorer and a radial wheel appears under the
cursor: drop the files onto a petal to convert them. Hold **Alt+Shift**
instead and the same wheel offers file tools — compress, crop, trim, redact,
collage, metadata, QR reading and more.

![Tangerine conversion wheel in dark mode](docs/images/wheel-dark.png)

<p align="center">
  <img src="docs/images/icon-preview.png" alt="Tangerine icon" width="128">
</p>

> **Disclaimer:** This is an independent, community reimplementation of the
> drag-to-convert *idea* of the macOS app **Tangerine** by **thmmhnsn**. It is
> **not affiliated with, endorsed by, or connected to** the original author or
> app. This project is a from-scratch Python/Qt reimplementation for Windows;
> the name is used only to describe the interaction it was inspired by, and the
> icon is an original re-creation of a citrus-wheel concept.

## What it is

Tangerine for Windows is a Python 3 + PySide6 tray application. It watches for
drag sessions in Explorer (using public Win32 state and the OLE drag
clipboard — no input hooks) and shows a fixed radial picker at the drop point.
Choosing a petal starts a background job with a progress window, and the output
is written as a **new sibling file**: source files are never modified, and the
drop itself is copy-only.

![Tangerine conversion wheel in light mode](docs/images/wheel-light.png)

The wheel follows the Windows theme by default, and can be forced to **Light**
or **Dark** under **Settings → General → Appearance**.

## Interface & appearance

The wheel, the progress card and every editor window are drawn as translucent
"material" surfaces: Tangerine captures the desktop behind the window, blurs
it and lays a warm wash on top, so the HUD looks frosted without depending on
OS blur effects. If the capture is unavailable the surface falls back to the
wash alone.

- **Wheel.** Petals are rounded wedges over a soft halo. Conversion mode shows
  one large uppercase label per petal (no icons); tools mode shows a
  monochrome line icon with a small label. The petal under the cursor turns
  orange (~120 ms) while a central capsule slides in with the name of the
  highlighted item. The wheel blooms in with a staggered animation (~250 ms)
  and fades out in ~160 ms.
- **Progress card.** Peach close chip, bold title ("Converting to MP4",
  "Cropping video"...), the file name and a thin orange bar.
- **Editor windows.** Their own header with the close chip, a centred title
  and a hairline; orange sliders with a white thumb, peach chips and an orange
  **Apply** button.
- **Appearance.** **Settings → General → Appearance** offers **System**,
  **Light** and **Dark** (stored as `appearanceTheme` in `settings.json`); the
  change applies live to the wheel, the progress cards and the editors.

## Features

### Editing multiple images and saved options

The tools wheel offers Crop, Annotate, Redact and Add Background for multiple
images. Positional edits open one editor per image; cancel stops the remaining
editors. Background applies one set of options across the selected images.
The PDF editor supports Ctrl/Shift selection, moving groups, and adding/removing
images before creating the PDF.

Compression (images, video, audio), Collage and Add Background offer **Use these
options next time without asking**. This is **off initially**. Enable it when
applying the options; turn it off in **Settings → Tool options** to edit them
again. Invalid or missing saved options open the dialog. Crop, Annotate and
Redact always collect positions for each source image.

See the [1.10.1 repair audit](docs/editor-repair-1.10.1.md) for causes, components,
and the runtime validation used before packaging. The [1.10.2 follow-up](docs/editor-repair-1.10.2.md)
covers numeric buttons after dragging a crop.

### Image PDF workflow

Create a PDF from one or several images through Tangerine's tray, tools wheel,
or **right-click → Send to → Tangerine - Crear PDF**. The second Send to entry,
**Tangerine - PDF por nombre**, converts immediately in natural filename order
and closes its progress card when finished. Both use the running Tangerine app.

![Image PDF editor in Tangerine](docs/images/image-pdf-dark.png)

See the [integration audit](docs/image-pdf-integration.md) for the native menu,
ordering, layout, migration and validation details.

### Conversions (Shift-drag, or Alt+Shift-drag with the wheel configured the default way)

| Family | Sources | Outputs |
| --- | --- | --- |
| Images | jpg/jpeg, png, webp, avif, heic/heif, tiff/tif, bmp, svg | jpg, png, webp, avif, heic, tiff, pdf, and docx (docx only from jpg/png) |
| Audio | mp3, m4a, wav, flac, ogg, opus, aiff/aif, wma | mp3, m4a, wav, flac, ogg, opus, aiff, wma (every target except the current format) |
| Video | mp4, mov, mkv, avi, webm, wmv, m4v | mp4, mov, mkv, webm, avi, wmv, gif, and audio extraction to mp3/m4a |
| GIF | gif | mp4, mov, mkv, webm, avi, wmv |
| Subtitles | srt, vtt | srt ↔ vtt (the other format) and txt |
| PDF | pdf | docx, jpg, png, txt |
| TXT | txt | pdf, jpg, png |
| Documents | docx, xlsx, pptx, csv, rtf, md, odt, epub | per source — see [Documents](#documents) below |
| Archives | zip, tar, gz, tar.gz, rar (rar needs WinRAR) | zip, tar, tar.gz, rar (extract + repack) |

Multi-file drops convert in batch. Several images can be merged into a single
PDF or a collage; several PDFs merge into one; several videos can be joined.

### Subtitles

SRT and WebVTT files convert without any external engine: the encoding is
detected tolerantly (BOM, UTF-8, CP1252) and the output is always UTF-8.
**SRT ↔ VTT** keeps cue text and timings; both convert to **TXT** as a plain
transcript (no timings). Converting to SRT drops WebVTT cue settings and cue
identifiers.

### Documents

Each document source offers its own targets:

| Source | Targets |
| --- | --- |
| docx | txt, pdf, jpg, png |
| xlsx | csv, pdf, jpg, png |
| pptx | txt, pdf, jpg, png |
| csv | xlsx, pdf, jpg, png, txt |
| rtf | txt, pdf, jpg, png |
| md | txt, html, pdf, jpg, png |
| odt | txt, pdf, jpg, png |
| epub | txt |

Document exports render through the same HTML/CSS pipeline: PDF and JPG/PNG
outputs honour the **Documents and PDF** settings (A4 or Letter paper, normal
16/18 mm margins by default, optional page numbers) and the resulting PDF
carries a real title and metadata.

Office files (docx, xlsx, pptx) also offer **doc.compress** in the tools wheel:
it rebuilds the file's ZIP container at maximum compression and keeps the
original bytes when the rebuilt container would not be smaller.

### Tools (Alt+Shift-drag by default)

The app ships **20 interactive editor dialogs** plus direct background jobs:

- **Images:** Compress · Metadata (view, edit or remove GPS location on
  images) · Edit Photo (exposure, contrast, saturation, temperature,
  vibrance, sharpness, vignette and grain, plus Mono/Sepia/Noir/Vivid/Warm/
  Cool presets) · Annotate Photo · Add Background (solid colour, gradient with
  angle, or an image; aspect ratio, margin and rounded corners) · Crop ·
  Redact Photo (solid, blur or 12 px pixelate, with face detection) · Create
  PDF · Create Collage · Read QR Codes
- **Audio:** Compress · Metadata · Normalize Volume · Audio Visualizer · Trim
  (waveform) · Convert Audio Channels · Bleep
- **Video:** Compress · Metadata · Remove Audio · Trim · Crop · Change Speed ·
  Snapshots · Split · Redact (solid, blur or pixelate) · Join
- **PDF:** Compress · Metadata · Split · Merge into one PDF · Read QR Codes
- **GIF:** Metadata · Read QR Codes
- **TXT:** Compress (tidy whitespace and blank lines)
- **Documents:** Compress (docx, xlsx, pptx)
- **Archives:** Extract

## Requirements

- **Windows 11** (tested target).
- **Python 3.11+** — developed and tested on Python 3.14.
- **FFmpeg + ffprobe** — required for every audio/video/GIF conversion and for
  media metadata. Tangerine detects it in its own `tools\` folder, in winget
  installs, in `C:\ffmpeg\bin`, and on `PATH`. The recommended install is:
  ```
  winget install Gyan.FFmpeg
  ```
- **WinRAR (optional)** — only needed to create or extract `.rar` archives.
  Everything else works without it.
- Python packages, installed with:
  ```
  pip install -r requirements.txt
  ```

## Run

From the repository root:

```
pythonw main.py
```

`pythonw` keeps the console hidden, which is the normal way to run it. Use
`python main.py` while debugging to see the console output. On first launch the
app shows a tray balloon explaining the Shift / Alt+Shift gestures.

## Usage

### Create a PDF from images

Use **Create PDF from images...** in the Tangerine tray menu or in
**Settings → General → File Explorer**. Select one or multiple images. The
same editor opens from the **Create PDF** tool petal and the **PDF** conversion
petal when all selected files are images.

- Order pages by natural filename (`Page 2` before `Page 10`), received Explorer
  order, or manually with **Move up / Move down**.
- Use one image per page or a rows × columns grid, with optional filename labels.
- Save beside the source images using an automatic free name, or choose a new
  output filename. Existing files and original images are preserved.
- Conversion runs through Tangerine's existing floating progress card, which
  closes on success. Cancellation and errors use that same card.

The Windows installer registers two shortcuts under **right-click → Send to**:
**Tangerine - Crear PDF** opens the editor; **Tangerine - PDF por nombre** converts
immediately in natural order. Both deliver all selected files to the running
Tangerine instance. They also start Tangerine if it is not running.

A native classic **Tangerine — PDF** submenu is registered too, with natural
order, received order and configuration actions. Windows may cache extension
registrations; **Send to** and the tray provide access independently. The
registration can be repaired from Settings. Explorer selection order does not
guarantee Ctrl-click chronology; use Manual for an exact sequence.

Upgrading migrates registrations owned by the previous CrisImageToPdf utility
after saving a registry snapshot under `%APPDATA%\Tangerine\backups`. Its files
remain available for rollback; image conversion now runs inside Tangerine.

Image PDF pages retain the source image's proportions at 150 dpi. The paper
and margin preferences for document exports do not apply to this image layout.

### Drag workflows

1. Select one or more files in File Explorer and start dragging them.
2. Hold **Shift** (conversions) or **Alt+Shift** (tools) while dragging.
3. The wheel appears under the cursor and stays fixed in place — move the
   cursor over a petal and release the files.
4. Dropping away from every petal cancels and hides the wheel.

While the wheel is visible the keyboard also works: **←/↑** and **→/↓** move
between petals (wrapping around), **Enter** applies the highlighted one and
**Escape** cancels. In File Explorer, **Shift+Enter** with files selected opens
the wheel at the cursor for that selection — no dragging needed. An optional
long-press touch mode (`touchLongPressEnabled`, off by default) shows the
conversion wheel when a file drag is held for ~700 ms with no modifier
pressed.

Sources are never touched: outputs are new files named beside the original
(`photo 2.jpg`, `clip Cropped.mp4`, `Photo Collage.png`, and so on), and the
drop action is always a copy.

## Settings and autostart

Right-click the tray icon → **Settings...** (double-clicking the tray icon
opens it too). From there you can:

- Remap the conversion and tools modifier combinations.
- Choose the HUD appearance (System/Light/Dark) and enable the optional
  long-press touch mode.
- Toggle sound/haptic feedback and pick the fan theme.
- Set default compression presets and sizes for images, video, and audio.
- Choose the paper size, margin preset and page numbering used by every
  document PDF/JPG/PNG export (Settings → Formats → Documents and PDF).
- Search conversion defaults, open the settings folder, or restore defaults.
- See detected FFmpeg/FFprobe/WinRAR paths, the settings file, and the log file
  on the **About** tab.

Settings are stored in `%APPDATA%\Tangerine\settings.json` (written atomically,
with a `.json.bak` fallback if the file is ever corrupted); logs live in the
same folder.

Tangerine does not register itself for autostart. To launch it with Windows,
create a shortcut to `pythonw main.py` (for example
`pythonw "C:\path\to\Tangerine\main.py"`), press `Win+R`, run `shell:startup`,
and place the shortcut in that folder. It then appears under **Task Manager →
Startup apps** and can be disabled from there at any time.

## Languages

The interface ships in **English** and **Spanish**. Pick the language under
**Settings → General → Language**; the choice is stored in `settings.json`.
**System** follows the Windows display language (`es_MX` → Spanish, any
unsupported locale falls back to English).

- Changing the language applies immediately to the tray menu, its tooltip and
  the conversion/tools wheel.
- Windows that are already open (editors, progress windows) apply the new
  language the next time they are opened.
- Coverage: the wheels, tray, settings window, every editor dialog, the
  progress windows and the common conversion/tool error messages. A few
  rarely seen fallback messages may still be in English.

## Tests

The suite uses pytest and needs no external fixtures — it synthesizes its own
images, audio, PDF, TXT, ZIP and (when possible) video files in temporary
folders. FFmpeg is not required for the tests to pass.

```
pip install -r requirements-dev.txt
set QT_QPA_PLATFORM=offscreen
python -m pytest -q
```

Covered: every `tangerine` module imports cleanly, every tool offered by the
catalog routes to a real editor/job without falling back to a "not available"
message box, and the settings file survives an atomic save/load round-trip and
a corrupt-file recovery.

The v1.9.1 verification run is fully green: **183 tests** (182 passing, one
intentionally skipped — the end-to-end render needs a display), a tool-routing
audit of **43/43** identifiers without fallbacks, **23/23** editor smoke
dialogs, **11/11** copy-regression checks, **50/50** end-to-end conversion/tool
cases and **38/38** document routes.

## Known limitations

- **Windows 11 only.** The drag detection uses Win32/OLE APIs and is not
  portable to macOS or Linux.
- **Windowed games without a known engine class stay available.** The wheel is
  suppressed over full-screen apps that cover the monitor, over known
  game-engine window classes and while Windows reports a blocked notification
  state; a game that keeps a normal window is treated like any other app.
  Turn off "Hide the wheel in games and full-screen apps" in Settings →
  General to disable the gate for everything.
- **Installer without code signing.** `packaging/build.ps1` builds a per-user
  Inno Setup installer (`dist\Tangerine-<version>-Setup.exe`) next to the
  portable ZIP, but the binaries are not signed, so Windows SmartScreen may
  warn when you first launch them.
- **No auto-update.** The "Check for Updates..." tray item only shows the
  version and points at manual updates.
- **Audio Visualizer needs an audio track.** Video-only sources are not
  supported for visualization.
- **OCR is optional.** PDFs without a text layer fall back to the open-source
  OCR engine when `rapidocr-onnxruntime` is installed
  (`pip install -r requirements-ocr.txt`); without it, such PDFs cannot be
  exported to `.txt` and the app points at the install command.
- **Document fidelity varies by format.** Markdown, DOCX, XLSX and CSV render
  through HTML/CSS (tables, code blocks and the images inside DOCX included)
  with a real page layout: A4 or Letter paper, configurable margins and
  optional page numbers. TXT, RTF and ODT print as readable paragraphs, while
  PPTX is redrawn slide by slide, so complex layouts are approximate and
  animations are not preserved.
- **Only modern document formats.** Legacy binary Office files (.doc, .xls,
  .ppt) are not supported — the document family reads OOXML, ODF and plain
  formats (docx, xlsx, pptx, csv, rtf, md, odt, epub).
- **Excel reads only the active sheet**, using the values cached in the file.
- **Document images render through an intermediate PDF** at 300 dpi.
- **Localization is UI-level.** English and Spanish are supported; a few
  rarely seen fallback/error strings remain English-only.
- **Large images.** Editing images above roughly 50 megapixels in the
  interactive editors can use a lot of memory.
- **FFmpeg-dependent behavior.** Audio/video codec availability and edge-case
  format support depend on the installed FFmpeg build.
- **Subtitle conversion is text-level.** Cue text and timings are preserved,
  but converting to SRT drops WebVTT cue settings, and TXT keeps only the
  transcript (no timings).
- **Files only.** Folders cannot be dropped onto the wheel.

## Repository layout

```
Tangerine/
├─ main.py               # entry point (pythonw main.py)
├─ tangerine/            # application package
├─ assets/               # tray icon, app icon, highlight sound
├─ tests/                # pytest suite
├─ docs/images/          # screenshots used by this README
├─ requirements.txt      # runtime dependencies
└─ requirements-dev.txt  # test dependencies
```

## Acknowledgements

- The original macOS **Tangerine** by **thmmhnsn**, whose drag-and-drop wheel
  interaction inspired this Windows implementation.
- Qt / PySide6, FFmpeg, Pillow, pillow-heif, pypdf, pypdfium2, python-docx,
  OpenCV (QR codes and face detection), and reportlab.
- The icon is an original re-creation of the citrus-wheel concept, not copied
  from the original app.

## License

This codebase is released under the [MIT License](LICENSE). The Tangerine name
and the original drag-convert idea belong to the original authors; this project
is independent and not affiliated with them.

---

## Resumen en español

**Tangerine para Windows** es una utilidad de bandeja para Windows 11: arrastra
archivos desde el Explorador manteniendo **Shift** y aparece una rueda radial
bajo el cursor; suelta sobre un pétalo para convertir. Con **Alt+Shift**
aparecen las herramientas (comprimir, recortar, censurar, collage, metadatos,
leer códigos QR, etc.). Los archivos originales nunca se modifican: todo se
guarda como copia con un nombre nuevo en la misma carpeta. El HUD (rueda,
tarjeta de progreso y ventanas de editor) usa superficies translúcidas de
"material"; su aspecto se elige en **Ajustes → General → Apariencia**
(Sistema/Claro/Oscuro). Con la rueda visible funcionan las flechas, **Enter**
y **Escape**; en el Explorador, **Shift+Enter** abre la rueda para la selección
actual, y hay un modo táctil opcional de pulsación larga (desactivado por
defecto).

Además de los formatos clásicos, v1.7.0 añade AVIF (imagen), OGG/Opus/AIFF/WMA
(audio), WebM/AVI/WMV (vídeo) y la familia de subtítulos SRT↔VTT→TXT; las
herramientas ganan pixelado en Censurar foto/vídeo, Añadir fondo completo
(color/degradado/imagen, aspecto, margen y esquinas redondeadas), Editar foto
completo (exposición, temperatura, vibrance, viñeta, grano y presets) y
metadatos GPS de imagen (ver, editar o quitar ubicación).

Desde la v1.9.0 las conversiones de documentos se imprimen con papel A4/Carta,
márgenes configurables (16/18 mm por defecto) y numeración de páginas, y TXT,
RTF, ODT y PPTX se componen por el mismo pipeline HTML que Markdown o DOCX.

Desde la v1.9.1 la rueda no se abre sobre juegos ni aplicaciones a pantalla
completa, y se oculta sola si una de ellas toma el primer plano con la rueda
visible o fija; el Explorador, el escritorio y las ventanas normales no se ven
afectados, y la puerta se puede desactivar en **Ajustes → General**.

Es una reimplementación independiente para Windows de la *idea* de conversión
por arrastre de la app macOS Tangerine (de thmmhnsn), **sin afiliación** con
sus autores. Requiere Python 3.11+ (probado en 3.14), FFmpeg
(`winget install Gyan.FFmpeg`) y, solo para `.rar`, WinRAR. Se instala con
`pip install -r requirements.txt` y se ejecuta con `pythonw main.py`. Los
ajustes están en el menú de la bandeja → Settings y se guardan en
`%APPDATA%\Tangerine\settings.json`; para el arranque automático, crea un
acceso directo en `shell:startup` y aparecerá en Administrador de tareas →
Aplicaciones de inicio. También convierte documentos (docx, xlsx, pptx, csv,
rtf, md, odt, epub) a txt/csv/xlsx/html/pdf/jpg/png según el formato, y
comprime docx/xlsx/pptx con **doc.compress**. Licencia MIT para este código; el
nombre y la idea pertenecen a los autores originales. Limitaciones honestas:
solo Windows 11, con instalador por usuario pero sin firma digital, sin actualización automática,
OCR opcional para PDF escaneados (`pip install -r requirements-ocr.txt`),
fidelidad de documentos variable (Markdown/DOCX/XLSX/CSV por HTML/CSS, PPTX
aproximado, sin formatos binarios antiguos `.doc`/`.xls`/`.ppt`), subtítulos
también a nivel de texto (se pierden los ajustes de cue de WebVTT al pasar a
SRT), interfaz en inglés o español (Ajustes → General; opción Sistema), y el
visualizador de audio necesita una pista de audio.
