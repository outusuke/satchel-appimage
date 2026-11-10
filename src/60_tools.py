LINUXDEPLOY_URL = "https://github.com/linuxdeploy/linuxdeploy/releases"


LD_DL = "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-{arch}.AppImage"


LD_HOME = os.path.expanduser("~/.local/bin/linuxdeploy")


def find_linuxdeploy():
    """The linuxdeploy program: on PATH, or the copy Satchel installed in ~/.local/bin (or one in ~/bin). None if none."""
    for n in ("linuxdeploy", f"linuxdeploy-{platform.machine()}.AppImage"):
        found = shutil.which(n)
        if found: return found
    return next((p for p in (LD_HOME, os.path.expanduser("~/bin/linuxdeploy")) if os.access(p, os.X_OK)), None)


def find_linuxdeploy_download():
    """A linuxdeploy file you already downloaded into ~/Downloads (newest first, no plugins), or None."""
    d = os.path.expanduser("~/Downloads")
    try: names = [f for f in os.listdir(d) if f.lower().startswith("linuxdeploy") and "plugin" not in f.lower()]
    except OSError: return None
    files = [f"{d}/{n}" for n in names if os.path.isfile(f"{d}/{n}")]
    files.sort(key=lambda p: (platform.machine() not in os.path.basename(p), -os.path.getmtime(p)))
    return files[0] if files else None


def download_linuxdeploy(dest=None, opener=None):
    """Download linuxdeploy for this machine and install it (~/.local/bin/linuxdeploy). Returns where it went."""
    with tempfile.TemporaryDirectory(prefix="satchel-dl-") as d:
        download(LD_DL.format(arch=platform.machine()), f"{d}/l", opener)
        if appimage_offset(f"{d}/l") is None: raise BuildError("What was downloaded is not an AppImage, so it was not installed.")
        return install_tool(f"{d}/l", dest or LD_HOME)


RUNTIME_URL = "https://github.com/AppImage/type2-runtime/releases"


TOOL_DL = "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-{arch}.AppImage"


RUNTIME_DL = "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-{arch}"


MAX_DOWNLOAD = 200 * 1024 * 1024


RUNTIME_DIR = os.path.expanduser("~/.local/share/satchel")


TOOL_HOME = os.path.expanduser("~/.local/bin/appimagetool")


def find_tool():
    return os.environ.get("APPIMAGETOOL") or shutil.which("appimagetool") or next(
        (p for p in (TOOL_HOME, os.path.expanduser("~/bin/appimagetool")) if os.access(p, os.X_OK)), None)


def find_download():
    """An appimagetool file you already downloaded into ~/Downloads (newest first), or None."""
    d = os.path.expanduser("~/Downloads")
    try: names = [f for f in os.listdir(d) if f.lower().startswith("appimagetool")]
    except OSError: return None
    files = [f"{d}/{n}" for n in names if os.path.isfile(f"{d}/{n}")]
    files.sort(key=lambda p: (platform.machine() not in os.path.basename(p), -os.path.getmtime(p)))
    return files[0] if files else None


def install_tool(src, dest=TOOL_HOME):
    """Copy a file you downloaded to ~/.local/bin/appimagetool and make it executable. Refuses anything that isn't a program."""
    try:
        with open(src, "rb") as f: magic = f.read(4)
    except OSError as e: raise BuildError(f"Could not read that file: {e}")
    if magic != b"\x7fELF": raise BuildError("That is not a program file (an AppImage starts with ELF). Did the download save a web page?")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".new"
    shutil.copyfile(src, tmp); os.chmod(tmp, 0o755); os.replace(tmp, dest)
    return dest


def download(url, dest, opener=None):
    """Fetch an https address into the file dest (at most MAX_DOWNLOAD bytes). Raises BuildError; leaves no partial file."""
    import urllib.request
    opener = opener or urllib.request.urlopen
    if not url.startswith("https://"): raise BuildError("Only https downloads are allowed.")
    try:
        with opener(urllib.request.Request(url, headers={"User-Agent": "satchel"}), timeout=30) as r, open(dest, "wb") as f:
            if not r.geturl().startswith("https://"): raise BuildError("The download was redirected to an address that is not https.")
            total = 0
            while True:
                chunk = r.read(65536)
                if not chunk: break
                total += len(chunk)
                if total > MAX_DOWNLOAD: raise BuildError("The download is larger than expected, so it was stopped.")
                f.write(chunk)
    except BuildError:
        if os.path.exists(dest): os.unlink(dest)
        raise
    except OSError as e:
        if os.path.exists(dest): os.unlink(dest)
        raise BuildError(f"Could not download {url}\n{e}\nUse 'Use a downloaded file…' instead.")


def download_tool(dest=None, opener=None):
    """Download appimagetool for this machine and install it. Returns where it went."""
    with tempfile.TemporaryDirectory(prefix="satchel-dl-") as d:
        download(TOOL_DL.format(arch=platform.machine()), f"{d}/t", opener)
        if appimage_offset(f"{d}/t") is None: raise BuildError("What was downloaded is not an AppImage, so it was not installed.")
        return install_tool(f"{d}/t", *([dest] if dest else []))


def download_runtime(dest=None, opener=None):
    """Download the AppImage runtime for this machine and install it. Returns where it went."""
    with tempfile.TemporaryDirectory(prefix="satchel-dl-") as d:
        download(RUNTIME_DL.format(arch=platform.machine()), f"{d}/r", opener)
        return install_runtime(f"{d}/r", dest)


def runtime_problem(path):
    """Why path can't be used as the AppImage runtime, or None. A runtime is a small type 2 ELF program; a complete
    AppImage (appimagetool itself, say) also starts with that ELF but has a whole program attached, and using it
    would make every AppImage you build start that program instead of yours."""
    try:
        with open(path, "rb") as f: h = f.read(64)
    except OSError as e: return f"Could not read it: {e}"
    if h[:4] != b"\x7fELF": return "It is not a program file (it should start with ELF). Did the download save a web page?"
    if h[8:11] != b"AI\x02": return "It is not an AppImage runtime (no type 2 marker)."
    if appimage_offset(path) is not None: return "It is a complete AppImage (such as appimagetool itself), not the small runtime file."
    return None


def find_runtime(root=None):
    """The AppImage runtime file, so appimagetool does not have to download it: yours, or one shipped inside the tool."""
    for p in (os.environ.get("APPIMAGE_RUNTIME"), f"{RUNTIME_DIR}/runtime-{platform.machine()}",
              root and f"{root}/usr/lib/appimagekit/runtime"):
        if p and os.path.isfile(p) and not runtime_problem(p): return p
    return None


def find_runtime_download():
    """A runtime-<arch> file you already downloaded into ~/Downloads (newest first), or None."""
    d = os.path.expanduser("~/Downloads")
    try: names = [f for f in os.listdir(d) if f.lower().startswith("runtime") and platform.machine() in f]
    except OSError: return None
    files = [f"{d}/{n}" for n in names if os.path.isfile(f"{d}/{n}") and not runtime_problem(f"{d}/{n}")]
    return max(files, key=os.path.getmtime) if files else None


def install_runtime(src, dest=None):
    """Copy a downloaded runtime file to ~/.local/share/satchel/ after checking it really is a runtime."""
    dest = dest or f"{RUNTIME_DIR}/runtime-{platform.machine()}"
    why = runtime_problem(src)
    if why: raise BuildError(why)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copyfile(src, dest + ".new"); os.chmod(dest + ".new", 0o644); os.replace(dest + ".new", dest)
    return dest


CACHE = os.path.expanduser("~/.cache/satchel")


def prepare_tool(tool):
    """(program to run, folder it needs or None). An AppImage tool is unpacked once and run as an ordinary program: with
    'ajail on' every AppImage is started inside a second sandbox that cannot see the build folder."""
    real = os.path.realpath(tool)
    off = appimage_offset(real)
    if off is None: return real, None
    st = os.stat(real)
    base = f"{CACHE}/{clean(os.path.basename(real))}-{st.st_size}-{st.st_mtime_ns}"
    root = f"{base}/root"
    if not os.path.isfile(f"{root}/AppRun"):
        unsq = shutil.which("unsquashfs")
        if not unsq: raise BuildError(f"unsquashfs is needed to unpack appimagetool: {install_hint('squashfs-tools')}")
        shutil.rmtree(base, ignore_errors=True)
        os.makedirs(base, mode=0o700)
        tmp = f"{base}/root.tmp"
        try: r = subprocess.run([unsq, "-no-progress", "-no-xattrs", "-o", str(off), "-d", tmp, real], capture_output=True,
                                text=True, errors="replace", stdin=subprocess.DEVNULL, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as e: raise BuildError(f"Could not unpack appimagetool: {e}")
        if r.returncode != 0 or not os.path.isfile(f"{tmp}/AppRun"):
            shutil.rmtree(base, ignore_errors=True)
            raise BuildError("Could not unpack appimagetool:\n" + (r.stderr or r.stdout).strip()[-600:])
        subprocess.run(["chmod", "-R", "u+rwX", tmp], check=False)
        os.replace(tmp, root)
    return f"{root}/AppRun", root
