# SnipAnnotate — Installation Guide (v9.9)

A single-file Windows screen-capture + annotation tool (`snipannotate.py`).
No installer, no registry — one Python file plus a few pip packages.

Repo: https://github.com/sushil-bhattacharjee/SnipAnnotate.git

---

## 1. Requirements

| What | Version | Why |
|---|---|---|
| Windows | 10 / 11 | capture, clipboard, and OCR use Windows APIs |
| Python | 3.9+ (3.13 tested) | from python.org — **avoid the Microsoft Store build** |
| git | any | *optional* — only to clone/pull the repo |
| Pillow | any current | screenshots, image handling — **required** |
| opencv-python + numpy | any current | ⏺ screen recording — *optional* |
| winrt (5 packages) | any current | 🔤 OCR (Windows built-in engine) — *optional* |

tkinter ships with the python.org installer — nothing extra for the UI.

## 2. Install Python (skip if already installed)

Windows has TWO ways to invoke Python, and machines differ in which works:

- `python` — on PATH only if "Add to PATH" was ticked at install time
- `py` — the **Windows py launcher**, installed by the python.org installer
  regardless of the PATH choice

They run the same interpreter. **Wherever this guide says `python`, use `py`
instead if `python` errors or opens the Microsoft Store** — same arguments,
e.g. `py -m venv .venv`, `py snipannotate.py`. To pick a version explicitly:
`py -3.13 ...`.

Check what you have:

```powershell
python --version
py --version
```

If BOTH fail:

1. Download the latest 3.x from https://www.python.org/downloads/windows/
2. Run the installer → **tick "Add python.exe to PATH"** → Install Now.
3. Close and reopen the terminal, re-check `python --version`.

Store-build warning: the Store Python hides `pythonw.exe` behind an alias and
breaks the shortcut steps in §7. If `where pythonw` shows a path under
`WindowsApps`, install from python.org instead.

## 3. Get the code

**Option A — git (recommended, easy updates):**

```powershell
# install git if missing (or download from https://git-scm.com/download/win)
winget install --id Git.Git -e

git clone https://github.com/sushil-bhattacharjee/SnipAnnotate.git C:\Apps\SnipAnnotate
cd C:\Apps\SnipAnnotate
# later, to update:  git pull
```

**Option B — no git:** GitHub → Code → Download ZIP → extract to
`C:\Apps\SnipAnnotate`.

(Any folder works; the guide uses `C:\Apps\SnipAnnotate` throughout — adjust
if yours differs.)

## 4. Create a virtual environment (skip if you have one)

Keeps the app's packages out of the system Python:

```powershell
cd C:\Apps\SnipAnnotate
python -m venv .venv                 # if python errors: py -m venv .venv
.\.venv\Scripts\Activate.ps1        # cmd.exe instead: .venv\Scripts\activate.bat
```

Once the venv is **active** (prompt shows `(.venv)`), plain `python` and
`pip` always work — the python/py ambiguity only exists outside a venv.

If PowerShell refuses to run the activate script:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Everything below assumes the venv is active (prompt shows `(.venv)`).

## 5. Install the packages

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

## 6. Run

```powershell
python snipannotate.py       # with console (shows errors — use while testing)
pythonw snipannotate.py      # no console window (daily use)
```

`python` not found outside the venv? Use `py snipannotate.py` /
`pyw snipannotate.py` (the launcher's console-less twin).

With a venv, the no-console interpreter is
`C:\Apps\SnipAnnotate\.venv\Scripts\pythonw.exe` — that exact path is what the
shortcuts below must target so the venv's packages are found.

## 7. Make it behave like a real Windows app

Goal: desktop icon, findable in Windows 11 Search, pinnable to the taskbar,
visible in Task Manager → Startup apps.

### 7.0 Base config (needed by every option below)

The only shared ingredient is the interpreter path and the script path —
set them once in the PowerShell session, then run whichever option you want:

Run this **from inside the app folder** (the one containing
`snipannotate.py`) — the path is detected, not typed:

```powershell
cd <your SnipAnnotate folder>                # e.g. C:\softwares\SnipAnnotate
$app = (Get-Location).Path
$pyw = "$app\.venv\Scripts\pythonw.exe"    # no venv? use (Get-Command pythonw.exe).Source
if (-not (Test-Path $pyw)) { throw "pythonw not found at $pyw - fix before creating shortcuts" }
$ws  = New-Object -ComObject WScript.Shell

function New-SnipShortcut($where) {
    $lnk = $ws.CreateShortcut($where)
    $lnk.TargetPath       = $pyw
    $lnk.Arguments        = "`"$app\snipannotate.py`""
    $lnk.WorkingDirectory = $app
    if (Test-Path "$app\snipannotate.ico") { $lnk.IconLocation = "$app\snipannotate.ico" }
    $lnk.Save()
}
```

v9.9+ writes `snipannotate.ico` next to the script on first run — **run the
app once before creating shortcuts** so they pick up the proper icon.

```powershell
```

Options a and b are independent — do either or both.

### 7.1a Enable W11 Search (Start Menu entry)

```powershell
New-SnipShortcut "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\SnipAnnotate.lnk"
```

Press `Win` and type "SnipAnnotate" — it appears like an installed app.
Remove later by deleting that `.lnk`.

### 7.1b Desktop shortcut

```powershell
New-SnipShortcut "$([Environment]::GetFolderPath('Desktop'))\SnipAnnotate.lnk"
```

Manual alternative (no PowerShell): right-click Desktop → New → Shortcut →
location `C:\Apps\SnipAnnotate\.venv\Scripts\pythonw.exe "C:\Apps\SnipAnnotate\snipannotate.py"`
→ name it SnipAnnotate.

### 7.2 Pin to taskbar (requires v9.9+ and the 7.1a Start-Menu shortcut)

Background: taskbar pins are keyed by an app identity (AppUserModelID).
`pythonw.exe` has none of its own, so a plain pin collapses onto Python's
registered entry — that is the "pin opens IDLE" symptom. Two things fix it,
and both are needed:

1. **The app declares its identity** — v9.9+ does this at startup
   (`hiTech.SnipAnnotate`). Nothing to do beyond running v9.9 or later.
2. **The Start-Menu shortcut carries the SAME identity** — one-time stamp:

```powershell
$code = @"
using System;
using System.Runtime.InteropServices;

namespace SnipPin {
  [StructLayout(LayoutKind.Sequential, Pack = 4)]
  public struct PropVariant {
    public ushort vt; ushort r1; ushort r2; ushort r3;
    public IntPtr p; int p2;
    public static PropVariant FromString(string s) {
      var v = new PropVariant();
      v.vt = 31; // VT_LPWSTR
      v.p = Marshal.StringToCoTaskMemUni(s);
      return v;
    }
  }

  [StructLayout(LayoutKind.Sequential)]
  public struct PropertyKey {
    public Guid fmtid; public uint pid;
    public PropertyKey(Guid f, uint p) { fmtid = f; pid = p; }
  }

  [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"),
   InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IPropertyStore {
    int GetCount(out uint cProps);
    int GetAt(uint iProp, out PropertyKey pkey);
    int GetValue(ref PropertyKey key, out PropVariant pv);
    int SetValue(ref PropertyKey key, ref PropVariant pv);
    int Commit();
  }

  [ComImport, Guid("0000010b-0000-0000-C000-000000000046"),
   InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IPersistFile {
    void GetClassID(out Guid pClassID);
    [PreserveSig] int IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName,
              [MarshalAs(UnmanagedType.Bool)] bool fRemember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
    void GetCurFile(out IntPtr ppszFileName);
  }

  public static class Stamp {
    public static void SetAumid(string lnk, string aumid) {
      var t = Type.GetTypeFromCLSID(new Guid("00021401-0000-0000-C000-000000000046"));
      object link = Activator.CreateInstance(t);
      ((IPersistFile)link).Load(lnk, 2);
      var store = (IPropertyStore)link;
      var key = new PropertyKey(new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5);
      var val = PropVariant.FromString(aumid);
      int hr = store.SetValue(ref key, ref val);
      if (hr != 0) throw new COMException("SetValue failed", hr);
      store.Commit();
      ((IPersistFile)link).Save(lnk, true);
    }
  }
}
"@
Add-Type -TypeDefinition $code
[SnipPin.Stamp]::SetAumid(
  "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\SnipAnnotate.lnk",
  "hiTech.SnipAnnotate")
```

3. Then pin: `Win` → search SnipAnnotate → right-click → **Pin to taskbar**.
   The pin now launches SnipAnnotate with its own icon — no IDLE, no
   grouping under Python.

Re-run the stamp after ever re-creating the Start-Menu shortcut (§7.0's
function overwrites the property).

### 7.3 Auto-start at logon (Task Manager → Startup apps)

Independent of a/b — uses the same base config from 7.0:

```powershell
New-SnipShortcut "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\SnipAnnotate.lnk"
```

Manage (enable/disable) it afterwards in **Task Manager → Startup apps** —
it's listed there like any application. Manual alternative: `Win+R` →
`shell:startup` → drop the shortcut in.

## 8. Upgrade

```powershell
cd C:\Apps\SnipAnnotate
git pull            # ZIP users: overwrite snipannotate.py with the new file
```

Restart the app. State is minimal: the error log lives at
`%USERPROFILE%\snipannotate_error.log` — attach it when reporting problems
(every capture, crop, OCR, copy, and failure is logged there since v9.x).

## 9. Uninstall (complete removal)

Nothing is installed system-wide — no registry entries, no services. Removal
is: unpin, delete the shortcuts, delete the folder (the `.venv` and all
packages live inside it), and optionally the log.

```powershell
# 1. Unpin from taskbar first (right-click the pinned icon → Unpin), then:

# 2. Shortcuts — Start Menu (W11 Search), Desktop, Startup
Remove-Item -ErrorAction SilentlyContinue `
  "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\SnipAnnotate.lnk", `
  "$([Environment]::GetFolderPath('Desktop'))\SnipAnnotate.lnk", `
  "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\SnipAnnotate.lnk"

# 3. The app folder — includes .venv, all pip packages, the .ico, the code
Remove-Item -Recurse -Force 'C:\softwares\SnipAnnotate'   # adjust to your folder

# 4. Optional: the error log
Remove-Item -ErrorAction SilentlyContinue "$env:USERPROFILE\snipannotate_error.log"
```

Notes:

- Deleting the folder removes the virtual environment completely — the venv
  is just a directory; pip packages installed inside it leave nothing behind.
- If you installed the winrt/opencv packages **without** a venv (straight into
  the system Python), remove them from there instead:
  `pip uninstall pillow opencv-python numpy winrt-Windows.Foundation winrt-Windows.Foundation.Collections winrt-Windows.Graphics.Imaging winrt-Windows.Media.Ocr winrt-Windows.Storage.Streams`
- Python itself and git are shared tools — uninstall via
  **Settings → Apps → Installed apps** only if nothing else uses them.
- A stale pinned taskbar icon after deletion is cosmetic — right-click →
  Unpin removes it.

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `python` opens the Microsoft Store / not found | use `py` instead (same args) — or reinstall from python.org with "Add to PATH" |
| `python` works in one terminal, not another | PATH differs per shell — `py` works everywhere; inside an activated venv `python` always works |
| Activate.ps1 blocked | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Shortcut starts but app can't find pillow | shortcut targets system `pythonw.exe`, not the venv's — fix TargetPath (§7.1) |
| Black console window at logon | shortcut targets `python.exe` — use `pythonw.exe` |
| "Pillow is required" | `pip install pillow` (inside the venv) |
| Record button says opencv missing | `pip install opencv-python numpy` |
| OCR popup about winrt | run the 5-package winrt line (§5), restart the app |
| OCR crash mentioning async/foundation | old winrt install — reinstall all 5 packages together (they must match) |
| Pin opens IDLE / generic Python icon | need v9.9+ AND the §7.2 shortcut stamp — both, then re-pin |
| System-wide copy/paste stops working | fixed in v3+ — update if on v2 |
| Snip overlay on one monitor only | fixed in v9.2+ — overlay spans all screens |
| Multiple Pythons installed | run with the explicit one: `py -3.13 snipannotate.py` |

## 11. Feature ↔ dependency map

| Feature | Needs |
|---|---|
| Snip / Freeform / Full Screen / annotate / layers / crop / trim | pillow only |
| Copy image + auto-copy on capture | pillow only (native Win32 clipboard) |
| 🔤 OCR + in-picture text selection | the 5 winrt packages |
| ⏺ Record (MP4/AVI/GIF) | opencv-python + numpy |
