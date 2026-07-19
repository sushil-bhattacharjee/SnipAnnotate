# SnipAnnotate — Install & Run on Windows

A Snipping-Tool-style capture and annotation app: region / full-screen snips,
pen, highlighter, arrows, shapes, movable text, screen recording, and
copy-to-clipboard.

**Repository:** <https://github.com/sushil-bhattacharjee/SnipAnnotate>

---

## Quick start

Already have Python? Three commands:

```powershell
git clone https://github.com/sushil-bhattacharjee/SnipAnnotate.git
cd SnipAnnotate
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install pillow
python .\snipannotate.py
```

Then jump to **[Step 5 — Desktop icon & autostart](#5-desktop-icon-start-menu-and-autostart)**.

Everything below is the same thing, explained for someone doing it for the first
time.

---

## 1. Install Python

1. Download the latest **Windows installer (64-bit)** from
   <https://www.python.org/downloads/windows/> (Python 3.10 or newer).
2. On the first screen of the installer, **tick “Add python.exe to PATH”**
   before clicking *Install Now*. This one checkbox saves a lot of pain.

Open **PowerShell** (`Win` → type `powershell` → Enter) and check:

```powershell
py --version          # if this fails, try:
python --version
```

Either one printing a version (e.g. `Python 3.14.6`) means you are good.

> **Why two commands?** `py` is the *Python launcher*; `python` is whatever is
> first on your PATH. Depending on how Python was installed, **either one may be
> missing** — that is normal. Use whichever answers, and use it consistently
> below. This guide writes `python`; substitute `py` if that is the one that
> works for you.

<details>
<summary><b>⚠ “Python was not found; run without arguments to install from the Microsoft Store…”</b> — even though Python IS installed</summary>

Windows 11 ships an **App Execution Alias** that hijacks the name `python` and
redirects it to the Microsoft Store. Your Python is fine; the name is being
intercepted.

**Fix it:**
1. Press `Win`, search **“Manage app execution aliases”**
   *(Settings → Apps → Advanced app settings → App execution aliases)*
2. Switch **`python.exe`** and **`python3.exe`** to **Off**
3. **Close and reopen PowerShell**

**Or simply use `py` instead** — the launcher is never affected by the alias.
</details>

<details>
<summary><b>⚠ “The term 'py' is not recognized…”</b></summary>

This machine has no Python launcher (common with a Microsoft Store install).
Just use **`python`** everywhere instead of `py`. They do the same job.
</details>

---

## 2. Get the code

```powershell
# Pick any folder you like — this guide uses C:\softwares as the example.
mkdir C:\softwares -Force
cd C:\softwares

git clone https://github.com/sushil-bhattacharjee/SnipAnnotate.git
cd SnipAnnotate
```

**No git?** Install it from <https://git-scm.com/download/win>, or on the GitHub
page click **Code → Download ZIP**, extract it, and `cd` into the folder.

> The install path does **not** matter. Every command below works from wherever
> you put it — nothing is hard-coded.

---

## 3. Create a virtual environment

A virtual environment keeps these packages out of your system Python.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Your prompt now begins with `(.venv)`.

<details>
<summary><b>⚠ “Unable to copy … venvwlauncher.exe to … pythonw.exe”</b> — re-creating a venv over an existing install</summary>

**SnipAnnotate is currently running** and is holding `pythonw.exe` open, so
`venv` cannot overwrite it. If you set up autostart, Windows launched it at
login — and because it runs without a console window, it is easy to miss.

Close it first:

```powershell
Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2
```

Then run the `venv` command again.
</details>

<details>
<summary><b>⚠ “Activate.ps1 cannot be loaded because running scripts is disabled on this system”</b></summary>

PowerShell blocks scripts by default. Allow them **for your own user only**:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

Answer **`Y`** when prompted. `RemoteSigned` means *“my own local scripts may
run; anything downloaded from the internet must be signed”* — the standard
developer setting. It does not affect other users or the machine.

**If company policy forbids changing it,** you do not need to activate at all.
Use the cmd activator:

```powershell
.\.venv\Scripts\activate.bat
```

…or skip activation entirely and call the venv's Python directly — this always
works, and it is exactly what the shortcut does anyway:

```powershell
.\.venv\Scripts\python.exe -m pip install pillow
.\.venv\Scripts\pythonw.exe .\snipannotate.py
```
</details>

---

## 4. Install the dependencies and run

```powershell
python -m pip install --upgrade pip

pip install pillow                    # required
pip install opencv-python numpy       # optional — only for the ⏺ Record button
```

| Package | Needed for | Required? |
|---|---|---|
| **pillow** | screen capture, drawing, saving, clipboard | **Yes** — the app exits without it |
| opencv-python, numpy | the ⏺ **Record** button (MP4 / AVI / GIF) | No — snipping works fine without them |
| tkinter | the window itself | Ships with Python — nothing to install |

Run it:

```powershell
python .\snipannotate.py
```

| Button | What it does |
|---|---|
| **⬛ New Snip** | dims the screen — drag a rectangle to capture |
| **🖥 Full Screen** | grabs everything (all monitors) |
| **⏺ Record** | records the screen *(needs opencv)* |
| **Screen** | choose which monitor to capture |
| **📂 Open…** | annotate an existing image file |
| Pen · Highlight · Line · Arrow · Rect · Ellipse · Text | annotation tools |
| **💾 Save** (`Ctrl+S`) · **📋 Copy** (`Ctrl+C`) | write a PNG · copy to clipboard |
| **↩ Undo** (`Ctrl+Z`) · **🗑 Clear** | undo · start over |
| **＋ － ⤢ Fit** | zoom the view |

After a capture the window comes forward and the image is zoomed to fit, so every
tool is reachable immediately.

---

## 5. Desktop icon, Start-menu entry, and autostart

Run the block below **once, from inside the SnipAnnotate folder**. It creates all
three, works no matter where you installed the app, and handles a
OneDrive-redirected Desktop:

```powershell
$app = (Get-Location).Path
$exe = "$app\.venv\Scripts\pythonw.exe"

if (-not (Test-Path $exe)) {
  Write-Warning "pythonw.exe not found — see the troubleshooting box below."
} else {
  $desk    = [Environment]::GetFolderPath('Desktop')          # OneDrive-safe
  $start   = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
  $targets = @(
    "$desk\SnipAnnotate.lnk",                                  # desktop icon
    "$start\SnipAnnotate.lnk",                                 # Windows Search / Start
    "$start\Startup\SnipAnnotate.lnk"                          # launch at login
  )

  foreach ($lnk in $targets) {
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $s.TargetPath       = $exe
    $s.Arguments        = "$app\snipannotate.py"
    $s.WorkingDirectory = $app
    $s.Description      = "SnipAnnotate — screen capture and annotation"
    $s.Save()
    if (Test-Path $lnk) { Write-Host "OK      $lnk" } else { Write-Warning "FAILED  $lnk" }
  }
}
```

You now have:

* a **Desktop icon** — double-click to launch
* an entry in **Windows Search** (`Win` → type *SnipAnnotate*); right-click it to
  *Pin to Start* or *Pin to taskbar*
* **automatic launch at every login**

> **Why `pythonw.exe` and not `python.exe`?** They are the same interpreter, but
> `pythonw` runs **without a black console window**. `python.exe` would drag a
> useless console along with the app every time.

**Turn off autostart** (the app stays installed):

```powershell
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\SnipAnnotate.lnk"
```

…or Settings → Apps → **Startup** → toggle it off.

<details>
<summary><b>⚠ The script says “pythonw.exe not found”</b></summary>

Some Python installs (notably from the Microsoft Store) create a virtual
environment without `pythonw.exe`. Check what you actually have:

```powershell
dir .\.venv\Scripts\*.exe
```

**Best fix — rebuild the venv with a python.org Python:**

```powershell
deactivate
Remove-Item -Recurse -Force .venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install pillow
Test-Path .\.venv\Scripts\pythonw.exe      # must now print True
```

Then run the shortcut block again.

**Or accept a console window** — point the shortcut at `python.exe` and start it
minimised: in the block above, replace the `$exe` line with

```powershell
$exe = "$app\.venv\Scripts\python.exe"
```

and add `$s.WindowStyle = 7` before `$s.Save()`.
</details>

<details>
<summary><b>⚠ “Unable to save shortcut … DirectoryNotFoundException” / the Desktop icon never appears</b></summary>

Your Desktop is **redirected to OneDrive**, so `C:\Users\<YOU>\Desktop` does not
exist. The script above already handles this — it asks Windows where the Desktop
really is:

```powershell
[Environment]::GetFolderPath('Desktop')
```

If you are creating a shortcut by hand, use whatever *that* prints. Never
hard-code `$env:USERPROFILE\Desktop`.
</details>

<details>
<summary><b>Prefer to create the shortcut by hand?</b></summary>

1. **Right-click the Desktop → New → Shortcut**
2. Paste this as the location, replacing the path with your own:

   ```
   C:\Users\<YOU>\softwares\SnipAnnotate\.venv\Scripts\pythonw.exe C:\Users\<YOU>\softwares\SnipAnnotate\snipannotate.py
   ```

3. Name it **SnipAnnotate** → Finish
4. **Right-click it → Properties → Start in:** set to your SnipAnnotate folder
5. To autostart: press `Win + R`, type **`shell:startup`**, Enter, and **copy the
   shortcut into that folder**
</details>

> **Why not a Windows Service?** A service runs with no desktop session, so it
> cannot show a window or capture the screen. For a GUI tool, *autostart at
> login* is the correct mechanism — a service would start and be useless.

---

## 6. Updating

```powershell
cd C:\softwares\SnipAnnotate          # ← your folder

# Close the app first if it is running (autostart), or git may fail to
# overwrite files that are in use:
Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process -Force

git pull
.\.venv\Scripts\Activate.ps1
pip install --upgrade pillow           # only if the requirements changed
```

Your shortcuts keep working — they point at the same files.

---

## 7. Troubleshooting

<details>
<summary><b>“Python was not found… install from the Microsoft Store” (but Python is installed)</b></summary>

Windows 11's **App Execution Alias** is hijacking `python`.
Settings → Apps → **Advanced app settings → App execution aliases** → switch
**`python.exe`** and **`python3.exe`** **Off**, then reopen PowerShell.
Or just use **`py`** — the launcher is never affected.
</details>

<details>
<summary><b>“The term 'py' is not recognized”</b></summary>

No Python launcher on this machine (typical of a Store install). Use **`python`**
instead — same result.
</details>

<details>
<summary><b>“Activate.ps1 cannot be loaded because running scripts is disabled”</b></summary>

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Answer `Y`, then activate again. Cannot change the policy? Use
`.\.venv\Scripts\activate.bat`, or skip activation and call
`.\.venv\Scripts\python.exe` directly.
</details>

<details>
<summary><b>“Pillow is required: pip install pillow”, then it exits</b></summary>

The virtual environment is not active, or Pillow went into a different Python.

```powershell
.\.venv\Scripts\Activate.ps1
pip install pillow
```
</details>

<details>
<summary><b>“The term '.\.venv\Scripts\pythonnw.exe' is not recognized”</b></summary>

Typo — it is **`pythonw.exe`**, with one `n`.
</details>

<details>
<summary><b>“can't open file '…\snip_annotate.py'”</b></summary>

The script in this repository is **`snipannotate.py`** — no underscore.
Run `dir *.py` to confirm the exact name.
</details>

<details>
<summary><b>The ⏺ Record button does nothing</b></summary>

Recording is optional and needs OpenCV:

```powershell
pip install opencv-python numpy
```
</details>

<details>
<summary><b>Snips are blurry, or the drag rectangle lands in the wrong place</b></summary>

A display-scaling (DPI) issue. The app sets per-monitor DPI awareness on Windows;
if it persists, right-click `pythonw.exe` → **Properties → Compatibility →
Change high DPI settings → Override high DPI scaling behaviour → Application**.
</details>

<details>
<summary><b>“Access to the path … is denied” when deleting or re-installing</b></summary>

The app is **running** — almost certainly started automatically at login. A
running `pythonw.exe` holds its own executable plus `PIL`, `numpy` and `cv2`
open, so those files cannot be deleted or overwritten.

```powershell
Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2
```

Then retry. (If it persists, close the SnipAnnotate window from the taskbar, or
Task Manager → **pythonw.exe** → *End task*.)
</details>

<details>
<summary><b>Double-clicking the shortcut does nothing</b></summary>

The target probably points at a file that is not there. Check it:

```powershell
$s = (New-Object -ComObject WScript.Shell).CreateShortcut(
       [Environment]::GetFolderPath('Desktop') + '\SnipAnnotate.lnk')
$s.TargetPath
Test-Path $s.TargetPath        # must print True
```

If it prints `False`, re-run the shortcut block in Step 5 from inside the
SnipAnnotate folder.
</details>

---

## 8. Uninstall

Edit the **first line** to your install folder, then run the whole block:

```powershell
$app = "C:\softwares\SnipAnnotate"          # ← your folder

# 1. Stop the app. It is almost certainly running (autostart), and a running
#    pythonw.exe locks its own files — deletion fails with "Access denied".
Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2

# 2. Remove the shortcuts
$start = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
Remove-Item "$start\Startup\SnipAnnotate.lnk" -ErrorAction SilentlyContinue
Remove-Item "$start\SnipAnnotate.lnk"         -ErrorAction SilentlyContinue
Remove-Item ([Environment]::GetFolderPath('Desktop') + '\SnipAnnotate.lnk') -ErrorAction SilentlyContinue

# 3. Remove the app (step out of the folder first — you cannot delete the
#    directory you are standing in)
cd (Split-Path $app)
Remove-Item -Recurse -Force $app

Test-Path $app        # must print False
```

Nothing is written outside that folder and those three shortcuts.

> `Stop-Process` is not optional. Skip it and you get a wall of *“Access to the
> path … is denied”* for `pythonw.exe`, `PIL`, `numpy` and `cv2` — every file the
> running app has open.
