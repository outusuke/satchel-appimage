#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Tests for satchel's build logic (no GTK and no real appimagetool needed):   python3 test_satchel.py
import contextlib, io, os, shutil, stat, struct, subprocess, tarfile, tempfile, threading, time, unittest, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def load():
    with open(f"{HERE}/satchel") as f: src = f.read()
    ns = {"__name__": "appimage_gui_under_test"}
    exec(compile(src, "satchel", "exec"), ns)
    return ns


M = load()
BuildError = M["BuildError"]


def put(path, text, mode="w"):
    with open(path, mode) as f: f.write(text)


def get(path, mode="r"):
    with open(path, mode) as f: return f.read()


def script(path, body):
    put(path, "#!/bin/sh\n" + body)
    os.chmod(path, 0o755)
    return path


def make_deb(path, files, compress="xz"):
    """A real .deb (ar archive: debian-binary, control.tar.gz, data.tar.<compress>). files: {name: bytes | ('link', target) |
    ('exe', bytes)}."""
    def tar_bytes(entries, comp):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:" + comp) as tf:
            for name, val in entries.items():
                ti = tarfile.TarInfo(name)
                if isinstance(val, tuple) and val[0] == "link": ti.type, ti.linkname = tarfile.SYMTYPE, val[1]; tf.addfile(ti)
                else:
                    data = val[1] if isinstance(val, tuple) else val
                    ti.size, ti.mode = len(data), 0o755 if isinstance(val, tuple) else 0o644
                    tf.addfile(ti, io.BytesIO(data))
        return buf.getvalue()
    members = [("debian-binary", b"2.0\n"), ("control.tar.gz", tar_bytes({"./control": b"Package: x\n"}, "gz")),
               (f"data.tar.{compress}", tar_bytes(files, compress))]
    out = b"!<arch>\n"
    for name, data in members:
        out += (name + "/").ljust(16).encode() + b"0".ljust(12) + b"0".ljust(6) + b"0".ljust(6) + b"100644".ljust(8) \
               + str(len(data)).ljust(10).encode() + b"`\n" + data + (b"\n" if len(data) % 2 else b"")
    with open(path, "wb") as f: f.write(out)
    return path


DESKTOP = b"[Desktop Entry]\nType=Application\nName=Fox Browser\nName[de]=Fuchs\nComment=Browse\nExec=/usr/bin/fox %u\nIcon=fox\nCategories=Network;WebBrowser;\n"


class Logic(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory(); self.t = os.path.realpath(self._t.name)
        M["RUNTIME_DIR"] = f"{self.t}/rt"; M["MAX_DOWNLOAD"] = 200 * 1024 * 1024; os.environ.pop("APPIMAGE_RUNTIME", None)
        M["TOOL_HOME"] = f"{self.t}/nohome/appimagetool"            # never see the real ~/.local/bin/appimagetool
        M["LD_HOME"] = f"{self.t}/nohome/linuxdeploy"               # nor the real linuxdeploy
    def tearDown(self): self._t.cleanup()

    def prog_folder(self):
        d = f"{self.t}/My App"; os.makedirs(f"{d}/bin"); os.makedirs(f"{d}/data")
        script(f"{d}/bin/run", 'echo "args: $@"\n')
        for rel in ("data/x.txt", "libfoo.so"): put(f"{d}/{rel}", "x")
        os.chmod(f"{d}/libfoo.so", 0o755)
        return d

    def ok_tool(self, path, pre=""):
        """A fake appimagetool: runs the shell line pre, then writes a valid-looking AppImage to its last argument."""
        fake = f"{self.t}/fake.AppImage"
        if not os.path.exists(fake): self.fake_appimage(fake)
        return script(path, f'{pre}\nfor a; do :; done; cp "{fake}" "$a"\n')

    def test_clean(self):
        self.assertEqual(M["clean"]("../My App!"), "My_App_")
        self.assertEqual(M["clean"]("..."), "app")

    def test_clean_drops_accents_and_keeps_different_names_apart(self):
        self.assertEqual(M["clean"]("Caf\u00e9"), "Cafe")
        a, b = M["clean"]("\u65e5\u672c\u8a9e"), M["clean"]("\u4e2d\u6587")
        self.assertTrue(a.startswith("app-") and b.startswith("app-") and a != b)
        self.assertEqual(M["clean"]("\u65e5\u672c\u8a9e"), a)                   # the same name always gives the same file

    def linked_folder(self):
        d = self.prog_folder()
        os.symlink("/etc/passwd", f"{d}/host")                                    # absolute
        os.symlink("../../outside", f"{d}/bin/up")                                # relative, leaves the folder
        os.symlink("data/x.txt", f"{d}/inside")                                   # relative, stays inside: fine
        os.symlink("/bin/sh", f"{d}/lnk")                                         # a link to a program on the host
        return d

    def test_links_that_leave_the_folder_are_not_packed_but_inner_ones_are(self):
        d, ad = self.linked_folder(), f"{self.t}/AD"
        self.assertEqual(M["outside_links"](d), ["bin/up", "host", "lnk"])
        M["make_appdir"](d, "My App", "bin/run", None, "Utility", False, ad)
        lib = f"{ad}/usr/lib/My_App"
        for gone in ("host", "bin/up", "lnk"): self.assertFalse(os.path.lexists(f"{lib}/{gone}"), gone)
        self.assertEqual(os.readlink(f"{lib}/inside"), "data/x.txt")
        note = M["links_note"](M["outside_links"](d))
        self.assertIn("host", note); self.assertIn("left out", note); self.assertEqual(M["links_note"]([]), "")

    def test_find_executables_ignores_links_to_the_host(self):
        self.assertEqual(M["find_executables"](self.linked_folder()), ["bin/run"])

    def test_packing_notes_joins_the_warnings(self):
        d = self.linked_folder()
        self.assertIn("symbolic links", M["packing_notes"](d, "bin/run"))
        os.remove(f"{d}/host"); os.remove(f"{d}/bin/up"); os.remove(f"{d}/lnk")      # only the harmless inner link is left
        self.assertNotIn("symbolic", M["packing_notes"](d, "bin/run"))

    def test_desktop_file_values_are_escaped(self):
        ad = f"{self.t}/AD"
        M["make_appdir"](self.prog_folder(), "My\\App %U", "bin/run", None, "Utility", False, ad, "1\\2")
        d = get(f"{ad}/{M['clean']('My\\App %U')}.desktop")
        self.assertIn("Name=My\\\\App %U\n", d); self.assertIn("X-AppImage-Version=1\\\\2\n", d)
        self.assertEqual(M["desktop_text"]("a\\b"), "a\\\\b")

    def test_icons_must_really_be_images(self):
        ad = f"{self.t}/AD"; prog = script(f"{self.t}/t", "")
        fake, fake_svg, svg = f"{self.t}/fake.png", f"{self.t}/fake.svg", f"{self.t}/ok.svg"
        put(fake, "not a png"); put(fake_svg, "hello"); put(svg, '<?xml version="1.0"?>\n<SVG xmlns="x"/>')
        for bad in (fake, fake_svg):
            with self.assertRaises(BuildError, msg=bad): M["make_appdir"](prog, "t", "", bad, "Utility", False, ad)
            self.assertIsNotNone(M["icon_problem"](bad))
        M["make_appdir"](prog, "t", "", svg, "Utility", False, ad)
        self.assertIsNone(M["icon_problem"](svg)); self.assertTrue(os.path.isfile(f"{ad}/t.svg"))
        self.assertIsNotNone(M["icon_problem"](f"{self.t}/missing.png"))

    def test_a_program_that_is_already_an_appimage_is_refused(self):
        d = f"{self.t}/Pk"; os.makedirs(d); self.fake_appimage(f"{d}/spaghetti.appimage"); put(f"{d}/data.txt", "x")
        single, ad = f"{self.t}/one.appimage", f"{self.t}/AD"; self.fake_appimage(single)
        for src, exe in ((d, "spaghetti.appimage"), (single, "")):
            for call in (lambda: M["make_appdir"](src, "x", exe, None, "Utility", False, ad), lambda: M["preview"](src, "x", exe, None, "Utility", False),
                         lambda: M["try_run"](src, "x", exe, None, "Utility", False)):
                with self.assertRaisesRegex(BuildError, "already an AppImage"): call()
        self.assertFalse(os.path.exists(f"{ad}/AppRun"))
        out = io.StringIO()
        with contextlib.redirect_stderr(out): self.assertEqual(M["main"]([single, "--dry-run"]), 1)
        self.assertIn("already an AppImage", out.getvalue())
        self.assertEqual(M["nested_problem"](script(f"{self.t}/plain", "echo\n")), "")       # an ordinary program is fine
        self.assertEqual(M["nested_problem"](f"{self.t}/missing"), "")
        script(f"{d}/run", "echo hi\n"); M["preview"](d, "x", "run", None, "Utility", False)   # a folder that merely holds one is fine

    def test_find_executables_skips_libs_and_data(self):
        self.assertEqual(M["find_executables"](self.prog_folder()), ["bin/run"])

    def test_folder_appdir_layout_and_apprun_runs(self):
        ad = f"{self.t}/AppDir"
        ident = M["make_appdir"](self.prog_folder(), "My App", "bin/run", None, "Utility", True, ad)
        self.assertEqual(ident, "My_App")
        d = get(f"{ad}/My_App.desktop")
        for line in ("Name=My App", "Exec=My_App", "Icon=My_App", "Categories=Utility;", "Terminal=true"): self.assertIn(line, d)
        self.assertTrue(os.path.isfile(f"{ad}/My_App.png") and os.path.isfile(f"{ad}/.DirIcon"))
        self.assertEqual(get(f"{ad}/.DirIcon", "rb")[:8], b"\x89PNG\r\n\x1a\n")
        self.assertTrue(os.path.isfile(f"{ad}/usr/lib/My_App/data/x.txt"))
        r = subprocess.run([f"{ad}/AppRun", "a b", "c"], capture_output=True, text=True)       # the path has a space in it
        self.assertEqual(r.stdout.strip(), "args: a b c", r.stderr)

    def test_single_file(self):
        p = script(f"{self.t}/tool", "echo hi\n"); ad = f"{self.t}/AD"
        M["make_appdir"](p, "tool", "ignored", None, "Utility", False, ad)
        self.assertEqual(subprocess.run([f"{ad}/AppRun"], capture_output=True, text=True).stdout.strip(), "hi")

    def test_custom_icon_is_used(self):
        ic = f"{self.t}/i.png"; put(ic, b"\x89PNG", "wb")
        ad = f"{self.t}/AD"; M["make_appdir"](script(f"{self.t}/t", ""), "t", "", ic, "Utility", False, ad)
        self.assertEqual(get(f"{ad}/t.png", "rb"), b"\x89PNG")

    def test_bad_input_is_refused(self):
        f, ad = self.prog_folder(), f"{self.t}/AD"
        bad = [(f, "", "bin/run", None, "Utility"), (f, "x", "../run", None, "Utility"), (f, "x", "/bin/sh", None, "Utility"),
               (f, "x", "data/x.txt", None, "Utility"), (f, "x", "", None, "Utility"), (f, "x", "bin/run", None, "Nope"),
               (f, "x", "bin/run", f"{self.t}/icon.gif", "Utility"), (f"{self.t}/missing", "x", "", None, "Utility"),
               (f, "a\nb", "bin/run", None, "Utility")]
        for src, name, exe, icon, cat in bad:
            with self.assertRaises(BuildError, msg=(name, exe, icon, cat)):
                M["make_appdir"](src, name, exe, icon, cat, False, ad)
            self.assertFalse(os.path.exists(f"{ad}/AppRun"))
        plain = f"{self.t}/plain"; put(plain, "x")                                  # not executable
        with self.assertRaises(BuildError): M["make_appdir"](plain, "x", "", None, "Utility", False, ad)

    def test_exe_must_not_be_a_link_that_leaves_the_folder(self):
        d, ad = self.linked_folder(), f"{self.t}/AD"
        for exe in ("lnk", "host", "bin/up"):
            with self.assertRaisesRegex(BuildError, "symbolic link|not an executable", msg=exe):
                M["make_appdir"](d, "x", exe, None, "Utility", False, ad)
        with self.assertRaisesRegex(BuildError, "symbolic link"): M["preview"](d, "x", "lnk", None, "Utility", False)
        os.makedirs(f"{self.t}/elsewhere"); script(f"{self.t}/elsewhere/run", "")
        os.symlink(f"../elsewhere", f"{d}/dirlink")                                  # a folder link leading out
        with self.assertRaisesRegex(BuildError, "symbolic link"): M["preview"](d, "x", "dirlink/run", None, "Utility", False)
        os.symlink("bin/run", f"{d}/inner_run")                                      # a link that stays inside is fine
        M["make_appdir"](d, "x", "inner_run", None, "Utility", False, ad)
        self.assertTrue(os.path.islink(f"{ad}/usr/lib/x/inner_run"))

    def test_exe_names_that_merely_start_with_dots_are_allowed(self):
        d = self.prog_folder(); script(f"{d}/..odd", "echo odd\n"); ad = f"{self.t}/AD"
        M["make_appdir"](d, "x", "..odd", None, "Utility", False, ad)
        self.assertEqual(subprocess.run([f"{ad}/AppRun"], capture_output=True, text=True).stdout.strip(), "odd")
        for bad in ("..", "../run", "bin/../../run"):
            with self.assertRaises(BuildError, msg=bad): M["preview"](d, "x", bad, None, "Utility", False)

    def test_build_runs_the_tool_and_returns_the_file(self):
        log = f"{self.t}/log"
        tool = self.ok_tool(f"{self.t}/appimagetool", f'echo "$ARCH $1" > {log}; test -x "$1/AppRun" || exit 1')
        os.makedirs(f"{self.t}/out")
        out = M["build"](self.prog_folder(), "My App", "bin/run", None, "Utility", False, f"{self.t}/out", tool)
        self.assertTrue(out.startswith(f"{self.t}/out/My_App-") and out.endswith(".AppImage") and os.path.isfile(out))
        self.assertIn(f"{M['platform'].machine()} ", get(log))

    def test_build_reports_tool_failure_and_missing_tool(self):
        os.makedirs(f"{self.t}/out"); prog = self.prog_folder()
        tool = script(f"{self.t}/appimagetool", 'echo boom >&2; exit 3\n')
        with self.assertRaisesRegex(BuildError, "boom"):
            M["build"](prog, "x", "bin/run", None, "Utility", False, f"{self.t}/out", tool)
        os.environ["PATH"], old = "/nonexistent", os.environ["PATH"]
        oldhome = os.environ.get("HOME"); os.environ["HOME"] = self.t       # so ~/bin/appimagetool is not found either
        try:
            os.environ.pop("APPIMAGETOOL", None)
            with self.assertRaisesRegex(BuildError, "appimagetool was not found"):
                M["build"](prog, "x", "bin/run", None, "Utility", False, f"{self.t}/out", None)
        finally:
            os.environ["PATH"] = old
            if oldhome is None: os.environ.pop("HOME", None)
            else: os.environ["HOME"] = oldhome

    def test_find_tool_lookup_order(self):
        home, bindir = f"{self.t}/h", f"{self.t}/pb"; os.makedirs(f"{home}/bin"); os.makedirs(bindir)
        env = {k: os.environ.get(k) for k in ("HOME", "PATH", "APPIMAGETOOL")}
        os.environ["HOME"], os.environ["PATH"] = home, bindir; os.environ.pop("APPIMAGETOOL", None)
        try:
            self.assertIsNone(M["find_tool"]())
            home_bin = script(f"{home}/bin/appimagetool", "")
            self.assertEqual(M["find_tool"](), home_bin)                               # ~/bin
            M["TOOL_HOME"] = script(f"{self.t}/th", "")
            self.assertEqual(M["find_tool"](), M["TOOL_HOME"])                         # TOOL_HOME beats ~/bin
            on_path = script(f"{bindir}/appimagetool", "")
            self.assertEqual(M["find_tool"](), on_path)                                # PATH beats both
            os.environ["APPIMAGETOOL"] = "/x/y"
            self.assertEqual(M["find_tool"](), "/x/y")                                 # the variable beats all
        finally:
            for k, v in env.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v

    def test_install_tool_copies_and_chmods_but_refuses_non_programs(self):
        src, dest = f"{self.t}/appimagetool-x86_64.AppImage", f"{self.t}/bin/appimagetool"
        put(src, b"\x7fELFrest", "wb")
        self.assertEqual(M["install_tool"](src, dest), dest)
        self.assertTrue(os.access(dest, os.X_OK) and get(dest, "rb") == b"\x7fELFrest")
        page = f"{self.t}/page.html"; put(page, "<html>404</html>")
        with self.assertRaisesRegex(BuildError, "not a program"): M["install_tool"](page, f"{self.t}/bin/x")
        self.assertFalse(os.path.exists(f"{self.t}/bin/x"))
        with self.assertRaises(BuildError): M["install_tool"](f"{self.t}/missing", f"{self.t}/bin/y")

    def test_find_download_prefers_this_machine_then_newest(self):
        home = f"{self.t}/h"; os.makedirs(f"{home}/Downloads")
        old = os.environ.get("HOME"); os.environ["HOME"] = home
        try:
            self.assertIsNone(M["find_download"]())
            arch = M["platform"].machine()
            for n, t in (("appimagetool-other.AppImage", 300), (f"appimagetool-{arch}.AppImage", 100), ("notes.txt", 400)):
                put(f"{home}/Downloads/{n}", "x"); os.utime(f"{home}/Downloads/{n}", (t, t))
            self.assertEqual(os.path.basename(M["find_download"]()), f"appimagetool-{arch}.AppImage")
        finally:
            if old is None: os.environ.pop("HOME")
            else: os.environ["HOME"] = old

    def fake_appimage(self, path, magic=b"hsqs"):
        h = bytearray(64); h[:4] = b"\x7fELF"; h[4] = 2; h[5] = 1; h[8:11] = b"AI\x02"
        h[0x28:0x30] = (64).to_bytes(8, "little"); h[0x3A:0x3C] = (64).to_bytes(2, "little")      # section headers: none, at byte 64
        put(path, bytes(h) + magic + b"payload", "wb"); os.chmod(path, 0o755)

    def test_appimage_offset(self):
        a, b, c = f"{self.t}/a", f"{self.t}/b", f"{self.t}/c"
        self.fake_appimage(a); self.fake_appimage(b, b"nope"); put(c, b"\x7fELF" + bytes(80), "wb")
        self.assertEqual(M["appimage_offset"](a), 64)
        self.assertIsNone(M["appimage_offset"](b)); self.assertIsNone(M["appimage_offset"](c))
        self.assertIsNone(M["appimage_offset"](f"{self.t}/missing"))

    def test_prepare_tool_unpacks_an_appimage_once_and_leaves_plain_programs_alone(self):
        home = f"{self.t}/h"; os.makedirs(home); bindir = f"{self.t}/pb"; os.makedirs(bindir)
        calls = f"{self.t}/calls"
        script(f"{bindir}/unsquashfs", f'echo "$@" >> {calls}\nfor a; do d="$a"; done\n'
               'while [ "$1" != "-d" ]; do shift; done; mkdir -p "$2"; printf "#!/bin/sh\\n" > "$2/AppRun"; chmod +x "$2/AppRun"\n')
        app = f"{self.t}/appimagetool.AppImage"; self.fake_appimage(app)
        old = dict(os.environ); M["CACHE"] = f"{home}/cache"
        os.environ["PATH"] = f"{bindir}:{old['PATH']}"
        try:
            exe, root = M["prepare_tool"](app)
            self.assertTrue(exe.endswith("/root/AppRun") and os.path.isfile(exe) and root == os.path.dirname(exe))
            self.assertIn("-o 64", get(calls))
            M["prepare_tool"](app)                                              # second time: cached, no new unpack
            self.assertEqual(len(get(calls).splitlines()), 1)
            plain = script(f"{self.t}/plain", "")
            self.assertEqual(M["prepare_tool"](plain), (plain, None))
            os.environ["PATH"] = "/nonexistent"; M["CACHE"] = f"{home}/cache2"
            with self.assertRaisesRegex(BuildError, "unsquashfs is needed"): M["prepare_tool"](app)
        finally:
            os.environ.clear(); os.environ.update(old)

    def fake_runtime(self, path):
        self.fake_appimage(path, b"")                                # type 2 ELF with nothing attached
        with open(path, "rb") as f: data = f.read()
        put(path, data[:64], "wb")

    def test_runtime_install_and_find(self):
        self.assertIsNone(M["find_runtime"]())
        src = f"{self.t}/runtime-{M['platform'].machine()}"; self.fake_runtime(src)
        dest = M["install_runtime"](src)
        self.assertEqual(M["find_runtime"](), dest)
        page = f"{self.t}/p.html"; put(page, "<html>")
        with self.assertRaisesRegex(BuildError, "not a program"): M["install_runtime"](page)
        root = f"{self.t}/root"; os.makedirs(f"{root}/usr/lib/appimagekit"); self.fake_runtime(f"{root}/usr/lib/appimagekit/runtime")
        os.remove(dest); self.assertEqual(M["find_runtime"](root), f"{root}/usr/lib/appimagekit/runtime")

    def test_a_complete_appimage_is_refused_as_runtime(self):
        """The bug: appimagetool itself chosen as the runtime made every built app start appimagetool."""
        tool = f"{self.t}/appimagetool-x86_64.AppImage"; self.fake_appimage(tool)       # ELF + squashfs attached
        with self.assertRaisesRegex(BuildError, "complete AppImage"): M["install_runtime"](tool)
        self.assertFalse(os.path.exists(f"{M['RUNTIME_DIR']}/runtime-{M['platform'].machine()}"))
        M["RUNTIME_DIR"] = f"{self.t}/rt"; os.makedirs(M["RUNTIME_DIR"])                 # a wrong file already in place is ignored
        shutil_copy = __import__("shutil").copyfile
        shutil_copy(tool, f"{M['RUNTIME_DIR']}/runtime-{M['platform'].machine()}")
        self.assertIsNone(M["find_runtime"]())

    def test_build_passes_runtime_file(self):
        log = f"{self.t}/log"; os.makedirs(f"{self.t}/out")
        tool = self.ok_tool(f"{self.t}/appimagetool", f'echo "$@" > {log}')
        rt = f"{self.t}/runtime"; self.fake_runtime(rt); os.environ["APPIMAGE_RUNTIME"] = rt
        try: M["build"](self.prog_folder(), "x", "bin/run", None, "Utility", False, f"{self.t}/out", tool)
        finally: os.environ.pop("APPIMAGE_RUNTIME")
        self.assertTrue(get(log).startswith(f"--runtime-file {rt} "))

    def test_bwrap_cmd_hides_home_and_shares_only_what_is_needed(self):
        c = M["bwrap_cmd"]("/usr/bin/bwrap", "/home/u/.cache/x/root/AppRun", "/home/u/.cache/x/root", "/home/u/.local/share/r",
                           "/tmp/w/AppDir", "/tmp/w/o.AppImage", "/tmp/w", "x86_64", "/home/u", False)
        j = " ".join(c)
        for need in ("--unshare-all", "--die-with-parent", "--clearenv", "--ro-bind / /", "--tmpfs /home --tmpfs /home/u", "--bind /tmp/w /tmp/w",
                     "--setenv ARCH x86_64", "--chdir /tmp/w -- /home/u/.cache/x/root/AppRun --runtime-file /home/u/.local/share/r /tmp/w/AppDir /tmp/w/o.AppImage"):
            self.assertIn(need, j)
        self.assertNotIn("--share-net", j)
        self.assertLess(j.index("--tmpfs /home/u"), j.index("--ro-bind /home/u/.cache/x/root /home/u/.cache/x/root"))
        self.assertLess(j.index("--tmpfs /home/u"), j.index("--ro-bind /home/u/.local/share/r"))
        self.assertNotIn("--ro-bind /home/u ", j)                       # the real home is never bound
        c = M["bwrap_cmd"]("bwrap", "/usr/local/bin/appimagetool", None, None, "/a", "/o", "/tmp/w", "x86_64", "/home/u", True)
        j = " ".join(c)
        self.assertIn("--share-net", j); self.assertIn("--ro-bind-try /run/systemd/resolve", j)
        self.assertNotIn("--runtime-file", j); self.assertNotIn("--ro-bind /usr/local", j)       # outside home: already visible

    def test_build_in_sandbox_runs_through_bwrap(self):
        log = f"{self.t}/log"; bindir = f"{self.t}/pb"; os.makedirs(bindir); os.makedirs(f"{self.t}/out")
        script(f"{bindir}/bwrap", f'echo "$@" > {log}\nwhile [ "$1" != "--" ]; do shift; done; shift\nexec "$@"\n')
        tool = self.ok_tool(f"{self.t}/appimagetool")
        prog = self.prog_folder(); old = os.environ["PATH"]; os.environ["PATH"] = f"{bindir}:{old}"
        try:
            out = M["build"](prog, "My App", "bin/run", None, "Utility", False, f"{self.t}/out", tool, True)
            self.assertTrue(os.path.isfile(out)); self.assertIn("--share-net", get(log))        # no runtime file: network on
            rt = f"{self.t}/runtime"; self.fake_runtime(rt); os.environ["APPIMAGE_RUNTIME"] = rt
            M["build"](prog, "My App", "bin/run", None, "Utility", False, f"{self.t}/out", tool, True)
            self.assertNotIn("--share-net", get(log)); self.assertIn(f"--runtime-file {rt}", get(log))
            os.environ["PATH"] = "/nonexistent"
            with self.assertRaisesRegex(BuildError, "bubblewrap"):
                M["build"](prog, "x", "bin/run", None, "Utility", False, f"{self.t}/out", tool, True)
        finally: os.environ["PATH"] = old; os.environ.pop("APPIMAGE_RUNTIME", None)

    def test_version_goes_into_desktop_file_file_name_and_environment(self):
        log = f"{self.t}/log"; os.makedirs(f"{self.t}/out")
        tool = self.ok_tool(f"{self.t}/appimagetool", f'echo "$VERSION" > {log}')
        out = M["build"](self.prog_folder(), "My App", "bin/run", None, "Utility", False, f"{self.t}/out", tool, False, "1.2 beta")
        self.assertTrue(out.endswith(f"/My_App-1.2_beta-{M['platform'].machine()}.AppImage") and os.path.isfile(out))
        self.assertEqual(get(log).strip(), "1.2 beta")
        ad = f"{self.t}/AD"; M["make_appdir"](script(f"{self.t}/t", ""), "t", "", None, "Utility", False, ad, "2.0")
        self.assertIn("X-AppImage-Version=2.0", get(f"{ad}/t.desktop"))
        with self.assertRaises(BuildError):
            M["make_appdir"](script(f"{self.t}/t2", ""), "t", "", None, "Utility", False, f"{self.t}/AD2", "1\n2")

    def test_bwrap_sets_version(self):
        c = M["bwrap_cmd"]("bwrap", "/x/t", None, None, "/a", "/o", "/tmp/w", "x86_64", "/home/u", False, "1.0")
        self.assertIn("--setenv VERSION 1.0", " ".join(c))
        self.assertNotIn("VERSION", " ".join(M["bwrap_cmd"]("bwrap", "/x/t", None, None, "/a", "/o", "/tmp/w", "x86_64", "/home/u", False)))

    def test_settings_roundtrip_and_bad_values_are_dropped(self):
        M["SETTINGS"] = f"{self.t}/cfg/settings.json"; out = f"{self.t}/o"; os.makedirs(out)
        self.assertEqual(M["load_settings"](), {})
        want = {"out_dir": out, "category": "Game", "terminal": True, "sandbox": False}
        M["save_settings"](want)
        self.assertEqual(M["load_settings"](), want)
        put(M["SETTINGS"], '{"out_dir": "/nonexistent/x", "category": "Nope", "terminal": "yes", "sandbox": 1}')
        self.assertEqual(M["load_settings"](), {})
        put(M["SETTINGS"], "not json"); self.assertEqual(M["load_settings"](), {})
        put(M["SETTINGS"], "[1]"); self.assertEqual(M["load_settings"](), {})
        os.makedirs(f"{self.t}/f"); put(f"{self.t}/f/file", "x")
        M["save_settings"]({"a": 1}, f"{self.t}/f/file/x/s.json")           # cannot be written: ignored, no exception

    def test_install_hint_follows_the_distribution(self):
        osr = f"{self.t}/os-release"; M["OS_RELEASE"] = osr
        for text, want in (("ID=fedora\n", "sudo dnf install p"), ("ID=ubuntu\nID_LIKE=debian\n", "sudo apt install p"),
                           ('ID=linuxmint\nID_LIKE="ubuntu debian"\n', "sudo apt install p"), ("ID=arch\n", "sudo pacman -S p"),
                           ('ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n', "sudo zypper install p"),
                           ("ID=weird\n", "sudo dnf install p")):
            put(osr, text); self.assertEqual(M["install_hint"]("p"), want, text)
        os.remove(osr); self.assertEqual(M["install_hint"]("p"), "sudo dnf install p")

    def test_command_line_builds_and_reports_errors(self):
        os.makedirs(f"{self.t}/out"); tool = self.ok_tool(f"{self.t}/appimagetool")
        os.environ["APPIMAGETOOL"] = tool
        try:
            with contextlib.redirect_stdout(io.StringIO()) as so:
                code = M["main"]([self.prog_folder(), "--exe", "bin/run", "--name", "My App", "--version", "3", "--out", f"{self.t}/out"])
            res = so.getvalue().strip()
            self.assertEqual(code, 0)
            self.assertTrue(os.path.isfile(res) and res.endswith(f"My_App-3-{M['platform'].machine()}.AppImage"), res)
            with contextlib.redirect_stderr(io.StringIO()) as se:
                self.assertEqual(M["main"]([f"{self.t}/missing", "--out", f"{self.t}/out"]), 1)
            self.assertIn("Choose the program", se.getvalue())
        finally: os.environ.pop("APPIMAGETOOL", None)

    def test_build_refuses_a_result_that_is_not_an_appimage(self):
        os.makedirs(f"{self.t}/out")
        tool = script(f"{self.t}/appimagetool", 'for a; do :; done; echo made > "$a"\n')
        with self.assertRaisesRegex(BuildError, "not a valid AppImage"):
            M["build"](self.prog_folder(), "x", "bin/run", None, "Utility", False, f"{self.t}/out", tool)
        self.assertEqual(os.listdir(f"{self.t}/out"), [])

    def test_apprun_finds_libraries_packed_next_to_the_program(self):
        d = self.prog_folder(); os.makedirs(f"{d}/lib dir"); put(f"{d}/lib dir/libbar.so.1", "x")
        script(f"{d}/bin/run", 'echo "$LD_LIBRARY_PATH"\n')
        self.assertEqual(M["library_dirs"](d), [".", "lib dir"])
        ad = f"{self.t}/AD"; M["make_appdir"](d, "My App", "bin/run", None, "Utility", False, ad)
        r = subprocess.run([f"{ad}/AppRun"], capture_output=True, text=True, env={**os.environ, "LD_LIBRARY_PATH": "/old"})
        paths, app = r.stdout.strip().split(":"), f"{ad}/usr/lib/My_App"
        self.assertEqual(paths, [app, f"{app}/lib dir", "/old"], r.stderr)
        p = script(f"{self.t}/tool", "echo hi\n"); ad2 = f"{self.t}/AD2"
        M["make_appdir"](p, "tool", "", None, "Utility", False, ad2)             # a single file: nothing to add
        self.assertNotIn("LD_LIBRARY_PATH", get(f"{ad2}/AppRun"))

    def fake_elf(self, path, needed, runpath=None, machine=0):
        """A tiny 64-bit ELF that has only what elf_dynamic reads: a load segment, a dynamic section and its string table
        (with a DT_RUNPATH when runpath is given, and the machine number in the header)."""
        strtab, offs = b"\0", []
        for n in needed: offs.append(len(strtab)); strtab += n.encode() + b"\0"
        rp = None
        if runpath is not None: rp = len(strtab); strtab += runpath.encode() + b"\0"
        str_off = 64 + 2 * 56; dyn_off = (str_off + len(strtab) + 7) // 8 * 8; base = 0x400000
        dyn = b"".join(struct.pack("<qQ", 1, o) for o in offs) + struct.pack("<qQ", 5, base + str_off)
        if rp is not None: dyn += struct.pack("<qQ", 29, rp)
        dyn += struct.pack("<qQ", 0, 0)
        total = dyn_off + len(dyn)
        h = bytearray(64); h[:4] = b"\x7fELF"; h[4] = 2; h[5] = 1; h[6] = 1; h[0x12:0x14] = machine.to_bytes(2, "little")
        h[0x20:0x28] = (64).to_bytes(8, "little"); h[0x36:0x38] = (56).to_bytes(2, "little"); h[0x38:0x3A] = (2).to_bytes(2, "little")
        ph = struct.pack("<IIQQQQQQ", 1, 5, 0, base, base, total, total, 0x1000)
        ph += struct.pack("<IIQQQQQQ", 2, 6, dyn_off, base + dyn_off, base + dyn_off, len(dyn), len(dyn), 8)
        data = bytes(h) + ph + strtab
        put(path, data + bytes(dyn_off - len(data)) + dyn, "wb"); os.chmod(path, 0o755)

    def test_needed_libs_reads_the_dynamic_section(self):
        p = f"{self.t}/e"; self.fake_elf(p, ["libc.so.6", "libfoo.so.1"])
        self.assertEqual(M["needed_libs"](p), ["libc.so.6", "libfoo.so.1"])
        script(f"{self.t}/s", "echo\n"); put(f"{self.t}/short", b"\x7fELF\x02\x01" + bytes(20), "wb")
        for other in (f"{self.t}/s", f"{self.t}/short", f"{self.t}/missing"): self.assertEqual(M["needed_libs"](other), [])

    def test_missing_libraries_lists_what_is_neither_packed_nor_installed(self):
        old = M["system_libraries"]; M["system_libraries"] = lambda: {"libc.so.6"}
        try:
            d = f"{self.t}/App"; os.makedirs(f"{d}/bin"); os.makedirs(f"{d}/lib")
            self.fake_elf(f"{d}/bin/run", ["libc.so.6", "libbundled.so.1", "libgone.so.2"])
            self.fake_elf(f"{d}/lib/libbundled.so.1", ["libc.so.6", "libdeep.so"])      # packed libraries are checked too
            self.assertEqual(M["missing_libraries"](d, "bin/run"), ["libdeep.so", "libgone.so.2"])
            f = f"{self.t}/single"; self.fake_elf(f, ["libc.so.6", "libx.so"])
            self.assertEqual(M["missing_libraries"](f, ""), ["libx.so"])
            self.assertEqual(M["missing_libraries"](d, "../x"), []); self.assertEqual(M["missing_libraries"](f"{self.t}/nope", "x"), [])
        finally: M["system_libraries"] = old
        self.assertEqual(M["missing_note"]([]), ""); self.assertIn("libx.so", M["missing_note"](["libx.so"]))

    def test_elf_dynamic_reads_runpath_and_machine(self):
        p = f"{self.t}/e"; self.fake_elf(p, ["libc.so.6"], "$ORIGIN/../lib:/opt/x/lib", 62)
        self.assertEqual(M["elf_dynamic"](p), (["libc.so.6"], ["$ORIGIN/../lib", "/opt/x/lib"], 62))
        self.fake_elf(p, [], None, 183); self.assertEqual(M["elf_dynamic"](p), ([], [], 183))
        self.assertEqual(M["elf_dynamic"](f"{self.t}/missing"), ([], [], None))

    def test_runpath_folders_count_as_places_libraries_can_be_found(self):
        old = M["system_libraries"]; M["system_libraries"] = lambda: {"libc.so.6"}
        try:
            d = f"{self.t}/App"; os.makedirs(f"{d}/bin"); os.makedirs(f"{d}/a/b/c/deep"); os.makedirs(f"{self.t}/abs")
            put(f"{d}/a/b/c/deep/libdeep.so.1", "x"); put(f"{self.t}/abs/libabs.so", "x")      # deeper than the packed-library search
            self.fake_elf(f"{d}/bin/run", ["libc.so.6", "libdeep.so.1", "libabs.so", "libgone.so"],
                          f"$ORIGIN/../a/b/c/deep:{self.t}/abs")
            self.assertEqual(M["missing_libraries"](d, "bin/run"), ["libgone.so"])
            self.fake_elf(f"{d}/bin/run", ["libdeep.so.1"], f"$ORIGIN/../../elsewhere")           # leaves the folder: does not travel
            self.assertEqual(M["missing_libraries"](d, "bin/run"), ["libdeep.so.1"])
            f = f"{self.t}/single"; self.fake_elf(f, ["libx.so"], "$ORIGIN")                       # a single file has no folder to travel with
            put(f"{self.t}/libx.so", "x"); self.assertEqual(M["missing_libraries"](f, ""), ["libx.so"])
        finally: M["system_libraries"] = old

    def test_a_program_for_another_kind_of_computer_is_warned_about(self):
        mine = M["MACHINES"][M["platform"].machine()]; other = 183 if mine != 183 else 62
        p = f"{self.t}/e"; self.fake_elf(p, [], None, other)
        self.assertIn("will not run", M["arch_problem"](p))
        self.assertIn("will not run", M["packing_notes"](p, ""))
        self.fake_elf(p, [], None, mine); self.assertEqual(M["arch_problem"](p), "")
        self.fake_elf(p, [], None, 0); self.assertEqual(M["arch_problem"](p), "")                 # unknown: say nothing
        script(f"{self.t}/s", "echo\n"); self.assertEqual(M["arch_problem"](f"{self.t}/s"), "")      # scripts run anywhere
        self.assertEqual(M["arch_problem"](f"{self.t}/missing"), "")
        self.assertEqual(M["arch_problem"](p, "weird-arch"), "")
        d = f"{self.t}/App"; os.makedirs(f"{d}/bin"); self.fake_elf(f"{d}/bin/run", [], None, other)
        self.assertIn("run", M["packing_notes"](d, "bin/run"))

    # -- name suggestion, preview, description, size, command line --
    def test_default_name_and_auto_name(self):
        d = f"{self.t}/my.app"; os.makedirs(d); f = script(f"{self.t}/tool.sh", "")
        self.assertEqual(M["default_name"](d), "my.app"); self.assertEqual(M["default_name"](d + "/"), "my.app")
        self.assertEqual(M["default_name"](f), "tool")
        auto = M["auto_name"]
        self.assertEqual(auto("", "", "new"), "new")                    # empty box: take the suggestion
        self.assertEqual(auto("old", "old", "new"), "new")              # still the last suggestion: replace it
        self.assertEqual(auto("My Editor", "old", "new"), "My Editor")  # typed by the person: keep it
        self.assertEqual(auto("   ", "old", "new"), "new")

    def test_settings_remember_the_last_folder(self):
        path = f"{self.t}/s.json"; M["save_settings"]({"last_dir": self.t, "category": "Game"}, path)
        self.assertEqual(M["load_settings"](path), {"last_dir": self.t, "category": "Game"})
        M["save_settings"]({"last_dir": f"{self.t}/gone"}, path); self.assertEqual(M["load_settings"](path), {})

    def test_description_goes_into_the_desktop_file(self):
        ad = f"{self.t}/AD"
        M["make_appdir"](self.prog_folder(), "My App", "bin/run", None, "Utility", False, ad, "", "  A tiny \\ tool ")
        self.assertIn("Comment=A tiny \\\\ tool\n", get(f"{ad}/My_App.desktop"))
        M["make_appdir"](self.prog_folder() if False else script(f"{self.t}/t2", ""), "t2", "", None, "Utility", False, f"{self.t}/AD2")
        self.assertNotIn("Comment=", get(f"{self.t}/AD2/t2.desktop"))
        with self.assertRaises(BuildError): M["make_appdir"](script(f"{self.t}/t3", ""), "t3", "", None, "Utility", False, f"{self.t}/AD3", "", "a\nb")

    def test_preview_matches_what_a_build_writes_and_copies_nothing(self):
        prog = self.prog_folder(); ad = f"{self.t}/AD"
        ident, desktop, apprun = M["preview"](prog, "My App", "bin/run", None, "Utility", True, "2.0", "hello")
        M["make_appdir"](prog, "My App", "bin/run", None, "Utility", True, ad, "2.0", "hello")
        self.assertEqual(ident, "My_App"); self.assertEqual(desktop, get(f"{ad}/My_App.desktop")); self.assertEqual(apprun, get(f"{ad}/AppRun"))
        for bad in (("", "bin/run"), ("x", "../run"), ("x", "")):                       # the same checks as a build
            with self.assertRaises(BuildError): M["preview"](prog, bad[0], bad[1], None, "Utility", False)

    def test_a_very_big_folder_is_mentioned(self):
        d = self.prog_folder(); self.assertEqual(M["size_note"](d), "")
        old = M["BIG_FOLDER"]; M["BIG_FOLDER"] = 5
        try:
            self.assertGreater(M["folder_size"](d), 5); self.assertIn("over 1 GB", M["size_note"](d))
            self.assertEqual(M["size_note"](script(f"{self.t}/one", "echo\n")), "")     # a single file is never "a folder"
        finally: M["BIG_FOLDER"] = old

    def test_command_line_dry_run_comment_and_seconds(self):
        prog = self.prog_folder()
        with contextlib.redirect_stdout(io.StringIO()) as so:
            code = M["main"]([prog, "--exe", "bin/run", "--name", "My App", "--comment", "hi there", "--dry-run"])
        out = so.getvalue(); self.assertEqual(code, 0)
        self.assertIn("--- My_App.desktop ---", out); self.assertIn("Comment=hi there", out); self.assertIn("--- AppRun ---", out)
        self.assertFalse(os.path.exists(f"{self.t}/My_App-{M['platform'].machine()}.AppImage"))
        script(f"{prog}/bin/run", "echo up\nsleep 30\n"); t0 = time.monotonic()
        with contextlib.redirect_stdout(io.StringIO()) as so:
            code = M["main"]([prog, "--exe", "bin/run", "--try", "--seconds", "0.5"])
        self.assertEqual(code, 0); self.assertIn("still running", so.getvalue()); self.assertLess(time.monotonic() - t0, 10)
        with contextlib.redirect_stderr(io.StringIO()) as se: self.assertEqual(M["main"]([prog, "--exe", "x", "--dry-run"]), 1)
        self.assertIn("not an executable", se.getvalue())

    # -- live output, cancel, test run --
    def test_run_streaming_passes_lines_on_and_keeps_the_tail(self):
        s = script(f"{self.t}/s", "echo one; echo two >&2; exit 3\n"); lines = []
        code, text, timed_out = M["run_streaming"]([s], os.environ, self.t, lines.append)
        self.assertEqual((code, text, timed_out), (3, "one\ntwo", False))
        self.assertTrue(lines[0].startswith("$ ")); self.assertEqual(lines[1:], ["one", "two"])

    def test_run_streaming_stops_on_cancel_and_on_timeout(self):
        s = script(f"{self.t}/slow", "echo started\nsleep 30\n")
        ev = threading.Event(); threading.Timer(0.4, ev.set).start(); t0 = time.monotonic()
        with self.assertRaises(M["Cancelled"]): M["run_streaming"]([s], os.environ, self.t, None, ev)
        self.assertLess(time.monotonic() - t0, 10)                            # not the 30 seconds of the sleep
        t0 = time.monotonic()
        with self.assertRaisesRegex(BuildError, "more than"): M["run_streaming"]([s], os.environ, self.t, None, None, 0.5)
        self.assertLess(time.monotonic() - t0, 10)
        code, text, timed_out = M["run_streaming"]([s], os.environ, self.t, None, None, 0.5, False)
        self.assertEqual((code, timed_out), (None, True)); self.assertIn("started", text)
        with self.assertRaisesRegex(BuildError, "Could not start"): M["run_streaming"]([f"{self.t}/nope"], os.environ, self.t)

    def test_build_streams_output_and_can_be_cancelled(self):
        os.makedirs(f"{self.t}/out"); prog = self.prog_folder(); lines = []
        tool = self.ok_tool(f"{self.t}/appimagetool", "echo step one; echo step two >&2")
        M["build"](prog, "x", "bin/run", None, "Utility", False, f"{self.t}/out", tool, False, "", lines.append)
        self.assertIn("step one", lines); self.assertIn("step two", lines)
        ev = threading.Event(); ev.set()
        with self.assertRaises(M["Cancelled"]):                                 # already cancelled: nothing is started
            M["build"](prog, "x", "bin/run", None, "Utility", False, f"{self.t}/out", tool, False, "", None, ev)
        slow = script(f"{self.t}/slowtool", "sleep 30\n"); ev2 = threading.Event(); threading.Timer(0.4, ev2.set).start()
        t0 = time.monotonic()
        with self.assertRaises(M["Cancelled"]):
            M["build"](prog, "y", "bin/run", None, "Utility", False, f"{self.t}/out", slow, False, "", None, ev2)
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual(os.listdir(f"{self.t}/out"), [f"x-{M['platform'].machine()}.AppImage"])      # no half-made file

    def test_try_run_reports_how_the_program_behaved(self):
        prog = self.prog_folder()
        ok, text = M["try_run"](prog, "My App", "bin/run", None, "Utility", False)
        self.assertTrue(ok); self.assertIn("finished normally", text); self.assertIn("args:", text)
        script(f"{prog}/bin/run", 'echo "error while loading shared libraries: libx.so" >&2\nexit 127\n')
        ok, text = M["try_run"](prog, "My App", "bin/run", None, "Utility", False)
        self.assertFalse(ok); self.assertIn("exit code 127", text); self.assertIn("shared library", text)
        script(f"{prog}/bin/run", "echo up\nsleep 30\n"); t0 = time.monotonic()
        ok, text = M["try_run"](prog, "My App", "bin/run", None, "Utility", False, False, 0.5)
        self.assertTrue(ok); self.assertIn("still running", text); self.assertIn("up", text)
        self.assertLess(time.monotonic() - t0, 10)
        with self.assertRaises(BuildError): M["try_run"](prog, "", "bin/run", None, "Utility", False)      # same checks as a build

    def test_try_run_in_the_sandbox_has_no_network_and_needs_bwrap(self):
        log = f"{self.t}/log"; bindir = f"{self.t}/pb"; os.makedirs(bindir); prog = self.prog_folder()
        script(f"{bindir}/bwrap", f'echo "$@" > {log}\nwhile [ "$1" != "--" ]; do shift; done; shift\nexec "$@"\n')
        old = os.environ["PATH"]; os.environ["PATH"] = f"{bindir}:{old}"
        try:
            ok, text = M["try_run"](prog, "My App", "bin/run", None, "Utility", False, True)
            self.assertTrue(ok); self.assertIn("inside the sandbox", text)
            self.assertIn("--unshare-all", get(log)); self.assertNotIn("--share-net", get(log))
            os.environ["PATH"] = "/nonexistent"
            with self.assertRaisesRegex(BuildError, "bubblewrap"): M["try_run"](prog, "x", "bin/run", None, "Utility", False, True)
        finally: os.environ["PATH"] = old

    def test_display_args_share_only_the_screen(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            sock = os.path.join(d, "wayland-0"); open(sock, "w").close()
            a, e = M["display_args"]({"XDG_RUNTIME_DIR": d, "WAYLAND_DISPLAY": "wayland-0"})
            self.assertIn(("WAYLAND_DISPLAY", "wayland-0"), e)
            self.assertIn(sock, a)
            self.assertNotIn("--share-net", a)
        self.assertEqual(M["display_args"]({}), ([], []))                    # no screen: nothing is added

    def test_find_icon_scores_by_clues(self):
        png = lambda path, n=256: (os.makedirs(os.path.dirname(path), exist_ok=True), M["placeholder_icon"](path))
        with tempfile.TemporaryDirectory() as d:
            icons = os.path.join(d, "browser", "icons", "default128.png")
            png(icons)
            put(os.path.join(d, "fake.png"), "text")                       # not an image: skipped
            self.assertEqual(M["find_icon"](d), icons)
            png(os.path.join(d, "images", "splash.png"))                               # bad name and bad folder
            self.assertEqual(M["find_icon"](d), icons)
            png(os.path.join(d, "myapp.png"))                                          # matches the program name
            self.assertEqual(M["find_icon"](d, ["myapp"]), os.path.join(d, "myapp.png"))
            png(os.path.join(d, "share", "chosen.png"))                                # named by a .desktop file
            put(os.path.join(d, "x.desktop"), "[Desktop Entry]\nName=X\nIcon=chosen\n")
            self.assertEqual(M["find_icon"](d, ["myapp"]), os.path.join(d, "share", "chosen.png"))
        with tempfile.TemporaryDirectory() as d:
            svg = os.path.join(d, "a.svg"); put(svg, "<svg></svg>")
            png(os.path.join(d, "b.png"))
            self.assertEqual(M["find_icon"](d), svg)                                   # tie: svg counts as largest
            os.rename(svg, os.path.join(d, "a-symbolic.svg"))
            self.assertEqual(M["find_icon"](d), os.path.join(d, "b.png"))
        with tempfile.TemporaryDirectory() as d: self.assertIsNone(M["find_icon"](d))

    def sample_deb(self, compress="xz"):
        M["placeholder_icon"](f"{self.t}/ic.png")
        return make_deb(f"{self.t}/fox_1.0_amd64.deb", {
            "./usr/bin/fox": ("exe", b"#!/bin/sh\necho fox $XDG_DATA_DIRS\n"), "./usr/share/applications/fox.desktop": DESKTOP,
            "./usr/share/icons/hicolor/256x256/apps/fox.png": get(f"{self.t}/ic.png", "rb"),
            "./usr/share/doc/fox/splash.png": get(f"{self.t}/ic.png", "rb"),
            "./usr/lib/fox/libfox.so": b"x", "./usr/bin/foxlink": ("link", "/usr/bin/fox")}, compress)

    def test_unpack_deb_and_read_the_package(self):
        for comp in ("xz", "gz", "bz2"):
            root = f"{self.t}/root-{comp}"
            M["unpack_package"](self.sample_deb(comp), root)
            self.assertTrue(os.access(f"{root}/usr/bin/fox", os.X_OK))
            self.assertEqual(os.readlink(f"{root}/usr/bin/foxlink"), "fox")             # absolute link rewritten to inside
        info = M["package_info"](root, "fox")
        self.assertEqual((info["exe"], info["name"], info["comment"], info["category"]), ("usr/bin/fox", "Fox Browser", "Browse", "Network"))
        self.assertEqual(info["icon"], f"{root}/usr/share/icons/hicolor/256x256/apps/fox.png")
        self.assertIn("usr/bin/fox", info["exes"])
        self.assertTrue(M["is_package"](f"{self.t}/fox_1.0_amd64.deb")); self.assertEqual(M["package_hint"]("/x/fox_1.0_amd64.deb"), "fox")

    def test_unpacking_never_writes_outside_the_folder(self):
        evil = make_deb(f"{self.t}/evil.deb", {"../../escaped.txt": b"x", "./usr/bin/ok": ("exe", b"#!/bin/sh\n"),
                                              "./usr/lib/up": ("link", "../../../../etc"), "./usr/lib/up/passwd": b"x"})
        root = f"{self.t}/a/b/root"; os.makedirs(f"{self.t}/a/b")
        M["unpack_package"](evil, root)
        self.assertFalse(os.path.exists(f"{self.t}/a/escaped.txt") or os.path.exists(f"{self.t}/escaped.txt"))
        self.assertFalse(os.path.islink(f"{root}/usr/lib/up"))                        # a link leading out is skipped
        self.assertTrue(os.path.isfile(f"{root}/usr/lib/up/passwd"))                   # and the file after it stayed inside
        self.assertTrue(os.path.isfile(f"{root}/usr/bin/ok"))
        put(f"{self.t}/no.deb", "not a deb")
        with self.assertRaisesRegex(BuildError, "not a .deb"): M["unpack_package"](f"{self.t}/no.deb", f"{self.t}/r2")
        with self.assertRaisesRegex(BuildError, "Only .deb"): M["unpack_package"](f"{self.t}/x.rpm", f"{self.t}/r3")

    def zip_with_links(self, path, links):
        with zipfile.ZipFile(path, "w") as zf:
            for name, target in links:
                zi = zipfile.ZipInfo(name); zi.external_attr = (stat.S_IFLNK | 0o777) << 16; zf.writestr(zi, target)
        return path

    def assert_nothing_leads_out(self, root):
        base = os.path.realpath(root)
        for n in os.listdir(root):
            real = os.path.realpath(f"{root}/{n}")
            self.assertTrue(real == base or real.startswith(base + os.sep), f"{n} leads to {real}")
        self.assertEqual(M["outside_links"](root), [])

    def test_symbolic_link_chains_cannot_lead_out_of_the_unpacked_folder(self):
        chain = (("x", "."), ("y", "x/.."), ("z", "y/.."), ("w", "z/.."))            # each one climbs a level, all look inside
        late = (("a", "b/.."), ("b", "."))                                           # a is fine until b is made
        for n, links in (("chain", chain), ("late", late)):
            tarp = f"{self.t}/{n}.tar"
            with tarfile.open(tarp, "w") as tf:
                for name, tgt in links:
                    ti = tarfile.TarInfo(name); ti.type, ti.linkname = tarfile.SYMTYPE, tgt; tf.addfile(ti)
            M["extract_tar"](tarp, f"{self.t}/t-{n}/a/root"); self.assert_nothing_leads_out(f"{self.t}/t-{n}/a/root")
            M["extract_zip"](self.zip_with_links(f"{self.t}/{n}.zip", links), f"{self.t}/z-{n}/a/root")
            self.assert_nothing_leads_out(f"{self.t}/z-{n}/a/root")
        self.assertFalse(os.path.lexists(f"{self.t}/t-late/a/root/a"))               # the one that led out is gone
        self.assertTrue(os.path.islink(f"{self.t}/t-late/a/root/b"))                 # the harmless one stays

    def test_archives_with_too_many_entries_or_oversized_links_are_refused(self):
        old = M["MAX_ENTRIES"]; M["MAX_ENTRIES"] = 3
        try:
            arc = self.make_tar(f"{self.t}/many.tar", {f"d/f{i}": b"x" for i in range(5)}, "w")
            with self.assertRaisesRegex(BuildError, "more than"): M["extract_tar"](arc, f"{self.t}/m1")
            zp = f"{self.t}/many.zip"
            with zipfile.ZipFile(zp, "w") as zf:
                for i in range(5): zf.writestr(f"d/f{i}", "x")
            with self.assertRaisesRegex(BuildError, "more than"): M["extract_zip"](zp, f"{self.t}/m2")
        finally: M["MAX_ENTRIES"] = old
        zp = self.zip_with_links(f"{self.t}/biglink.zip", [("big", "a" * (M["MAX_LINK"] + 1)), ("ok", "data")])
        with zipfile.ZipFile(zp, "a") as zf: zf.writestr("data", "x")
        M["extract_zip"](zp, f"{self.t}/m3")
        self.assertFalse(os.path.lexists(f"{self.t}/m3/big")); self.assertTrue(os.path.islink(f"{self.t}/m3/ok"))

    def test_cli_warns_when_it_would_run_a_package_without_the_sandbox(self):
        deb = self.sample_deb()
        def run(*extra):
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err): M["cli"]([deb, *extra])
            return err.getvalue()
        self.assertIn("without the sandbox", run("--try", "--seconds", "2"))
        self.assertNotIn("without the sandbox", run("--bundle-libs", "--dry-run"))
        self.assertNotIn("without the sandbox", run("--dry-run"))

    def test_package_layout_apprun_and_preview(self):
        root = f"{self.t}/root"; M["unpack_package"](self.sample_deb(), root)
        ident, desktop, apprun = M["preview"](root, "Fox Browser", "usr/bin/fox", None, "Network", False)
        self.assertIn("XDG_DATA_DIRS", apprun); self.assertIn("usr/share", apprun); self.assertIn("usr/lib/fox", apprun)
        self.assertNotIn("usr/lib\"", apprun.split("LD_LIBRARY_PATH=")[1].split("\n")[0])
        plain = M["apprun_script"]("a", "run", [])
        self.assertNotIn("XDG_DATA_DIRS", plain); self.assertNotIn("APP=", plain)         # an ordinary folder is unchanged
        self.assertIn('"$HERE"/usr/lib', M["apprun_script"]("a", "run", [], False, True))

    def test_cli_takes_a_deb_and_fills_in_what_the_package_says(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(M["cli"]([self.sample_deb(), "--dry-run"]), 0)
        self.assertIn("Name=Fox Browser", out.getvalue()); self.assertIn("Categories=Network;", out.getvalue())
        self.assertIn("usr/bin/fox", out.getvalue()); self.assertIn("From the package", err.getvalue())
        err = io.StringIO()
        with contextlib.redirect_stderr(err): self.assertEqual(M["cli"]([f"{self.t}/no.deb", "--dry-run"]), 1)

    def test_bundling_runs_linuxdeploy_and_needs_it(self):
        root = f"{self.t}/root"; M["unpack_package"](self.sample_deb(), root)
        old = os.environ["PATH"]
        try:
            os.environ["PATH"] = f"{self.t}/emptybin"
            with self.assertRaisesRegex(BuildError, "linuxdeploy"):
                M["make_appdir"](root, "Fox", "usr/bin/fox", None, "Network", False, f"{self.t}/A1", "", "", True)
            os.makedirs(f"{self.t}/bin")
            script(f"{self.t}/bin/linuxdeploy", 'mkdir -p "$2/usr/lib"; echo bundled > "$2/usr/lib/libbundled.so"\n')
            os.environ["PATH"] = f"{self.t}/bin:{old}"
            M["make_appdir"](root, "Fox", "usr/bin/fox", None, "Network", False, f"{self.t}/A2", "", "", True)
        finally: os.environ["PATH"] = old
        self.assertTrue(os.path.isfile(f"{self.t}/A2/usr/lib/libbundled.so"))
        self.assertIn('"$HERE"/usr/lib', get(f"{self.t}/A2/AppRun"))
        self.assertEqual(M["packing_notes"](root, "usr/bin/fox", True).count("shared libraries"), 0)

    def test_linuxdeploy_download_installs_a_real_appimage_only(self):
        self.fake_appimage(f"{self.t}/good"); good = get(f"{self.t}/good", "rb")
        dest = f"{self.t}/inst/linuxdeploy"
        got = M["download_linuxdeploy"](dest, self.opener(good))
        self.assertEqual(got, dest); self.assertTrue(os.access(dest, os.X_OK))
        with self.assertRaisesRegex(BuildError, "not an AppImage"): M["download_linuxdeploy"](f"{self.t}/inst/x", self.opener(b"<html>"))
        self.assertFalse(os.path.exists(f"{self.t}/inst/x"))
        M["LD_HOME"] = dest; old = os.environ["PATH"]
        try:
            os.environ["PATH"] = f"{self.t}/emptybin"; self.assertEqual(M["find_linuxdeploy"](), dest)     # found without being on PATH
        finally: os.environ["PATH"] = old

    def test_bundling_in_the_sandbox_runs_linuxdeploy_inside_bwrap_without_network(self):
        os.makedirs(f"{self.t}/bin"); tool = script(f"{self.t}/bin/linuxdeploy", "exit 0\n")
        seen, old_run, old_path = [], M["run_streaming"], os.environ["PATH"]
        M["run_streaming"] = lambda cmd, *a, **k: (seen.append(cmd), (0, "", False))[1]
        try:
            os.environ["PATH"] = f"{self.t}/bin:{old_path}"
            M["bundle_libraries"](f"{self.t}/work/AppDir", "x", "bin/x", None, None, "/usr/bin/bwrap")
        finally: M["run_streaming"] = old_run; os.environ["PATH"] = old_path
        cmd = seen[0]
        self.assertEqual(cmd[0], "/usr/bin/bwrap"); self.assertNotIn("--share-net", cmd); self.assertIn("--unshare-all", cmd)
        self.assertIn("APPIMAGE_EXTRACT_AND_RUN", cmd); self.assertEqual(cmd[cmd.index("--") + 1:][0], os.path.realpath(tool))
        self.assertIn(f"{self.t}/work", cmd)                                               # the folder holding the AppDir is writable

    def make_tar(self, path, files, mode="w:xz"):
        with tarfile.open(path, mode) as tf:
            for name, val in files.items():
                ti = tarfile.TarInfo(name); data = val[1] if isinstance(val, tuple) else val
                ti.size, ti.mode = len(data), 0o755 if isinstance(val, tuple) else 0o644
                tf.addfile(ti, io.BytesIO(data))
        return path

    def test_archives_unpack_to_the_folder_inside_and_the_program_is_found(self):
        M["placeholder_icon"](f"{self.t}/ic.png"); ic = get(f"{self.t}/ic.png", "rb")
        files = {"firefox/firefox": ("exe", b"#!/bin/sh\necho hi\n"), "firefox/browser/chrome/icons/default/default128.png": ic,
                 "firefox/browser/chrome/icons/default/default16.png": b"x"}
        for ext, mode in ((".tar.xz", "w:xz"), (".tar.gz", "w:gz"), (".tar.bz2", "w:bz2")):
            arc = self.make_tar(f"{self.t}/firefox-159.0.1{ext}", files, mode)
            root = M["unpack_package"](arc, f"{self.t}/r{ext}")
            self.assertEqual(root, f"{self.t}/r{ext}/firefox")                          # the single top folder is used
            info = M["package_info"](root, M["package_hint"](arc))
            self.assertEqual(info["exe"], "firefox"); self.assertTrue(info["icon"].endswith("default128.png"))
        zp = f"{self.t}/tool-1.0.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zi = zipfile.ZipInfo("tool/run"); zi.external_attr = 0o755 << 16; zf.writestr(zi, "#!/bin/sh\necho z\n")
            zf.writestr("../../escaped.txt", "x"); zf.writestr("tool/data.txt", "d")
        root = M["unpack_package"](zp, f"{self.t}/z")
        self.assertEqual(root, f"{self.t}/z/tool"); self.assertTrue(os.access(f"{root}/run", os.X_OK))
        self.assertFalse(os.path.exists(f"{self.t}/escaped.txt")); self.assertFalse(os.path.exists(f"{self.t}/z/escaped.txt"))
        arch = self.make_tar(f"{self.t}/foo-1.2-1-x86_64.pkg.tar.xz", {"usr/bin/foo": ("exe", b"#!/bin/sh\n"), ".PKGINFO": b"pkgname = foo\n"})
        self.assertEqual(M["unpack_package"](arch, f"{self.t}/arch"), f"{self.t}/arch")  # an Arch package keeps its usr/ tree
        self.assertEqual(M["package_info"](f"{self.t}/arch", "foo")["exe"], "usr/bin/foo")
        if shutil.which("zstd"):
            z = f"{self.t}/zz-1.0.tar.zst"; subprocess.run(["zstd", "-q", "-o", z, self.make_tar(f"{self.t}/zz.tar", {"zz/run": ("exe", b"#!/bin/sh\n")}, "w")], check=True)
            self.assertEqual(M["unpack_package"](z, f"{self.t}/zr"), f"{self.t}/zr/zz")

    def test_package_hint_and_is_package(self):
        h = M["package_hint"]
        self.assertEqual(h("/x/firefox_159.0.1_amd64.deb"), "firefox"); self.assertEqual(h("/x/firefox-159.0.1.tar.xz"), "firefox")
        self.assertEqual(h("foo-1.2-1-x86_64.pkg.tar.zst"), "foo"); self.assertEqual(h("my-app-v2.0.zip"), "my-app")
        self.assertEqual(h("plain.tgz"), "plain")
        for n, want in (("a.zip", True), ("a.tar.gz", True), ("a.deb", True), ("a.txt", False), ("a", False)):
            put(f"{self.t}/{n}", "x"); self.assertEqual(M["is_package"](f"{self.t}/{n}"), want)

    def test_cli_takes_an_archive(self):
        arc = self.make_tar(f"{self.t}/myapp-2.0.tar.gz", {"myapp/bin/myapp": ("exe", b"#!/bin/sh\n")}, "w:gz")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err): self.assertEqual(M["cli"]([arc, "--dry-run"]), 0)
        self.assertIn("Name=myapp", out.getvalue()); self.assertIn("bin/myapp", out.getvalue())

    def test_describe_run_explains_the_usual_causes(self):
        d = M["describe_run"]
        self.assertIn("exec format error", d(126, "bash: x: cannot execute: Exec format error", False, 5, False).lower())
        self.assertIn("could not be started", d(127, "", False, 5, False))
        self.assertIn("window program", d(1, "Gtk-WARNING: cannot open display:", False, 5, True))
        self.assertNotIn("window program", d(1, "cannot open display:", False, 5, False))                # only when sandboxed
        self.assertIn("Output:", d(1, "boom", False, 5, False)); self.assertNotIn("Output:", d(0, "", False, 5, False))

    def test_command_line_try_option(self):
        prog = self.prog_folder()
        with contextlib.redirect_stdout(io.StringIO()) as so:
            self.assertEqual(M["main"]([prog, "--exe", "bin/run", "--try"]), 0)
        self.assertIn("finished normally", so.getvalue())
        script(f"{prog}/bin/run", "exit 5\n")
        with contextlib.redirect_stdout(io.StringIO()) as so:
            self.assertEqual(M["main"]([prog, "--exe", "bin/run", "--try"]), 1)
        self.assertIn("exit code 5", so.getvalue())

    # -- downloads (a fake opener stands in for the network) --
    class Resp:
        def __init__(self, data, url="https://github.com/x"): self.data, self.url, self.pos = data, url, 0
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def geturl(self): return self.url
        def read(self, n):
            c = self.data[self.pos:self.pos + n]; self.pos += n; return c

    def opener(self, data, url="https://github.com/x", calls=None):
        def op(req, timeout=None):
            if calls is not None: calls.append(req.full_url)
            return self.Resp(data, url)
        return op

    def test_download_writes_the_file_and_refuses_bad_cases(self):
        dest = f"{self.t}/d"
        M["download"]("https://github.com/a", dest, self.opener(b"hello")); self.assertEqual(get(dest, "rb"), b"hello")
        with self.assertRaisesRegex(BuildError, "https"): M["download"]("http://github.com/a", dest, self.opener(b"x"))
        with self.assertRaisesRegex(BuildError, "redirected"): M["download"]("https://github.com/a", dest, self.opener(b"x", "http://evil/x"))
        self.assertFalse(os.path.exists(dest))
        M["MAX_DOWNLOAD"] = 10
        with self.assertRaisesRegex(BuildError, "larger than expected"): M["download"]("https://github.com/a", dest, self.opener(b"x" * 100))
        self.assertFalse(os.path.exists(dest))
        def boom(req, timeout=None): raise OSError("no network")
        with self.assertRaisesRegex(BuildError, "no network"): M["download"]("https://github.com/a", dest, boom)
        self.assertFalse(os.path.exists(dest))

    def test_download_tool_and_runtime_install_valid_files_only(self):
        calls = []; arch = M["platform"].machine()
        app = f"{self.t}/a"; self.fake_appimage(app)
        dest = M["download_tool"](f"{self.t}/bin/appimagetool", self.opener(get(app, "rb"), calls=calls))
        self.assertTrue(os.access(dest, os.X_OK)); self.assertTrue(calls[0].endswith(f"appimagetool-{arch}.AppImage"))
        with self.assertRaisesRegex(BuildError, "not an AppImage"): M["download_tool"](f"{self.t}/bin/x", self.opener(b"<html>"))
        rt = f"{self.t}/r"; self.fake_runtime(rt)
        out = M["download_runtime"](f"{self.t}/rt/r", self.opener(get(rt, "rb"), calls=calls))
        self.assertEqual(get(out, "rb"), get(rt, "rb")); self.assertTrue(calls[1].endswith(f"runtime-{arch}"))
        with self.assertRaisesRegex(BuildError, "complete AppImage"): M["download_runtime"](f"{self.t}/rt/r2", self.opener(get(app, "rb")))


class Meta(type):
    def __getattr__(cls, name): return W()                         # Gtk.Align.CENTER, Gtk.StringList.new, ...


class W(metaclass=Meta):
    """A stand-in for any GTK widget: it remembers the few things the window code reads back, and ignores the rest."""
    def __init__(self, *a, **k):
        self.kw, self.text, self.sub, self.css, self.handlers = k, "", None, set(), {}
        self.active, self.sel, self.vis, self.sens, self.label = k.get("active", False), 0, k.get("visible", True), True, k.get("label")
    def __call__(self, *a, **k): return W()
    def __getattr__(self, name): return lambda *a, **k: W()
    def get_text(self): return self.text
    def set_text(self, t): self.text = t
    def set_subtitle(self, t): self.sub = t
    def get_active(self): return self.active
    def get_selected(self): return self.sel
    def set_selected(self, i): self.sel = i
    def get_visible(self): return self.vis
    def set_visible(self, v): self.vis = v
    def get_sensitive(self): return self.sens
    def set_sensitive(self, v): self.sens = v
    def set_label(self, t): self.label = t
    def add_css_class(self, c): self.css.add(c)
    def remove_css_class(self, c): self.css.discard(c)
    def connect(self, sig, cb): self.handlers.setdefault(sig, []).append(cb)
    def set_extra_child(self, c): self.extra = c
    def get_buffer(self): return W()


class Window(unittest.TestCase):
    """The window code run against stand-ins for GTK (this machine may have no GTK or display), to catch slips in the handlers."""
    def setUp(self):
        import sys, types
        self._t = tempfile.TemporaryDirectory(); self.t = os.path.realpath(self._t.name)
        self.alerts, self.wins = [], []
        alerts, wins = self.alerts, self.wins
        class Dialog(W):
            def present(self, parent): alerts.append(self)
        class AppWin(W):
            def __init__(self, *a, **k): super().__init__(**k); wins.append(self)
        class AppBase(W):
            props = types.SimpleNamespace(active_window=None)
            def run(self, argv): self.do_activate(); return 0
        class adw(W): pass                                                  # unknown names (Adw.ToastOverlay, ...) give stand-ins
        adw.AlertDialog, adw.ApplicationWindow, adw.Application = Dialog, AppWin, AppBase
        class glib(W): pass
        glib.idle_add, glib.Error = staticmethod(lambda fn, *a: fn(*a)), Exception
        class gtk(W): pass
        class gdk(W): pass
        class gio(W): pass
        for name in ("Button", "Label", "ScrolledWindow", "Box", "TextView"): setattr(gtk, name, W)      # these keep what they are given
        for name in ("EntryRow", "SwitchRow", "ComboRow", "ActionRow", "PreferencesGroup", "ToastOverlay"): setattr(adw, name, W)
        repo = types.ModuleType("gi.repository"); repo.Adw, repo.GLib, repo.Gtk, repo.Gdk, repo.Gio = adw, glib, gtk, gdk, gio
        gi = types.ModuleType("gi"); gi.require_version = lambda *a: None; gi.repository = repo
        self._old = {k: sys.modules.get(k) for k in ("gi", "gi.repository")}; sys.modules["gi"], sys.modules["gi.repository"] = gi, repo
        self._set = M["SETTINGS"]; M["SETTINGS"] = f"{self.t}/settings.json"
        put(M["SETTINGS"], '{"sandbox": false}')
        tool = script(f"{self.t}/tool", 'for a; do :; done; cp "%s" "$a"\n' % self.fake())
        os.environ["APPIMAGETOOL"] = tool
        M["run_gui"](); self.w = wins[0]

    def tearDown(self):
        import sys
        for k, v in self._old.items():
            if v is None: sys.modules.pop(k, None)
            else: sys.modules[k] = v
        M["SETTINGS"] = self._set; os.environ.pop("APPIMAGETOOL", None); self._t.cleanup()

    def fake(self):
        path = f"{self.t}/fake.AppImage"
        h = bytearray(64); h[:4] = b"\x7fELF"; h[4] = 2; h[5] = 1; h[8:11] = b"AI\x02"
        h[0x28:0x30] = (64).to_bytes(8, "little"); h[0x3A:0x3C] = (64).to_bytes(2, "little"); h[0x3C:0x3E] = (0).to_bytes(2, "little")
        put(path, bytes(h) + b"hsqs" + bytes(60), "wb"); return path

    def folder(self, name, exe="run"):
        d = f"{self.t}/{name}"; os.makedirs(d); script(f"{d}/{exe}", "echo hi\n"); return d

    def body(self, dlg): return dlg.extra.kw["child"].kw["label"]

    def test_the_name_follows_the_program_until_you_type_your_own(self):
        a, b, c = self.folder("alpha"), self.folder("beta"), self.folder("gamma")
        self.w.set_source(a); self.assertEqual(self.w.name_row.get_text(), "alpha")
        self.w.set_source(b); self.assertEqual(self.w.name_row.get_text(), "beta")           # not stuck on the first one
        self.w.name_row.set_text("My Editor"); self.w.set_source(c); self.assertEqual(self.w.name_row.get_text(), "My Editor")
        self.assertEqual(self.w.exes, ["run"]); self.assertEqual(self.w.last_dir, self.t)    # dialogs open here next time

    def test_the_program_matching_the_folder_name_is_chosen_to_start(self):
        d = self.folder("alpha", "ajail"); script(f"{d}/alpha", "echo hi\n")                # "ajail" sorts first, "alpha" is the app
        self.w.set_source(d)
        self.assertEqual(self.w.exes, ["ajail", "alpha"]); self.assertEqual(self.w.exe_row.get_selected(), 1)
        d2 = self.folder("beta", "aaa"); script(f"{d2}/zzz", "")                              # nothing matches: the first one
        self.w.set_source(d2); self.assertEqual(self.w.exe_row.get_selected(), 0)
        self.assertEqual(M["best_exe"](["bin/x", "bin/foo-cli", "foo"], ["foo"]), 2)        # exact name beats a prefix
        self.assertEqual(M["best_exe"](["a", "foo-bin"], ["foo"]), 1); self.assertEqual(M["best_exe"]([], ["x"]), 0)

    def test_empty_name_is_marked_and_cleared(self):
        self.w.name_row.set_text(""); self.w.name_changed(); self.assertIn("error", self.w.name_row.css)
        self.w.name_row.set_text("x"); self.w.name_changed(); self.assertNotIn("error", self.w.name_row.css)

    def test_dropping_a_folder_or_an_icon(self):
        class Dropped:
            def __init__(self, p): self.p = p
            def get_path(self): return self.p
        d = self.folder("dropme"); icon, fake = f"{self.t}/i.png", f"{self.t}/fake.png"
        put(icon, b"\x89PNG\r\n", "wb"); put(fake, "text")
        self.assertTrue(self.w.dropped(None, Dropped(d), 0, 0)); self.assertEqual(self.w.src, d)
        self.assertTrue(self.w.dropped(None, Dropped(icon), 0, 0)); self.assertEqual(self.w.icon, icon)
        self.assertTrue(self.w.dropped(None, Dropped(fake), 0, 0)); self.assertEqual(self.w.icon, icon)    # refused, kept the old one
        self.assertEqual(self.alerts[-1].kw["heading"], "Can't use that icon")
        self.assertFalse(self.w.dropped(None, Dropped(f"{self.t}/nope"), 0, 0)); self.assertFalse(self.w.dropped(None, None, 0, 0))

    def test_dropping_a_deb_fills_the_form_from_the_package(self):
        import time as _t
        class Dropped:
            def __init__(self, p): self.p = p
            def get_path(self): return self.p
        M["placeholder_icon"](f"{self.t}/ic.png")
        deb = make_deb(f"{self.t}/fox_1.0_amd64.deb", {"./usr/bin/fox": ("exe", b"#!/bin/sh\n"), "./usr/share/applications/fox.desktop": DESKTOP,
                                                      "./usr/share/icons/hicolor/256x256/apps/fox.png": get(f"{self.t}/ic.png", "rb")})
        self.assertTrue(self.w.dropped(None, Dropped(deb), 0, 0))
        for _ in range(100):
            if self.w.pkg_tmp: break
            _t.sleep(0.1)
        self.assertTrue(self.w.src.endswith("/root")); self.assertEqual(self.w.exes, ["usr/bin/fox"])
        self.assertTrue(self.w.icon.endswith("fox.png")); self.assertTrue(self.w.go.get_sensitive())
        tmp = self.w.pkg_tmp; self.w.set_source(self.folder("other"))                       # opening another program removes the unpacked one
        self.assertFalse(os.path.exists(tmp)); self.assertIsNone(self.w.pkg_tmp)

    def test_preview_shows_the_files_and_refuses_bad_input(self):
        self.w.preview_it(); self.assertEqual(self.alerts[-1].kw["heading"], "Nothing to preview")
        self.w.set_source(self.folder("prev")); self.w.comment_row.set_text("hello"); self.w.preview_it()
        self.assertEqual(self.alerts[-1].kw["heading"], "Files that will be made")
        for want in ("prev.desktop", "Exec=prev", "Comment=hello", "exec "): self.assertIn(want, self.body(self.alerts[-1]))
        self.w.name_row.set_text("  "); self.w.preview_it(); self.assertEqual(self.alerts[-1].kw["heading"], "Can't preview it")

    def test_a_build_ends_with_a_done_message_that_can_show_the_folder(self):
        import time as _t
        self.assertTrue(self.w.go.get_sensitive())
        os.makedirs(f"{self.t}/out"); self.w.out_dir = f"{self.t}/out"
        self.w.set_source(self.folder("built")); self.w.comment_row.set_text("c")
        self.w.shortcut_build()                                                                # Ctrl+B
        for _ in range(100):
            if self.alerts: break
            _t.sleep(0.1)
        dlg = self.alerts[-1]; self.assertEqual(dlg.kw["heading"], "Done")
        self.assertTrue(self.body(dlg).startswith(f"{self.t}/out/built-")); self.assertEqual(self.w.go.label, "Build AppImage")
        self.assertTrue(self.w.tool_group.get_sensitive() and self.w.prev_btn.get_sensitive())
        dlg.handlers["response"][0](dlg, "extra")                                              # the "Show in folder" button
        self.assertEqual(M["load_settings"]().get("last_dir"), self.t)                         # remembered between runs
        self.assertEqual(M["load_settings"]()["out_dir"], f"{self.t}/out")


if __name__ == "__main__":
    unittest.main(verbosity=2)
