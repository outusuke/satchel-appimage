def icon_problem(path):
    """Why path cannot be used as an icon, or None. The file must really be a PNG or an SVG, whatever its name says."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in ICON_EXT or not os.path.isfile(path): return "The icon must be an existing .png or .svg file."
    try:
        with open(path, "rb") as f: head = f.read(4096)
    except OSError as e: return f"Could not read the icon: {e}"
    if ext == ".png" and head[:4] != b"\x89PNG": return "That file is named .png but is not a PNG image."
    if ext == ".svg" and b"<svg" not in head.lower(): return "That file is named .svg but is not an SVG image."
    return None


def png_size(path):
    """(width, height) read from a PNG header, or None."""
    try:
        with open(path, "rb") as f: head = f.read(24)
    except OSError: return None
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR": return None
    return struct.unpack(">II", head[16:24])


GOOD_ICON_WORDS = ("icon", "logo", "default", "appicon")


BAD_ICON_WORDS = ("splash", "banner", "background", "screenshot", "wallpaper", "header", "thumb", "preview", "symbolic",
                  "sprite", "cursor", "tray", "mask")


GOOD_ICON_DIRS = ("icons", "icon", "hicolor", "pixmaps", "apps")


BAD_ICON_DIRS = ("images", "img", "doc", "docs", "help", "screenshots", "samples", "examples", "test", "tests")


def desktop_icon_names(folder, depth=4):
    """The Icon= values of the .desktop files inside folder (name or absolute path as written)."""
    base, out = os.path.realpath(folder), []
    for root, dirs, files in os.walk(base):
        if root[len(base):].count(os.sep) >= depth: dirs[:] = []
        for fn in files:
            if not fn.endswith(".desktop"): continue
            try:
                with open(os.path.join(root, fn), encoding="utf-8", errors="replace") as f: text = f.read(20000)
            except OSError: continue
            m = re.search(r"^Icon\s*=\s*(.+?)\s*$", text, re.M)
            if m: out.append(m.group(1))
    return out


def find_icon(folder, hints=(), min_px=48, depth=4, limit=3000, extra_names=()):
    """The most likely icon inside a program folder, or None. Every real .png/.svg (checked by content; a .png is at
    least min_px) is scored by clues: an Icon= line in a .desktop file in the folder is the strongest; a file name
    that matches the program (hints) or says icon/logo is good; splash, banner, screenshot and similar names are bad;
    an icons/ folder is good, images/ or docs/ is bad; a square image is better. Ties go to the larger image, and
    an .svg counts as the largest. Looks at most depth folders down and at most limit files."""
    base, cands, seen = os.path.realpath(folder), [], 0
    if not os.path.isdir(base): return None
    named = [*desktop_icon_names(base, depth), *extra_names]
    names = {os.path.splitext(os.path.basename(n))[0].lower() for n in named}
    paths = {os.path.realpath(n) for n in named if os.path.isabs(n)}
    hint_stems = {os.path.splitext(os.path.basename(h))[0].lower() for h in hints if h}
    for root, dirs, files in os.walk(base):
        parts = [x.lower() for x in root[len(base):].split(os.sep) if x]
        if len(parts) >= depth: dirs[:] = []
        dirs.sort()
        for fn in sorted(files):
            seen += 1
            if seen > limit: break
            path, (stem, ext) = os.path.join(root, fn), os.path.splitext(fn)
            low = stem.lower()
            if ext.lower() not in ICON_EXT or os.path.islink(path) or icon_problem(path): continue
            if ext.lower() == ".svg": size, square = 4096, True
            else:
                wh = png_size(path)
                if not wh or min(wh) < min_px: continue
                size, square = min(wh), wh[0] == wh[1]
            score = 0
            if os.path.realpath(path) in paths or low in names: score += 100
            if low in hint_stems or any(h and low.startswith(h) for h in hint_stems): score += 50
            if any(w in low for w in GOOD_ICON_WORDS): score += 20
            if any(w in low for w in BAD_ICON_WORDS): score -= 60
            if any(d in GOOD_ICON_DIRS for d in parts): score += 15
            if any(d in BAD_ICON_DIRS for d in parts): score -= 15
            if square: score += 10
            cands.append((score, size, -len(parts), path))
        if seen > limit: break
    return max(cands)[3] if cands else None
