BIG_FOLDER = 1 << 30


def folder_size(folder, limit=None):
    """The total size in bytes of the files in a folder (links not followed); stops counting once it passes limit."""
    total = 0
    for root, _dirs, files in os.walk(folder):
        for f in files:
            try: total += os.lstat(os.path.join(root, f)).st_size
            except OSError: pass
            if limit and total > limit: return total
    return total


def outside_link(path, base):
    """True when path is a symbolic link that would not work once the folder is packed: an absolute link, or one that
    leads out of base (a folder, already resolved)."""
    if not os.path.islink(path): return False
    if os.path.isabs(os.readlink(path)): return True
    real = os.path.realpath(path)
    return not (real == base or real.startswith(base + os.sep))


def outside_links(folder):
    """Paths (relative to folder, sorted) of the symbolic links in it that point outside it or use an absolute path."""
    base, out = os.path.realpath(folder), []
    for root, dirs, files in os.walk(base):
        for n in dirs + files:
            p = os.path.join(root, n)
            if outside_link(p, base): out.append(os.path.relpath(p, base))
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
    return sorted(out)


def nested_problem(path):
    """Why a program that is itself an AppImage is refused, or ''. Inside another AppImage it needs FUSE to start, and a system
    that hands AppImages to a launcher or sandbox (binfmt_misc, ajail, AppImageLauncher) starts that instead of the program."""
    if not os.path.isfile(path) or appimage_offset(path) is None: return ""
    return (f"'{os.path.basename(path)}' is already an AppImage, so it is not packed inside another one: it would need FUSE to "
            "start, and a system that passes AppImages to a launcher or sandbox (binfmt_misc, ajail, AppImageLauncher) would "
            "start that instead. Use the file as it is, or unpack it and pack what is inside.")


def is_program(path):
    """True when the file starts like something that can be run: an ELF binary or a script with a #! line. The permission bit
    is not used: some packages mark every file executable, pictures and data included, and a copy or an extraction can drop
    the bit from a real program (the build sets it on the program it packs)."""
    try:
        with open(path, "rb") as f: head = f.read(4)
    except OSError:
        return False
    return head == b"\x7fELF" or head[:2] == b"#!"


def find_executables(folder):
    """Programs at most two folders deep, as paths relative to folder (shallowest first): ELF files and #! scripts,
    no .so files, no links that lead outside the folder."""
    base, out = os.path.realpath(folder), []
    for root, dirs, files in os.walk(base):
        if root[len(base):].count(os.sep) >= 2: dirs[:] = []
        for f in files:
            p = os.path.join(root, f)
            if outside_link(p, base): continue
            if os.path.isfile(p) and not (f.endswith(".so") or ".so." in f) and is_program(p):
                out.append(os.path.relpath(p, base))
    return sorted(out, key=lambda r: (r.count(os.sep), r))


def best_exe(exes, hints):
    """The index in exes of the program most likely meant: one named like a hint (the folder or app name) beats one whose
    name starts with a hint; otherwise the first. exes is the list from find_executables (shallowest first)."""
    want = [h.lower() for h in hints if h]
    best, score = 0, 0
    for i, e in enumerate(exes):
        stem = os.path.splitext(os.path.basename(e))[0].lower()
        s = 2 if stem in want else 1 if any(stem.startswith(h) for h in want) else 0
        if s > score: best, score = i, s
    return best


def _is_lib(name):
    return name.endswith(".so") or ".so." in name


def library_dirs(folder, max_depth=3):
    """Folders inside the program folder (at most three deep) that hold shared libraries, as paths relative to it
    ('.' is the folder itself), shallowest first. Names with a colon are skipped: they cannot go into LD_LIBRARY_PATH."""
    base, out = os.path.realpath(folder), []
    for root, dirs, files in os.walk(base):
        rel = os.path.relpath(root, base)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth >= max_depth: dirs[:] = []
        if ":" not in rel and any(_is_lib(f) for f in files): out.append((depth, rel))
    return [rel for _, rel in sorted(out)[:30]]


def appimage_offset(path):
    """Where the squashfs image starts inside a type 2 AppImage (right after its ELF section headers), or None if it isn't one."""
    try:
        with open(path, "rb") as f:
            h = f.read(64)
            if len(h) < 64 or h[:4] != b"\x7fELF" or h[8:11] != b"AI\x02": return None
            e = "<" if h[5] == 1 else ">"
            if h[4] == 2: shoff, = struct.unpack_from(e + "Q", h, 0x28); sz, num = struct.unpack_from(e + "HH", h, 0x3A)
            else: shoff, = struct.unpack_from(e + "I", h, 0x20); sz, num = struct.unpack_from(e + "HH", h, 0x2E)
            off = shoff + sz * num
            f.seek(off)
            return off if f.read(4) in (b"hsqs", b"sqsh") else None
    except (OSError, struct.error):
        return None
