CATEGORIES = ["Utility", "Development", "Graphics", "Network", "AudioVideo", "Office", "Game", "Education",
              "Science", "System", "Settings"]


ICON_EXT = (".png", ".svg")


TOOL_URL = "https://github.com/AppImage/appimagetool/releases"


APP_VERSION = "1.1"


class BuildError(Exception):
    pass


class Cancelled(BuildError):
    """The person stopped the build; not an error to show."""


OS_RELEASE = "/etc/os-release"


def install_hint(pkg):
    """The command that installs pkg on this distribution, guessed from /etc/os-release (dnf when unknown)."""
    ids = []
    try:
        with open(OS_RELEASE) as f:
            for line in f:
                k, _, v = line.partition("=")
                if k in ("ID", "ID_LIKE"): ids += v.strip().strip('"').lower().split()
    except OSError: pass
    for i in ids:
        if i in ("debian", "ubuntu"): return f"sudo apt install {pkg}"
        if i in ("arch", "manjaro"): return f"sudo pacman -S {pkg}"
        if i in ("suse", "sles") or i.startswith("opensuse"): return f"sudo zypper install {pkg}"
    return f"sudo dnf install {pkg}"


def clean(n):
    """A safe file name: letters, digits, dot, dash, underscore. Accents are dropped (Café -> Cafe). A name with no usable
    letters at all (日本語, ...) becomes app-<short hash of the name>, so different names do not end up as the same file."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", n) if not unicodedata.combining(c))
    out = re.sub(r"[^A-Za-z0-9._-]", "_", plain).lstrip("._-")[:64]
    if out.strip("_"): return out
    return "app-" + hashlib.sha1(n.encode("utf-8", "replace")).hexdigest()[:6] if n.strip(". _-") else "app"


def desktop_text(v):
    """A value for a .desktop file: a backslash is the escape character there, so it is doubled."""
    return v.replace("\\", "\\\\")


def default_name(path):
    """The app name suggested for a program: a file's name without its extension, a folder's name as it is."""
    p = path.rstrip("/") or path
    return os.path.basename(p) if os.path.isdir(p) else os.path.splitext(os.path.basename(p))[0]


def auto_name(current, previous, new):
    """What the Name box should say after the program changed. The new suggestion goes in when the box is empty or still
    shows the last suggestion; text the person typed or edited is kept."""
    return new if not current.strip() or current == previous else current
