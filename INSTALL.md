# Install and run Cat Guardian

A complete, beginner-friendly walkthrough for Windows. No administrator rights, no accounts, no cloud services.

**Space and time you need**

| Item | Size |
| --- | --- |
| Python installer download | ~30 MB |
| Project folder (source) | ~5 MB |
| `.venv` folder created in Step 4 | ~750 MB on disk |
| Python packages download in Step 5 | ~150–250 MB |
| Total disk after setup | ~800 MB |

The slowest step is Step 5 (usually 1–3 minutes on a normal connection). Everything else takes seconds.

---

## Step 1 — Check that Python is installed

1. Press the **Windows key**, type `powershell`, and click **Windows PowerShell**.
2. Type this and press **Enter**:

```powershell
py --version
```

3. You should see something like `Python 3.10.11`.

**If you see an error** ("py is not recognized", or nothing happens), install Python:

1. Go to <https://www.python.org/downloads/windows/>.
2. Click the latest **Python 3.x** "Windows installer (64-bit)".
3. Run the installer.
4. **Important:** tick **Add python.exe to PATH** at the bottom of the first screen.
5. Click **Install Now**, wait, then click **Close**.
6. Close the PowerShell window, open a new one, and run `py --version` again.

If `py` still does not work but `python --version` does, use `python` instead of `py` in the commands below. Cat Guardian needs **Python 3.10 or newer**.

---

## Step 2 — Get the app folder

If you downloaded the project as a ZIP from GitHub:

1. Right-click the downloaded `.zip` file.
2. Choose **Extract All…**, then **Extract**.
3. Remember the folder path, for example `C:\Users\YourName\Downloads\cat_guardian`.

If you cloned the repository with Git, use that folder instead.

Avoid running the app directly from inside the `.zip` preview — it must be a real extracted folder.

---

## Step 3 — Open PowerShell in the app folder

1. Open **File Explorer** and go into the `cat_guardian` folder (the one containing `main.py`).
2. Hold **Shift** and right-click on an empty area inside the folder.
3. Choose **Open PowerShell window here** (Windows 10) or **Open in Terminal** (Windows 11).

Alternative method that always works:

1. Open PowerShell from the Start menu.
2. Type `cd "` (with the quote), then drag the `cat_guardian` folder from File Explorer into the window, then close the quote and press **Enter**. Example:

```powershell
cd "C:\Users\YourName\Downloads\cat_guardian"
```

Confirm you are in the right place:

```powershell
dir main.py
```

You should see `main.py` listed.

---

## Step 4 — Create the virtual environment

A virtual environment ("venv") is a private copy of Python just for this app, so nothing else on your PC is affected.

```powershell
py -m venv .venv
```

- This creates a folder named `.venv` inside the project (about 750 MB).
- It takes 30–90 seconds. Nothing appears while it works — that is normal.
- If the `py` command fails, use `python -m venv .venv`.

Check that it worked:

```powershell
dir .venv\Scripts\python.exe
```

The file should be listed. If you get "cannot find path", repeat this step.

---

## Step 5 — Install the app's packages

Run these two commands, one at a time:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
```

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

What gets installed:

- **PySide6** — the windows and drawing engine (~100 MB download)
- **numpy** — math for the music analyzer (~15 MB)
- **soundcard** — reads what Windows is playing, without a microphone

You will see a lot of text. Wait until the prompt comes back and you see `Successfully installed …`. Warnings about `pip` being old are safe to ignore.

> Why not just `pip install`? The full path makes sure the packages go into the app's private environment, even if PowerShell blocked activation. If you prefer activation, run `.\.venv\Scripts\Activate.ps1` first; if Windows blocks it, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` and try again.

---

## Step 6 — Start the cat

```powershell
.\.venv\Scripts\python.exe -m cat_guardian
```

What you should see:

1. A small cat appears in the middle of the screen, raises a paw, and says **"Meow!"** with sound.
2. It walks back to a corner and sits down.
3. A cat icon appears in the **system tray** near the clock (you may need to click the `^` arrow to see it).

Leave the PowerShell window open while the app runs. To stop the cat, right-click its tray icon and choose **Quit**.

**From now on, just double-click `main.py`.** It finds the `.venv` folder automatically and starts without a console window. If something is missing it shows a dialog and writes the details to `%LOCALAPPDATA%\CatGuardian\cat_guardian.log`.

---

## Step 7 — Load the browser extension (for tab blocking)

1. Make sure Cat Guardian is already running.
2. Open Chrome (or Brave/Edge) and go to `chrome://extensions` (`brave://extensions`, `edge://extensions`).
3. Turn on **Developer mode** (top-right toggle).
4. Click **Load unpacked**.
5. Select the `browser_extension` folder inside the project folder.
6. Open the extension's **Details** and confirm it is enabled.

The default blocked site is `instagram.com`. To change the list, right-click the cat's tray icon → **Settings…** → edit **Blocked websites** → **Save settings**.

When a blocked site is opened in the front tab, the cat runs over, paws at the tab bar, and the extension closes that tab.

---

## Step 8 — Make it yours

Right-click the tray icon:

- **Cat Appearance…** — choose one of 19 coats or type any `#RRGGBB` colour. **Apply** updates the cat instantly.
- **Settings…** — blocked sites and apps, sleep delay, music reactions, copy-paste animations.
- **Hide cat / Show cat** — keep it off the desktop temporarily.
- **Info / Mouse Gestures…** — the full gesture list.
- **Quit** — stop the app completely.

### Optional: animate copied files

Copy-paste animations are **off by default**:

1. Tray icon → **Settings…**.
2. Tick **Animate copied files and images when I copy and paste them**.
3. Click **Save settings**.

Now copying a file makes the cat walk over and grab it, and pasting makes it deliver the file back to your pointer. One animation per action, even for many files.

### Optional: music dancing

Music reactions are on by default. Play something with a beat and the cat starts dancing after the activation delay. In **Settings → Music reactions** you can pick a style, tune intensity and sensitivity, or add always-music apps and websites.

---

## Step 9 — Everyday use

| Action | How |
| --- | --- |
| Start the app | Double-click `main.py` |
| Open settings | Right-click the tray cat → **Settings…** |
| Change colour | Right-click the tray cat → **Cat Appearance…** |
| Bring the cat back | Left-click or double-click the tray icon |
| Pause protection | Right-click the tray cat → **Pause protection** |
| Stop the app | Right-click the tray cat → **Quit** |

The cat sits behind your normal windows on purpose. To see it, minimise or move your windows, or look at the desktop.

---

## Uninstall, update, or reset

- **Update the code:** replace the project files with the new version, then run `Step 5` again (the `.venv` can be kept).
- **Reinstall cleanly:** delete the `.venv` folder, then repeat Step 4 and Step 5.
- **Reset all settings (colour, blocked lists, position):** right-click the tray cat → **Quit**, then delete the folder `%LOCALAPPDATA%\CatGuardian`. Paste that path into File Explorer's address bar and press Enter.

---

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `py` is not recognized | Python is missing or not on PATH. Redo Step 1 and tick **Add python.exe to PATH**. |
| `python` opens the Microsoft Store | Install Python from python.org instead, or turn off the Store app aliases in **Settings → Apps → Advanced app settings → App execution aliases**. |
| `Activate.ps1 cannot be loaded…` | Skip activation and use the full path `.\.venv\Scripts\python.exe …` as shown in Step 5. |
| `No module named PySide6` | Step 5 did not finish. Run the install command again and wait for `Successfully installed`. |
| Double-clicking `main.py` shows a dialog about dependencies | The `.venv` is missing or empty. Repeat Steps 4 and 5. |
| Double-clicking `main.py` does nothing at all | Open PowerShell in the folder and run `.\.venv\Scripts\python.exe -m cat_guardian` to see the real error, or read `%LOCALAPPDATA%\CatGuardian\cat_guardian.log`. |
| No cat on screen, but the app seems to run | The cat is on the desktop layer behind your windows. Minimise windows, or left-click the tray icon. |
| Tray icon not visible | Click the `^` arrow near the clock and drag the cat icon onto the taskbar to pin it. |
| Music animations never start | Check **Settings → Music reactions** is on, play audio on the default output device, and give it the activation delay (default 6 s). |
| Audio status says `Unavailable (audio device)` | No default speaker is selected, or the device is busy. Check Windows sound settings and restart the app. |
| Extension does not close tabs | Cat Guardian must be running, the extension loaded, and the site listed in **Blocked websites**. Reload the extension after updating its files. |
| Copy-paste animation does not play | Enable it in **Settings → Copy-paste deliveries**, and use Ctrl+C / Ctrl+V (not right-click → Paste). |

---

## What the app never does

- It does not need administrator rights.
- It does not send your audio, clipboard, file names, or browsing titles anywhere.
- It does not use the microphone — music detection listens to what Windows is already playing.
- It does not open a network port beyond `127.0.0.1` (this computer only), used by the browser extension.
