def bundle_libraries(appdir, ident, exe, log=None, cancel=None, bwrap=None):
    """Run linuxdeploy on the AppDir so the libraries the program needs are copied into appdir/usr/lib. With bwrap it runs
    inside bubblewrap: no network, your home hidden, only the folder holding the AppDir writable."""
    tool = find_linuxdeploy()
    if not tool: raise BuildError(f"Bundling libraries needs linuxdeploy, which was not found. Download it from "
                                  f"{LINUXDEPLOY_URL}, make it executable and put it in your PATH.")
    prog_tool, share = prepare_tool(tool)       # an AppImage linuxdeploy is unpacked and run as a plain program (see prepare_tool)
    if log: log("Copying libraries in (linuxdeploy)…")
    prog = f"{appdir}/usr/lib/{ident}/{os.path.normpath(exe)}"
    # linuxdeploy parses every --executable as ELF and aborts on anything else, so a launcher script (Chrome's google-chrome-stable
    # is one) is left out; the ELF files inside the AppDir (chrome, its .so files) are still scanned by linuxdeploy on its own.
    is_elf = elf_dynamic(os.path.realpath(prog))[2] is not None
    cmd, env, work = [prog_tool, "--appdir", appdir] + (["--executable", prog] if is_elf else []), {**os.environ, "APPIMAGE_EXTRACT_AND_RUN": "1"}, os.path.dirname(appdir)
    if bwrap:
        base = bwrap_base(bwrap, (share or tool,), work, platform.machine(), os.path.realpath(os.path.expanduser("~")), False)
        cmd = base[:-3] + ["--setenv", "APPIMAGE_EXTRACT_AND_RUN", "1"] + base[-3:] + cmd
    code, text, _ = run_streaming(cmd, env, work, log, cancel, 600, own_group=not bwrap)
    if code != 0: raise BuildError("linuxdeploy failed:\n" + text.strip()[-900:])


def placeholder_icon(dst):
    """A plain 256x256 PNG square (appimagetool wants a PNG for .DirIcon)."""
    n, bg, fg = 256, bytes((0x3B, 0x42, 0x52, 255)), bytes((0xEC, 0xEF, 0xF4, 255))
    row = lambda y: b"\0" + b"".join(fg if 96 <= x < 160 and 96 <= y < 160 else bg for x in range(n))
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"".join(row(y) for y in range(n)))) + chunk(b"IEND", b""))
    with open(dst, "wb") as f: f.write(png)


def check_inputs(src, name, exe, icon, category, version="", comment=""):
    """Check what the person gave. Returns (name, version, comment, clean name, program inside the folder, icon extension);
    raises BuildError with a plain message when something is wrong."""
    name, version, comment = name.strip(), version.strip(), comment.strip()
    if not name or any(ord(c) < 32 for c in name): raise BuildError("Give the app a name (one line, no control characters).")
    if any(ord(c) < 32 for c in version): raise BuildError("The version must be one line, no control characters.")
    if any(ord(c) < 32 for c in comment): raise BuildError("The description must be one line, no control characters.")
    if category not in CATEGORIES: raise BuildError(f"Unknown category '{category}'.")
    src = os.path.realpath(src)
    if os.path.isdir(src):
        exe = os.path.normpath(exe or "")
        if not exe or exe == "." or os.path.isabs(exe) or exe.split(os.sep)[0] == "..": raise BuildError("Choose which program in the folder to start.")
        if not os.path.isfile(os.path.join(src, exe)): raise BuildError(f"'{exe}' is not an executable file inside the folder.")
        real_exe = os.path.realpath(os.path.join(src, exe))
        if outside_link(os.path.join(src, exe), src) or not real_exe.startswith(src + os.sep):
            raise BuildError(f"'{exe}' is a symbolic link that leads outside the folder, so it would not be packed. Copy the real file into the folder.")
        if not is_program(os.path.join(src, exe)):
            raise BuildError(f"'{exe}' is not an executable file (it is not a binary or a script that starts with #!).")
    elif os.path.isfile(src):
        if not is_program(src): raise BuildError("That file is not an executable program (it is not a binary or a script that starts with #!).")
        exe = os.path.basename(src)
    else:
        raise BuildError("Choose the program file or folder to pack.")
    why = nested_problem(os.path.join(src, exe) if os.path.isdir(src) else src)
    if why: raise BuildError(why)
    if icon:
        ext = os.path.splitext(icon)[1].lower()
        if icon_problem(icon): raise BuildError(icon_problem(icon))
    else:
        ext = ".png"
    return name, version, comment, clean(name), exe, ext


def desktop_file(ident, name, category, terminal, version="", comment=""):
    """The text of the .desktop file."""
    return (f"[Desktop Entry]\nType=Application\nName={desktop_text(name)}\n"
            + (f"Comment={desktop_text(comment)}\n" if comment else "")
            + f"Exec={ident}\nIcon={ident}\nCategories={category};\nTerminal={'true' if terminal else 'false'}\n"
            + (f"X-AppImage-Version={desktop_text(version)}\n" if version else ""))


def usr_layout(src, exe):
    """True when src is an unpacked package: a usr/ tree with share/ in it and a program under usr/ or opt/."""
    return (os.path.isdir(os.path.join(src, "usr", "share"))
            and os.path.normpath(exe or "").replace(os.sep, "/").startswith(("usr/", "opt/")))


DATA_COPY_MAX = 64 * 1024       # a settings-like file this small, anywhere in the program folder, is copied, not linked
DATA_COPY_EXT = (".json", ".ini", ".cfg", ".conf", ".toml", ".yaml", ".yml", ".xml", ".properties")


def data_copies(src, exe, extra=()):
    """The extra names (paths inside the packed folder, any depth) that a writable-data AppRun copies once instead of
    linking, for programs that change a big file in place. Nothing is needed for a program that only adds new files:
    every folder is a real folder in the data folder. Settings-like small files are found by AppRun itself."""
    out = set()
    for n in extra:
        n = os.path.normpath(n.strip()) if n.strip() else ""
        if n and n != "." and not os.path.isabs(n) and n.split(os.sep)[0] != "..": out.add(n)
    return sorted(out)


def apprun_script(ident, exe, libs, usr=False, bundled=False, data=None):
    """The text of AppRun: it starts the program, with LD_LIBRARY_PATH pointing at the .so folders packed next to it.
    usr (an unpacked package) also puts the package's usr/bin and usr/share on PATH and XDG_DATA_DIRS; bundled adds
    the folder linuxdeploy copied libraries into. data (a list of names, possibly empty; None means off) makes AppRun work from a
    writable folder, ~/.local/share/<ident>, because an AppImage is read-only: on each start (the AppImage is mounted somewhere
    new each time) the packed folder's tree is rebuilt there as real folders holding links to the packed files, so the program can
    create files anywhere. Small settings-like files, and the names in data, are copied once and then left alone so the program can change them."""
    out = '#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\n'
    if libs or usr or bundled or data is not None: out += f'APP="$HERE"/{shlex.quote(f"usr/lib/{ident}")}\n'
    parts = [('"$APP"' if r == "." else '"$APP"/' + shlex.quote(r)) for r in libs] + (['"$HERE"/usr/lib'] if bundled else [])
    if parts:                                       # let the program find the .so files packed next to it
        out += f'LD_LIBRARY_PATH={":".join(parts)}"${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"; export LD_LIBRARY_PATH\n'
    if usr:
        out += ('PATH="$APP"/usr/bin:"$APP"/usr/games"${PATH:+:$PATH}"; export PATH\n'
                'XDG_DATA_DIRS="$APP"/usr/share:"${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"; export XDG_DATA_DIRS\n')
    if data is not None:
        copy = ""
        if data:
            copy = ("for n in " + " ".join(shlex.quote(n) for n in data) + "; do\n"
                    '  [ -e "$APP/$n" ] || continue\n  [ -e "$DATA/$n" ] || [ -L "$DATA/$n" ] && continue\n'
                    '  mkdir -p "$(dirname "$DATA/$n")" && cp -a "$APP/$n" "$DATA/$n" && chmod -R u+w "$DATA/$n"\ndone\n')
        ext = " -o ".join(f"-iname '*{e}'" for e in DATA_COPY_EXT)
        out += (f'DATA="${{XDG_DATA_HOME:-$HOME/.local/share}}"/{shlex.quote(ident)}\n'
                'mkdir -p "$DATA" && [ -w "$DATA" ] || { echo "Cannot write to $DATA" >&2; exit 1; }\n'
                '# 1. drop the links an earlier start made: they point into an AppImage mount that is gone (this also turns a\n'
                '#    folder link from an older AppRun into a real folder)\n'
                f"find \"$DATA\" -type l -lname '*/usr/lib/{ident}/*' -delete\n"
                '# 2. things the person asked to copy (copied once, then left alone)\n' + copy +
                '# 3. every folder becomes a real folder, every file a link to the packed file; anything already there is kept\n'
                'for f in "$APP"/* "$APP"/.[!.]*; do\n  [ -e "$f" ] || [ -L "$f" ] || continue\n  cp -rs "$f" "$DATA"/ 2>/dev/null\ndone\n'
                '#    (the packed folders may be read-only: the real folders made here must let the program add files)\n'
                'find "$DATA" -type d ! -perm -u+w -exec chmod u+w {} + 2>/dev/null\n'
                f'# 4. small settings-like files are copied (once), so the program can change them in place\n'
                f'find "$APP" -type f \\( {ext} \\) -size -{DATA_COPY_MAX + 1}c -exec sh -c \'A=$1; D=$2; shift 2\n'
                '  for f; do r=${f#"$A"/}\n    if [ -L "$D/$r" ]; then rm -f "$D/$r" && cp "$f" "$D/$r" && chmod u+w "$D/$r"; fi\n  done\' sh "$APP" "$DATA" {} +\n'
                'cd "$DATA" || exit 1\n')
    return out + f'exec "$HERE"/{shlex.quote(f"usr/lib/{ident}/{exe}")} "$@"\n'


def app_libs(src, exe):
    """The .so folders to put on LD_LIBRARY_PATH (a package is searched deeper)."""
    return library_dirs(src, 5 if usr_layout(src, exe) else 3) if os.path.isdir(src) else []


def preview(src, name, exe, icon, category, terminal, version="", comment="", bundle=False, data=None):
    """(clean name, .desktop text, AppRun text) exactly as a build would write them, with the same checks and without
    copying anything."""
    name, version, comment, ident, exe, _ext = check_inputs(src, name, exe, icon, category, version, comment)
    real = os.path.realpath(src)
    return (ident, desktop_file(ident, name, category, terminal, version, comment),
            apprun_script(ident, exe, app_libs(real, exe), usr_layout(real, exe), bundle, None if data is None else data_copies(real, exe, data)))


def make_appdir(src, name, exe, icon, category, terminal, appdir, version="", comment="", bundle=False, log=None, cancel=None, bwrap=None, data=None):
    """Lay out an AppDir in appdir (must not exist). src is a file or a folder; exe is the program to start,
    relative to the folder (ignored for a single file). With bundle, linuxdeploy copies the libraries the program needs into
    the AppDir. data (None = off, else a list of extra names to copy) makes AppRun use a writable data folder (see apprun_script). Returns the clean name used for files."""
    name, version, comment, ident, exe, ext = check_inputs(src, name, exe, icon, category, version, comment)
    src = os.path.realpath(src)
    lib = f"{appdir}/usr/lib/{ident}"
    os.makedirs(lib)
    if os.path.isdir(src):
        shutil.copytree(src, lib, symlinks=True, dirs_exist_ok=True,
                        ignore=lambda d, names: [n for n in names if outside_link(os.path.join(d, n), src)])
    else: shutil.copy2(src, f"{lib}/{exe}")
    prog = os.path.join(lib, exe)                   # the packed copy must be startable even if the original lost its bit
    os.chmod(prog, (os.stat(prog).st_mode & 0o7777) | 0o111)
    if icon: shutil.copy2(icon, f"{appdir}/{ident}{ext}")
    else: placeholder_icon(f"{appdir}/{ident}{ext}")
    shutil.copy2(f"{appdir}/{ident}{ext}", f"{appdir}/.DirIcon")
    with open(f"{appdir}/{ident}.desktop", "w") as f: f.write(desktop_file(ident, name, category, terminal, version, comment))
    if bundle: bundle_libraries(appdir, ident, exe, log, cancel, bwrap)
    with open(f"{appdir}/AppRun", "w") as f: f.write(apprun_script(ident, exe, app_libs(src, exe), usr_layout(src, exe), bundle, None if data is None else data_copies(src, exe, data)))
    os.chmod(f"{appdir}/AppRun", 0o755)
    return ident
