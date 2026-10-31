# Cat Guardian

A cozy, local-only desktop pet for Windows. A small chibi cat lives on your desktop, dances to your music, fetches the files you copy, and quietly shoos away the tabs and apps that distract you.

Everything runs on your PC — no accounts, no telemetry, no cloud processing.

![Cat Guardian design sheet](cat_design_draft.png)

## What it does

- **Lives on the desktop.** The cat sits in the bottom layer of the window stack, so your normal windows always cover it. It never steals focus and never floats over your work.
- **Greets you on launch.** It walks to the centre of the screen, raises a paw with happy eyes and a blush, says **"Meow!"** with a bundled meow clip, then returns to its saved desktop spot. The greeting lasts under three seconds and is mouse-transparent.
- **Sleeps when idle.** Slow breathing, closed eyes, a curled tail, occasional ear and tail flicks, optional floating `z` marks. Repaint rate drops while asleep.
- **Wakes when you interact.** Hover, click, press-and-hold to pet, double-click for a hop, drag it around, circle the cursor for a chase loop, or flick upward for a jump. The cat remembers where you left it.
- **Chases distractions away.** A bundled Chromium extension reports blocked sites. The cat runs over, paws at the tab bar(currently not optimised for chrome vertical tabs), and the extension closes only the triggering tab by ID. Blocked apps get a normal close request as the paw lands. After a confirmed close the cat brags with a **"NO DISTRACTIONS!!!!"** popout.
- **Dances to music.** A local classifier listens to the Windows playback mix through WASAPI loopback (never the microphone). Six manual styles plus a 14-dance Auto Vibe catalog that adapts to tempo, energy and beat strength.
- **Fetches copied files.** One trip per copy or cut, one delivery per paste — regardless of how many files are selected. Explorer, the desktop and other app windows are supported. Text and emoji clips are ignored.
- **Looks how you want.** The silhouette is vector-traced from a reference illustration, with 19 coat presets and any custom `#RRGGBB` colour.

Every new behaviour has its own switch in **Settings**, and the copy-paste feature is off until you enable it.

## Requirements

- Windows 10 or 11
- Python 3.10+ (tested with 3.10.11)
- A Chromium browser (Chrome, Brave or Edge) for the tab-blocking extension
- `PySide6`, `numpy`, `soundcard` (installed from `requirements.txt`)

## Quick start

**Double-click `main.py`.** It relaunches itself inside the project virtualenv without a console window and shows a dialog if anything is missing, so startup never fails silently.

New to Python or setting this up for the first time? Follow the full walkthrough in **[INSTALL.md](INSTALL.md)** — it covers installing Python, creating the virtualenv, loading the extension and troubleshooting.

To set it up from a terminal instead:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m cat_guardian
```

If PowerShell blocks activation:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m cat_guardian
```

## Load the browser extension

1. Start Cat Guardian first.
2. Open `chrome://extensions` (or `brave://extensions`, `edge://extensions`).
3. Turn on **Developer mode**.
4. Click **Load unpacked** and select the `browser_extension` folder.
5. `instagram.com` is blocked by default. Edit the site list from the tray menu.

The extension talks to the app over a local bridge at `http://127.0.0.1:8765` only. If the app is not running, it leaves your tabs alone.

## Coats

| Colour | Hex | Colour | Hex |
| --- | --- | --- | --- |
| Cream | `#EFE6DE` | Ivory | `#F2EADB` |
| Charcoal | `#2E2E2E` | Grey | `#B0B0B0` |
| Apricot | `#F0B085` | Sage | `#A9BFA3` |
| Plum | `#7E4B5C` | Arctic Cyan | `#66D9EF` |
| Electric Mint | `#57F2B3` | Aurora Teal | `#20C9B7` |
| Neon Lavender | `#B69CFF` | Cosmic Purple | `#9061F9` |
| Rose Quartz | `#FF8FB1` | Coral Ember | `#FF795E` |
| Sunset Gold | `#FFC857` | Ice Blue | `#8DBBFF` |
| Pearl White | `#E7EDF5` | Graphite | `#65717F` |
| Midnight Navy | `#243B67` | | |

Open **Cat Appearance…** from the tray icon to pick a preset or type a custom hex value. Changes apply to the live cat, the tray icon and the settings preview immediately.

## Settings and toggles

| Feature | Where | Default |
| --- | --- | --- |
| Tab / app blocking | Settings → Blocked websites and applications | On |
| Copy-paste animations | Settings → Copy-paste deliveries | Off |
| Grab trip on copy/cut | Inside copy-paste deliveries | On |
| Drop inside other apps | Inside copy-paste deliveries | On |
| Include images | Inside copy-paste deliveries | On |
| Music reactions | Settings → Music reactions | On |
| Full dance catalog in Auto Vibe | Settings → Music reactions | On |
| Beat tick sound | Settings → Music reactions | Off |
| Floating `z` while asleep | Settings → Sleep | On |
| Launch greeting | Runs automatically on start | — |

## Privacy

Cat Guardian is **100% on-device**:

- Audio is read from the Windows playback loopback, analysed in memory and discarded. It is never recorded, stored or sent anywhere, and the microphone is never opened.
- Clipboard access is limited to the local file list or raw image while copy-paste animations are enabled. Text, emoji and HTML are never read or logged.
- The browser bridge binds to `127.0.0.1` only and accepts requests from the extension's origin.
- Logs record status and counts — never page titles, file names, paths or audio.
- No telemetry, cloud API, account or model download.

## Project layout

```
main.py                 Double-click launcher (restarts inside .venv)
cat_guardian/           Application package
  __main__.py           App shell, settings window, tray
  pet.py                Pet window, animation state machine, timeline
  cat_shapes.py         Traced vector geometry and shared painters
  appearance.py         Coat palettes and live preview
  music.py              WASAPI loopback capture and music classifier
  music_moods.py        Dance catalog and Auto Vibe selection
  paste.py              Clipboard inspection and Ctrl+V observer
  watcher.py            Foreground app watcher
  bridge.py             Local bridge for the extension
  native.py             Windows window helpers
browser_extension/      Unpacked Chromium extension
tests/                  Offscreen Qt test suite
scripts/                Helper scripts
```

## Tests

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

126 tests cover the greeting, protection timeline, music engine, dance catalog, always-music sources, copy-paste delivery, appearance and settings. The UI tests run offscreen with Qt; no browser is needed.

## Build a standalone executable

```powershell
pip install pyinstaller
pyinstaller --noconfirm --clean --windowed --name CatGuardian --add-data "cat_guardian;cat_guardian" -m cat_guardian
```

If your PyInstaller version rejects `-m`, point it at the entry file instead:

```powershell
pyinstaller --noconfirm --clean --windowed --name CatGuardian --add-data "cat_guardian;cat_guardian" cat_guardian/__main__.py
```

Music capture needs the audio dependencies collected too:

```powershell
pyinstaller --noconfirm --clean --windowed --name CatGuardian ^
  --collect-all soundcard --collect-all cffi --collect-all numpy ^
  --add-data "cat_guardian;cat_guardian" -m cat_guardian
```

## Limitations

- Tab blocking supports Chromium browsers only and needs the unpacked extension.
- Tab-strip position is estimated, because browsers do not expose tab geometry; the close itself is done by tab ID, so the wrong tab is never closed.
- Music detection is a feature-based heuristic, deliberately biased to silence — a beatless track may not start a dance.
- Copy-paste animations react to `Ctrl+V` only; right-click → Paste and pastes into elevated windows are not observed.
- Windows 11 no longer renders the old wallpaper-layer trick reliably, so the cat uses a bottom-of-z-order window instead.

## License

This project does not ship a license file yet. If you plan to publish it, add a `LICENSE` (for example MIT) before making the repository public.
