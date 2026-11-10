# ---- packages (.deb): unpacked into a folder, then handled like any program folder ----
ARCHIVE_EXT = (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar.zst", ".tzst", ".zip")


PKG_EXT = (".deb",) + ARCHIVE_EXT                  # a .pkg.tar.zst (Arch) is an archive that also holds a usr/ tree


MAX_UNPACKED = 8 << 30


CATEGORY_MAP = {"Utility": "Utility", "TextEditor": "Utility", "Development": "Development", "IDE": "Development",
                "Graphics": "Graphics", "Network": "Network", "WebBrowser": "Network", "Email": "Network",
                "AudioVideo": "AudioVideo", "Audio": "AudioVideo", "Video": "AudioVideo", "Player": "AudioVideo",
                "Office": "Office", "Game": "Game", "Education": "Education", "Science": "Science",
                "System": "System", "Settings": "Settings"}


def is_package(path):
    """A .deb, an Arch package, a .tar.* archive or a .zip: something Satchel unpacks to find the program inside."""
    return os.path.isfile(path) and path.lower().endswith(PKG_EXT)


def package_hint(path):
    """The program name from a file name like firefox_159.0.1_amd64.deb, firefox-159.0.1.tar.xz or foo-1.2-1-x86_64.pkg.tar.zst."""
    n = os.path.basename(path)
    for ext in sorted(PKG_EXT, key=len, reverse=True):
        if n.lower().endswith(ext): n = n[:-len(ext)]; break
    n = re.sub(r"\.pkg$", "", n)
    n = n.split("_")[0]
    return re.sub(r"[-.]v?\d[\w.+~:-]*$", "", n) or n


def ar_members(f):
    """(name, data offset, size) of each member of an ar archive (a .deb is one)."""
    if f.read(8) != b"!<arch>\n": raise BuildError("That file is not a .deb package (it is not an ar archive).")
    while True:
        head = f.read(60)
        if len(head) < 60: return
        try: size = int(head[48:58])
        except ValueError: size = -1
        if head[58:60] != b"`\n" or size < 0: raise BuildError("The .deb package is damaged (bad archive header).")
        start = f.tell()
        yield head[:16].decode("ascii", "replace").strip().rstrip("/"), start, size
        f.seek(start + size + (size & 1))


def zstd_unpack(raw, out):
    """Decompress the zstd file raw into out (Python 3.14's own module, else the zstd program)."""
    try:
        from compression import zstd                                    # Python 3.14 and later
        with zstd.open(raw, "rb") as s_, open(out, "wb") as o: shutil.copyfileobj(s_, o)
        return out
    except ImportError: pass
    z = shutil.which("zstd")
    if not z: raise BuildError(f"zstd is needed to unpack this package: {install_hint('zstd')}")
    r = subprocess.run([z, "-d", "-q", "-f", "-o", out, raw], capture_output=True, text=True, errors="replace",
                       stdin=subprocess.DEVNULL, timeout=600)
    if r.returncode != 0: raise BuildError("Could not unpack the package:\n" + (r.stderr or r.stdout).strip()[-400:])
    return out


def _tar_ready(raw, name, work):
    """A tar file tarfile can open: gz, bz2 and xz are read directly, zstd is unpacked first."""
    kind = name[len("data.tar"):]
    if kind in ("", ".gz", ".bz2", ".xz"): return raw
    if kind != ".zst": raise BuildError(f"This package uses a compression Satchel does not know ({name}).")
    return zstd_unpack(raw, f"{work}/data.tar")


def extract_tar(tar_path, dest):
    """Unpack a tar into dest safely: nothing is written outside dest (no .., no writing through links), absolute
    links are rewritten to point inside dest, device files and the like are skipped, and the total size is limited."""
    base, total = os.path.realpath(dest), 0
    inside = lambda p: p == base or p.startswith(base + os.sep)
    os.makedirs(base, exist_ok=True)
    try:
        with tarfile.open(tar_path, "r:*") as tar:
            for m in tar:
                parts = [x for x in m.name.split("/") if x not in ("", ".")]
                if not parts or ".." in parts: continue
                target = os.path.join(base, *parts)
                parent = os.path.dirname(target)
                if not inside(os.path.realpath(parent)): continue
                if m.isdir():
                    if os.path.islink(target): continue
                    os.makedirs(target, exist_ok=True); os.chmod(target, (m.mode & 0o777) | 0o700)
                    continue
                os.makedirs(parent, exist_ok=True)
                if m.isreg():
                    total += m.size
                    if total > MAX_UNPACKED: raise BuildError("The package is unpacked larger than 8 GB, so it was not unpacked.")
                    if os.path.lexists(target): os.remove(target)
                    with tar.extractfile(m) as src_, open(target, "wb") as out: shutil.copyfileobj(src_, out)
                    os.chmod(target, (m.mode & 0o777) | 0o600)
                elif m.issym():
                    link = m.linkname
                    if os.path.isabs(link): link = os.path.relpath(os.path.join(base, link.lstrip("/")), parent)
                    if not inside(os.path.normpath(os.path.join(parent, link))): continue
                    if os.path.lexists(target): os.remove(target)
                    os.symlink(link, target)
                elif m.islnk():
                    srcp = os.path.join(base, *[x for x in m.linkname.split("/") if x not in ("", ".")])
                    if inside(os.path.realpath(srcp)) and os.path.isfile(srcp):
                        if os.path.lexists(target): os.remove(target)
                        shutil.copy2(srcp, target)
    except BuildError: raise
    except Exception as e: raise BuildError(f"Could not unpack the package: {e}")


def unpack_deb(path, dest):
    """Unpack a .deb (its data.tar.*) into dest, with Python's own tools (a .zst package also needs zstd)."""
    parent = os.path.dirname(os.path.abspath(dest))
    try:
        with open(path, "rb") as f:
            for name, off, size in ar_members(f):
                if name.startswith("data.tar"): break
            else: raise BuildError("There is no data.tar inside that file, so it is not a usable .deb package.")
            with tempfile.TemporaryDirectory(prefix="satchel-deb-", dir=parent) as work:
                raw = f"{work}/data"
                f.seek(off)
                with open(raw, "wb") as o:
                    left = size
                    while left > 0:
                        chunk = f.read(min(left, 1 << 20))
                        if not chunk: raise BuildError("The .deb package is cut short (damaged download?).")
                        o.write(chunk); left -= len(chunk)
                extract_tar(_tar_ready(raw, name, work), dest)
    except OSError as e: raise BuildError(f"Could not read the package: {e}")


def extract_zip(zip_path, dest):
    """Unpack a .zip into dest with the same safety rules as extract_tar (nothing outside dest, links kept only when
    they stay inside, size limited). The program's executable bit is kept."""
    base, total = os.path.realpath(dest), 0
    inside = lambda p: p == base or p.startswith(base + os.sep)
    os.makedirs(base, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for zi in zf.infolist():
                parts = [x for x in zi.filename.replace("\\", "/").split("/") if x not in ("", ".")]
                if not parts or ".." in parts: continue
                target = os.path.join(base, *parts); parent = os.path.dirname(target)
                if not inside(os.path.realpath(parent)): continue
                mode = (zi.external_attr >> 16) & 0o7777
                if zi.is_dir():
                    if not os.path.islink(target): os.makedirs(target, exist_ok=True); os.chmod(target, (mode & 0o777) | 0o700)
                    continue
                os.makedirs(parent, exist_ok=True)
                if stat.S_ISLNK(mode):
                    link = zf.read(zi).decode("utf-8", "replace")
                    if os.path.isabs(link): link = os.path.relpath(os.path.join(base, link.lstrip("/")), parent)
                    if not inside(os.path.normpath(os.path.join(parent, link))): continue
                    if os.path.lexists(target): os.remove(target)
                    os.symlink(link, target)
                    continue
                if os.path.lexists(target): os.remove(target)
                with zf.open(zi) as src_, open(target, "wb") as out:
                    while True:
                        chunk = src_.read(1 << 20)
                        if not chunk: break
                        total += len(chunk)
                        if total > MAX_UNPACKED: raise BuildError("The archive is unpacked larger than 8 GB, so it was not unpacked.")
                        out.write(chunk)
                os.chmod(target, ((mode & 0o777) or 0o644) | 0o600)
    except BuildError: raise
    except Exception as e: raise BuildError(f"Could not unpack the archive: {e}")


def single_folder(dest):
    """dest, or the one folder inside it when that is all it holds (firefox.tar.xz holds just firefox/)."""
    try: names = os.listdir(dest)
    except OSError: return dest
    if len(names) == 1:
        inner = os.path.join(dest, names[0])
        if os.path.isdir(inner) and not os.path.islink(inner): return inner
    return dest


def unpack_archive(path, dest):
    """Unpack a .zip or .tar.* into dest."""
    if path.lower().endswith(".zip"): return extract_zip(path, dest)
    with tempfile.TemporaryDirectory(prefix="satchel-tar-", dir=os.path.dirname(os.path.abspath(dest))) as work:
        raw = path
        if path.lower().endswith((".zst", ".tzst")): raw = zstd_unpack(path, f"{work}/archive.tar")
        extract_tar(raw, dest)


def unpack_package(path, dest):
    """Unpack a .deb, an Arch package, a .tar.* archive or a .zip into dest. Returns the folder to use as the program
    folder: dest itself, except that an ordinary archive holding a single folder gives that folder."""
    low = path.lower()
    if low.endswith(".deb"):
        unpack_deb(path, dest); return dest
    if not low.endswith(ARCHIVE_EXT): raise BuildError("Only .deb packages, .tar.* archives and .zip files can be opened.")
    try: unpack_archive(path, dest)
    except OSError as e: raise BuildError(f"Could not read the archive: {e}")
    return dest if ".pkg." in os.path.basename(low) else single_folder(dest)


def parse_desktop(path):
    """The keys of the [Desktop Entry] group of a .desktop file (first value of each, translations left out)."""
    out, inside = {}, False
    try:
        with open(path, encoding="utf-8", errors="replace") as f: text = f.read(50000)
    except OSError: return out
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["): inside = line == "[Desktop Entry]"
        elif inside and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            if "[" not in k: out.setdefault(k.strip(), v.strip())
    return out


def package_info(root, hint=""):
    """What an unpacked package says about itself: its programs (usr/bin, usr/games, opt), the one to start, and the
    name, description, category, terminal flag and icon from its .desktop file. Missing things are '' or None."""
    root = os.path.realpath(root)
    every = find_executables(root)
    exes = [e for e in every if e.startswith(("usr/bin/", "usr/games/", "opt/"))] or every
    by_name = {os.path.basename(e): e for e in exes}
    entries, apps = [], os.path.join(root, "usr/share/applications")
    if os.path.isdir(apps):
        for fn in sorted(os.listdir(apps)):
            d = parse_desktop(os.path.join(apps, fn)) if fn.endswith(".desktop") else {}
            if d.get("Type", "Application") == "Application" and d.get("NoDisplay", "").lower() != "true": entries.append(d)
    def exec_name(d):
        words = [w for w in d.get("Exec", "").split() if w != "env" and "=" not in w]
        return os.path.basename(words[0]) if words else ""
    entry = next((d for d in entries if exec_name(d) in by_name), entries[0] if entries else {})
    exe = by_name.get(exec_name(entry)) or by_name.get(hint) or (exes[0] if exes else "")
    icon_name = entry.get("Icon", "")
    names = [icon_name] if icon_name else []
    hints = [os.path.basename(exe), hint, *names]
    icon = None
    for sub in ("usr/share/icons", "usr/share/pixmaps", "opt", ""):
        d = os.path.join(root, sub)
        if os.path.isdir(d): icon = find_icon(d, hints, depth=7, limit=20000, extra_names=names)
        if icon: break
    category = next((CATEGORY_MAP[c] for c in entry.get("Categories", "").split(";") if c in CATEGORY_MAP), "")
    return {"exes": exes, "exe": exe, "name": entry.get("Name", ""), "comment": entry.get("Comment", ""),
            "category": category, "terminal": entry.get("Terminal", "").lower() == "true", "icon": icon}
