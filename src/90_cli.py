def cli(argv):
    """Build from the command line, no window. Prints the path of the AppImage; returns the exit code."""
    import argparse
    p = argparse.ArgumentParser(prog="satchel", description="Build an AppImage from a program file or folder (no window).")
    p.add_argument("source", help="the program file, the folder that holds the program, or a .deb package, .tar.* archive or .zip file")
    p.add_argument("--name", help="app name (default: the file or folder name)")
    p.add_argument("--exe", default="", help="for a folder: the program to start, relative to the folder")
    p.add_argument("--icon", help="a .png or .svg file (default: the best one found inside the folder)")
    p.add_argument("--category", choices=CATEGORIES, help="default: from the package, else Utility")
    p.add_argument("--version", default="", help="goes into the file name and the desktop file")
    p.add_argument("--comment", default="", help="a short description for the desktop file")
    p.add_argument("--terminal", action="store_true", help="the app runs in a terminal")
    p.add_argument("--bundle-libs", action="store_true", help="copy the libraries the program needs into the AppImage (needs linuxdeploy)")
    p.add_argument("--writable-data", action="store_true",
                   help="for programs that read and write files next to themselves: AppRun works from ~/.local/share/<name>, "
                        "which has the same folders as the program folder (real, so the program can add files anywhere) holding links "
                        "to the packed files; small files such as settings.json are copied, so they can change")
    p.add_argument("--writable", action="append", default=[], metavar="NAME",
                   help="with --writable-data, only if needed: copy this file or folder (a path inside the program folder) instead of linking it, "
                        "for a program that changes a big file in place; repeatable")
    p.add_argument("--out", default=".", help="folder for the result (default: here)")
    p.add_argument("--sandbox", action="store_true", help="run appimagetool (or the test run) inside bubblewrap")
    p.add_argument("--try", dest="try_first", action="store_true",
                   help="only start the program for a few seconds to see that it works; builds nothing")
    p.add_argument("--seconds", type=float, default=5, help="with --try: how long to watch the program (default 5)")
    p.add_argument("--dry-run", action="store_true", help="only print the .desktop file and AppRun that would be made; builds nothing")
    p.add_argument("-v", "--verbose", action="store_true", help="show the output while it runs")
    a = p.parse_args(argv)
    if not is_package(a.source):
        a.category = a.category or "Utility"
        return cli_run(a)
    with tempfile.TemporaryDirectory(prefix="satchel-pkg-") as t:
        root = f"{t}/root"
        try: root = unpack_package(a.source, root)
        except BuildError as e:
            print(e, file=sys.stderr)
            return 1
        hint = package_hint(a.source)
        info = package_info(root, hint)
        if not (a.exe or info["exe"]):
            print("No program was found inside that file.", file=sys.stderr)
            return 1
        a.name, a.exe = a.name or info["name"] or hint, a.exe or info["exe"]
        a.icon, a.comment = a.icon or info["icon"], a.comment or info["comment"]
        a.category, a.terminal = a.category or info["category"] or "Utility", a.terminal or info["terminal"]
        print(f"From the package or archive: program {a.exe}, name {a.name}" + (f", icon {os.path.relpath(a.icon, root)}" if a.icon else ""),
              file=sys.stderr)
        if (a.try_first or a.bundle_libs) and not a.dry_run and not a.sandbox:
            print("Heads up: --try and --bundle-libs run programs from this package on your computer without the sandbox. "
                  "Add --sandbox unless you trust it.", file=sys.stderr)
        a.source = root
        return cli_run(a)


def cli_run(a):
    name = a.name or default_name(os.path.realpath(a.source))
    data = (a.writable or []) if a.writable_data else None
    if a.writable and not a.writable_data: print("--writable only works together with --writable-data.", file=sys.stderr); return 1
    log = (lambda line: print(line, file=sys.stderr)) if a.verbose else None
    note = packing_notes(a.source, a.exe, a.bundle_libs)
    if not a.icon and os.path.isdir(a.source):
        a.icon = find_icon(a.source, (os.path.basename(os.path.realpath(a.source)), os.path.basename(a.exe)))
        if a.icon: print(f"Icon found in the folder: {a.icon}", file=sys.stderr)
    try:
        if a.dry_run:
            ident, desktop, apprun = preview(a.source, name, a.exe, a.icon, a.category, a.terminal, a.version, a.comment, a.bundle_libs, data)
            print(f"--- {ident}.desktop ---\n{desktop}\n--- AppRun ---\n{apprun}", end="")
            if note: print(note, file=sys.stderr)
            return 0
        if a.try_first:
            ok, text = try_run(a.source, name, a.exe, a.icon, a.category, a.terminal, a.sandbox, a.seconds, log, None, a.bundle_libs, data)
            print(text)
            if note: print(note, file=sys.stderr)
            return 0 if ok else 1
        print(build(a.source, name, a.exe, a.icon, a.category, a.terminal, os.path.abspath(a.out), None, a.sandbox, a.version, log, None, a.comment, a.bundle_libs, data))
    except BuildError as e:
        print(e, file=sys.stderr)
        return 1
    if note: print(note, file=sys.stderr)
    return 0
