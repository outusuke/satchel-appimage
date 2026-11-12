SELF_BIN = os.path.expanduser("~/.local/bin")
SELF_APPS = os.path.expanduser("~/.local/share/applications")


def desktop_exec(path):
    """A path written the way the Exec= line of a .desktop file wants it (quoted when it holds spaces or shell characters)."""
    if not re.search(r"[\s\"'\\<>~|&;$*?#()`]", path): return path
    return '"' + re.sub(r'(["`$\\])', r"\\\1", path).replace("%", "%%") + '"'


def install_self(src=None, bin_dir=None, apps_dir=None):
    """Put Satchel itself in your local binaries (~/.local/bin/satchel) and add it to the app menu (a satchel.desktop file in
    ~/.local/share/applications that starts that copy). src is the Satchel file to copy (default: the one running). Safe to repeat:
    it replaces the old copy. Returns (path of the program, path of the menu entry, a note about PATH or '')."""
    src = src or globals().get("__file__")
    if not src or not os.path.isfile(src): raise BuildError("Cannot find the Satchel file to install.")
    bin_dir, apps_dir = bin_dir or SELF_BIN, apps_dir or SELF_APPS
    dest, entry = f"{bin_dir}/satchel", f"{apps_dir}/satchel.desktop"
    try:
        os.makedirs(bin_dir, exist_ok=True); os.makedirs(apps_dir, exist_ok=True)
        if not (os.path.exists(dest) and os.path.samefile(src, dest)):
            with open(src, "rb") as f: data = f.read()
            with open(dest + ".new", "wb") as f: f.write(data)
            os.chmod(dest + ".new", 0o755); os.replace(dest + ".new", dest)
        text = ("[Desktop Entry]\nType=Application\nName=Satchel\nComment=Build an AppImage from a program file or folder\n"
                f"Exec={desktop_exec(dest)}\nIcon=package-x-generic\nCategories=Utility;\nTerminal=false\n")
        with open(entry + ".new", "w") as f: f.write(text)
        os.replace(entry + ".new", entry)
    except OSError as e: raise BuildError(f"Could not install Satchel: {e}")
    tool = shutil.which("update-desktop-database")
    if tool:
        try: subprocess.run([tool, apps_dir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        except (OSError, subprocess.SubprocessError): pass          # only a cache refresh; the menu finds the entry anyway
    on_path = os.path.realpath(bin_dir) in [os.path.realpath(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    return dest, entry, "" if on_path else f"Note: {bin_dir} is not in your PATH, so `satchel` will not start from a terminal until you add it."


def uninstall_self(bin_dir=None, apps_dir=None):
    """Remove what install_self made. Returns the paths it removed (empty when nothing was installed)."""
    bin_dir, apps_dir = bin_dir or SELF_BIN, apps_dir or SELF_APPS
    gone = []
    for p in (f"{bin_dir}/satchel", f"{apps_dir}/satchel.desktop"):
        try: os.remove(p); gone.append(p)
        except FileNotFoundError: pass
        except OSError as e: raise BuildError(f"Could not remove {p}: {e}")
    return gone


def self_installed(bin_dir=None, apps_dir=None):
    """True when Satchel is already in the local binaries with a menu entry (see install_self)."""
    return os.path.isfile(f"{bin_dir or SELF_BIN}/satchel") and os.path.isfile(f"{apps_dir or SELF_APPS}/satchel.desktop")
