def build(src, name, exe, icon, category, terminal, out_dir, tool=None, sandbox=False, version="", log=None, cancel=None, comment="", bundle=False):
    """Make the AppDir in a temporary folder, run appimagetool on it (optionally inside bubblewrap), and move the
    result to out_dir. Returns the path of the .AppImage. A version goes into the desktop file, the file name and
    the VERSION variable that appimagetool reads; a comment goes into the desktop file. log(line) is called with the tool's output while it runs; setting
    the cancel event stops the build (raises Cancelled)."""
    version = version.strip()
    tool = tool or find_tool()
    if not tool: raise BuildError(f"appimagetool was not found. Download it from {TOOL_URL}, make it executable and put it in your PATH.")
    if not os.path.isdir(out_dir): raise BuildError("The output folder does not exist.")
    bwrap = need_bwrap() if sandbox else None
    if cancel is not None and cancel.is_set(): raise Cancelled("Cancelled.")
    prog, share = prepare_tool(tool)
    runtime = find_runtime(share)
    with tempfile.TemporaryDirectory(prefix="satchel-") as tmp:
        tmp = os.path.realpath(tmp)
        appdir = f"{tmp}/AppDir"
        if log: log("Making the app folder…")
        ident = make_appdir(src, name, exe, icon, category, terminal, appdir, version, comment, bundle, log, cancel, bwrap)
        arch = platform.machine()
        stem = f"{ident}-{clean(version)}-{arch}" if version else f"{ident}-{arch}"
        built = f"{tmp}/{stem}.AppImage"
        if bwrap: cmd, env = bwrap_cmd(bwrap, prog, share, runtime, appdir, built, tmp, arch, os.path.realpath(os.path.expanduser("~")), not runtime, version), os.environ
        else: cmd, env = [prog, *(["--runtime-file", runtime] if runtime else []), appdir, built], {**os.environ, "ARCH": arch, **({"VERSION": version} if version else {})}
        code, text, _ = run_streaming(cmd, env, tmp, log, cancel, 600, own_group=not bwrap)
        if code != 0 or not os.path.isfile(built):
            hint = "\n\n(If the sandbox is the problem, switch it off and try again.)" if bwrap else ""
            raise BuildError("appimagetool failed:\n" + text.strip()[-900:] + hint)
        if appimage_offset(built) is None:
            raise BuildError("appimagetool finished, but the file it made is not a valid AppImage "
                             "(no squashfs image after the runtime), so it was not saved.\n" + text.strip()[-600:])
        out = f"{out_dir}/{stem}.AppImage"
        shutil.move(built, out)
    return out


def describe_run(code, text, timed_out, seconds, sandboxed):
    """Plain words for how a test run went: what happened, a hint for the usual causes, then the end of its output."""
    if timed_out: head = f"It was still running after {seconds:g} seconds, so it starts. It was stopped again."
    elif code == 0: head = "It started and finished normally."
    else: head = f"It stopped by itself with exit code {code}."
    low, lines = text.lower(), [head]
    if "shared librar" in low or "shared object file" in low:
        lines.append("A shared library is missing. Install it, or pack it in the folder.")
    if "exec format error" in low:
        lines.append("The program is not for this kind of computer, or it is not a real program (exec format error).")
    if code in (126, 127):
        lines.append("The program could not be started at all: check that the right file is chosen and that it is executable.")
    if sandboxed and not timed_out and code != 0 and any(w in low for w in ("display", "wayland", "xcb", "x server")):
        lines.append("It looks like a window program, but it could not reach a screen from inside the sandbox "
                     "(the screen is shared when one is found). Switch the sandbox off to see it.")
    if sandboxed: lines.append("(It ran inside the sandbox: no network, your home folder hidden.)")
    tail = text.strip()[-700:]
    return "\n".join(lines) + (f"\n\nOutput:\n{tail}" if tail else "")


def try_run(src, name, exe, icon, category, terminal, sandbox=False, seconds=5, log=None, cancel=None, bundle=False):
    """Start the packed program the way the AppImage would and watch it for a few seconds (inside bubblewrap, with no
    network and your home hidden but your screen shared, when sandbox is on). Returns (ok, a description); ok means it ran fine or was still
    running when the time was up. It catches a wrong program, a missing library or a format error before you build."""
    bwrap = need_bwrap() if sandbox else None
    with tempfile.TemporaryDirectory(prefix="satchel-try-") as tmp:
        tmp = os.path.realpath(tmp)
        appdir = f"{tmp}/AppDir"
        make_appdir(src, name, exe, icon, category, terminal, appdir, "", "", bundle, log, cancel, bwrap)   # the same checks as a build
        cmd = [f"{appdir}/AppRun"]
        if bwrap: cmd = bwrap_base(bwrap, (), tmp, platform.machine(), os.path.realpath(os.path.expanduser("~")), False, display=True) + cmd
        code, text, timed_out = run_streaming(cmd, os.environ, tmp, log, cancel, seconds, False, own_group=not bwrap)
    return (timed_out or code == 0), describe_run(code, text, timed_out, seconds, bool(bwrap))
