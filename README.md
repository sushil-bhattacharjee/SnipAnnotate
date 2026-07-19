# SnipAnnotate — Installation Guide (v9.8)

A single-file Windows screen-capture + annotation tool (`snipannotate.py`).
No installer, no registry — one Python file plus a few pip packages.

---

## 1. Requirements

| What | Version | Why |
|---|---|---|
| Windows | 10 / 11 | capture, clipboard, and OCR use Windows APIs |
| Python | 3.9+ (3.13 tested) | from python.org, **tick "Add to PATH"** — avoid the Microsoft Store build |
| Pillow | any current | screenshots, image handling — **required** |
| opencv-python + numpy | any current | ⏺ screen recording — *optional* |
| winrt (5 packages) | any current | 🔤 OCR (Windows built-in engine) — *optional* |

tkinter ships with the python.org installer — nothing to install for the UI.

## 2. Install

```powershell
# required
pip install pillow

# optional: screen recording (MP4/AVI/GIF)
pip install opencv-python numpy

# optional: OCR / in-picture text selection (copy this whole line)
pip install winrt-Windows.Foundation winrt-Windows.Foundation.Collections winrt-Windows.Graphics.Imaging winrt-Windows.Media.Ocr winrt-Windows.Storage.Streams
```

Skipping the optional packages is fine — snipping and annotation work without
them; the Record and OCR buttons will tell you what's missing if clicked.

## 3. Run

```powershell
python snipannotate.py       # with console (shows errors — use while testing)
pythonw snipannotate.py      # no console window (daily use)
```

## 4. Auto-start at logon (optional)

One-time PowerShell (adjust the script path):

```powershell
$pyw = (Get-Command pythonw.exe).Source
$ws  = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\SnipAnnotate.lnk")
$lnk.TargetPath = $pyw
$lnk.Arguments  = '"C:\Users\Sushi\test\snipannotate.py"'
$lnk.Save()
```

Manual alternative: `Win+R` → `shell:startup` → New → Shortcut →
`pythonw.exe "C:\Users\Sushi\test\snipannotate.py"`.

Manage it later in **Task Manager → Startup apps**. If `pythonw.exe` isn't
found, your Python is the Store build — use the full path, e.g.
`%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe`.

## 5. Upgrade

Replace `snipannotate.py` with the new version and restart the app.
State is minimal: the error log lives at
`%USERPROFILE%\snipannotate_error.log` — attach it when reporting problems
(every capture, crop, OCR, copy, and failure is logged there since v9.x).

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| Black console window at logon | Shortcut targets `python.exe` — use `pythonw.exe` |
| "Pillow is required" | `pip install pillow` |
| Record button says opencv missing | `pip install opencv-python numpy` |
| OCR popup about winrt | run the 5-package winrt line above, restart the app |
| OCR crash mentioning async/foundation | old winrt install — reinstall all 5 packages together (they must match) |
| System-wide copy/paste stops working | fixed in v3+ (clipboard ownership released on exit) — update if on v2 |
| Snip overlay on one monitor only | fixed in v9.2+ — overlay spans all screens |
| Multiple Pythons installed | run with the explicit one: `py -3.13 snipannotate.py` |

## 7. Feature ↔ dependency map

| Feature | Needs |
|---|---|
| Snip / Freeform / Full Screen / annotate / layers / crop / trim | pillow only |
| Copy image + auto-copy on capture | pillow only (native Win32 clipboard) |
| 🔤 OCR + in-picture text selection | the 5 winrt packages |
| ⏺ Record (MP4/AVI/GIF) | opencv-python + numpy |
