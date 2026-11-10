def bundle_libraries(appdir, ident, exe, log=None, cancel=None, bwrap=None):
    """Run linuxdeploy on the AppDir so the libraries the program needs are copied into appdir/usr/lib. With bwrap it runs
    inside bubblewrap: no network, your home hidden, only the folder holding the AppDir writable."""
    tool = find_linuxdeploy()
    if not tool: raise BuildError(f"Bundling libraries needs linuxdeploy, which was not found. Download it from "
                                  f"{LINUXDEPLOY_URL}, make it executable and put it in your PATH.")
    if log: log("Copying libraries in (linuxdeploy)…")
    prog = f"{appdir}/usr/lib/{ident}/{os.path.normpath(exe)}"
    cmd, env, work = [tool, "--appdir", appdir, "--executable", prog], {**os.environ, "APPIMAGE_EXTRACT_AND_RUN": "1"}, os.path.dirname(appdir)
    if bwrap:
        base = bwrap_base(bwrap, (tool,), work, platform.machine(), os.path.realpath(os.path.expanduser("~")), False)
        cmd = base[:-3] + ["--setenv", "APPIMAGE_EXTRACT_AND_RUN", "1"] + base[-3:] + [os.path.realpath(tool), *cmd[1:]]
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
        if not (os.path.isfile(os.path.join(src, exe)) and os.access(os.path.join(src, exe), os.X_OK)):
            raise BuildError(f"'{exe}' is not an executable file inside the folder.")
        real_exe = os.path.realpath(os.path.join(src, exe))
        if outside_link(os.path.join(src, exe), src) or not real_exe.startswith(src + os.sep):
            raise BuildError(f"'{exe}' is a symbolic link that leads outside the folder, so it would not be packed. Copy the real file into the folder.")
    elif os.path.isfile(src):
        if not os.access(src, os.X_OK): raise BuildError("That file is not executable (chmod +x it first).")
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


def apprun_script(ident, exe, libs, usr=False, bundled=False):
    """The text of AppRun: it starts the program, with LD_LIBRARY_PATH pointing at the .so folders packed next to it.
    usr (an unpacked package) also puts the package's usr/bin and usr/share on PATH and XDG_DATA_DIRS; bundled adds
    the folder linuxdeploy copied libraries into."""
    out = '#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\n'
    if libs or usr or bundled: out += f'APP="$HERE"/{shlex.quote(f"usr/lib/{ident}")}\n'
    parts = [('"$APP"' if r == "." else '"$APP"/' + shlex.quote(r)) for r in libs] + (['"$HERE"/usr/lib'] if bundled else [])
    if parts:                                       # let the program find the .so files packed next to it
        out += f'LD_LIBRARY_PATH={":".join(parts)}"${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"; export LD_LIBRARY_PATH\n'
    if usr:
        out += ('PATH="$APP"/usr/bin:"$APP"/usr/games"${PATH:+:$PATH}"; export PATH\n'
                'XDG_DATA_DIRS="$APP"/usr/share:"${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"; export XDG_DATA_DIRS\n')
    return out + f'exec "$HERE"/{shlex.quote(f"usr/lib/{ident}/{exe}")} "$@"\n'


def app_libs(src, exe):
    """The .so folders to put on LD_LIBRARY_PATH (a package is searched deeper)."""
    return library_dirs(src, 5 if usr_layout(src, exe) else 3) if os.path.isdir(src) else []


def preview(src, name, exe, icon, category, terminal, version="", comment="", bundle=False):
    """(clean name, .desktop text, AppRun text) exactly as a build would write them, with the same checks and without
    copying anything."""
    name, version, comment, ident, exe, _ext = check_inputs(src, name, exe, icon, category, version, comment)
    real = os.path.realpath(src)
    return (ident, desktop_file(ident, name, category, terminal, version, comment),
            apprun_script(ident, exe, app_libs(real, exe), usr_layout(real, exe), bundle))


def make_appdir(src, name, exe, icon, category, terminal, appdir, version="", comment="", bundle=False, log=None, cancel=None, bwrap=None):
    """Lay out an AppDir in appdir (must not exist). src is a file or a folder; exe is the program to start,
    relative to the folder (ignored for a single file). With bundle, linuxdeploy copies the libraries the program needs into
    the AppDir. Returns the clean name used for files."""
    name, version, comment, ident, exe, ext = check_inputs(src, name, exe, icon, category, version, comment)
    src = os.path.realpath(src)
    lib = f"{appdir}/usr/lib/{ident}"
    os.makedirs(lib)
    if os.path.isdir(src):
        shutil.copytree(src, lib, symlinks=True, dirs_exist_ok=True,
                        ignore=lambda d, names: [n for n in names if outside_link(os.path.join(d, n), src)])
    else: shutil.copy2(src, f"{lib}/{exe}")
    if icon: shutil.copy2(icon, f"{appdir}/{ident}{ext}")
    else: placeholder_icon(f"{appdir}/{ident}{ext}")
    shutil.copy2(f"{appdir}/{ident}{ext}", f"{appdir}/.DirIcon")
    with open(f"{appdir}/{ident}.desktop", "w") as f: f.write(desktop_file(ident, name, category, terminal, version, comment))
    if bundle: bundle_libraries(appdir, ident, exe, log, cancel, bwrap)
    with open(f"{appdir}/AppRun", "w") as f: f.write(apprun_script(ident, exe, app_libs(src, exe), usr_layout(src, exe), bundle))
    os.chmod(f"{appdir}/AppRun", 0o755)
    return ident
