# ---- window (GTK is loaded only here, so the functions above can be tested without it) ----
def run_gui():
    import gi
    gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk

    class Win(Adw.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Satchel", default_width=560, default_height=760)
            self.saved = load_settings()
            self.src, self.icon, self.exes, self.icon_auto = None, None, [], False
            self.pkg_tmp, self.pkg_busy = None, False    # the folder an opened package is unpacked into
            self.auto_name = ""                         # the name last suggested from the program (see auto_name)
            self.last_dir = self.saved.get("last_dir")  # where the file dialogs open next
            self.out_dir = self.saved.get("out_dir", os.path.expanduser("~"))
            self.cancel_ev = None                       # set while a build runs; the Build button is Cancel then
            self.toasts = Adw.ToastOverlay()
            bar = Adw.HeaderBar()
            about = Gtk.Button(icon_name="help-about-symbolic", tooltip_text="About", css_classes=["flat"])
            about.connect("clicked", lambda *_: self.about()); bar.pack_end(about)
            view = Adw.ToolbarView(); view.add_top_bar(bar)
            page = Adw.PreferencesPage()
            view.set_content(page); self.toasts.set_child(view); self.set_content(self.toasts)

            g = Adw.PreferencesGroup(title="Satchel")
            self.self_row = Adw.ActionRow(title="Add Satchel to the app menu", use_markup=False)
            self.self_rm = Gtk.Button(label="Remove", valign=Gtk.Align.CENTER)
            self.self_rm.connect("clicked", lambda *_: self.menu_self(False))
            self.self_add = Gtk.Button(valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
            self.self_add.connect("clicked", lambda *_: self.menu_self(True))
            self.self_row.add_suffix(self.self_rm); self.self_row.add_suffix(self.self_add)
            g.add(self.self_row); page.add(g); self.refresh_self()

            g = Adw.PreferencesGroup(title="appimagetool", description="The tool that makes the AppImage. Nothing is downloaded unless you press Download (https, from the AppImage project's GitHub releases).")
            self.tool_row = Adw.ActionRow(title="appimagetool", use_markup=False)
            self.use_dl = Gtk.Button(valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
            self.use_dl.connect("clicked", lambda *_: self.install(self.dl))
            self.get_btn = Gtk.Button(label="Download", valign=Gtk.Align.CENTER)
            self.get_btn.connect("clicked", lambda *_: self.fetch(download_tool, self.get_btn))
            self.have_btn = Gtk.Button(label="Use a downloaded file…", valign=Gtk.Align.CENTER, css_classes=["flat"])
            self.have_btn.connect("clicked", lambda *_: self.choose_tool())
            for b in (self.use_dl, self.get_btn, self.have_btn): self.tool_row.add_suffix(b)
            g.add(self.tool_row)
            self.rt_row = Adw.ActionRow(title="Runtime", use_markup=False)
            self.rt_use = Gtk.Button(valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
            self.rt_use.connect("clicked", lambda *_: self.install_rt(self.rt_dl))
            self.rt_get = Gtk.Button(label="Download", valign=Gtk.Align.CENTER)
            self.rt_get.connect("clicked", lambda *_: self.fetch(download_runtime, self.rt_get))
            self.rt_have = Gtk.Button(label="Use a downloaded file…", valign=Gtk.Align.CENTER, css_classes=["flat"])
            self.rt_have.connect("clicked", lambda *_: self.choose_rt())
            for b in (self.rt_use, self.rt_get, self.rt_have): self.rt_row.add_suffix(b)
            g.add(self.rt_row)
            self.ld_row = Adw.ActionRow(title="linuxdeploy", use_markup=False)
            self.ld_use = Gtk.Button(valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
            self.ld_use.connect("clicked", lambda *_: self.install_ld(self.ld_dl))
            self.ld_get = Gtk.Button(label="Download", valign=Gtk.Align.CENTER)
            self.ld_get.connect("clicked", lambda *_: self.fetch(download_linuxdeploy, self.ld_get))
            self.ld_have = Gtk.Button(label="Use a downloaded file…", valign=Gtk.Align.CENTER, css_classes=["flat"])
            self.ld_have.connect("clicked", lambda *_: self.choose_ld())
            for b in (self.ld_use, self.ld_get, self.ld_have): self.ld_row.add_suffix(b)
            g.add(self.ld_row)
            self.tool_group = g
            self.box_row = Adw.SwitchRow(title="Run it in a bubblewrap sandbox", active=self.saved.get("sandbox", True), visible=bool(shutil.which("bwrap")),
                                         subtitle="Hides your home folder. No network once a runtime file is set. Your screen is still shared")
            g.add(self.box_row); page.add(g)

            g = Adw.PreferencesGroup(title="Program", description="What goes inside the AppImage.")
            self.prog_row = Adw.ActionRow(title="Program", subtitle="Nothing chosen yet", use_markup=False)
            self.pick_button(self.prog_row, "File…", lambda: self.choose(False))
            self.pick_button(self.prog_row, "Folder…", lambda: self.choose(True))
            self.exe_row = Adw.ComboRow(title="Starts", subtitle="The program to run inside the folder", visible=False)
            g.add(self.prog_row); g.add(self.exe_row); page.add(g)

            g = Adw.PreferencesGroup(title="App")
            self.name_row = Adw.EntryRow(title="Name")
            self.ver_row = Adw.EntryRow(title="Version (optional)")
            self.comment_row = Adw.EntryRow(title="Description (optional)")
            self.name_row.connect("changed", self.name_changed)
            self.icon_row = Adw.ActionRow(title="Icon", subtitle="None: a plain placeholder is made", use_markup=False)
            self.pick_button(self.icon_row, "Choose…", self.choose_icon)
            self.cat_row = Adw.ComboRow(title="Category", model=Gtk.StringList.new(CATEGORIES))
            if self.saved.get("category"): self.cat_row.set_selected(CATEGORIES.index(self.saved["category"]))
            self.term_row = Adw.SwitchRow(title="Runs in a terminal", active=self.saved.get("terminal", False))
            self.bundle_row = Adw.SwitchRow(title="Bundle libraries (linuxdeploy)", active=False, sensitive=False)
            self.data_row = Adw.SwitchRow(title="Let the program save files (writable data folder)", active=False,
                                          subtitle="Turn on if the program saves settings, downloads or other files next to itself "
                                                   "(it fails or loses them otherwise, because an AppImage is read-only). "
                                                   "It then runs from ~/.local/share/<app name>, where it can write, "
                                                   "and what it saves stays there. Leave off if it only reads its files.")
            self.writ_row = Adw.EntryRow(title="Advanced: big files the program edits in place (usually leave empty)", visible=False,
                                         tooltip_text="Only for a program that changes a large existing file in place, such as a database. "
                                                      "Paths inside the program folder, separated by commas, e.g. data/big.db. "
                                                      "Those files are copied to the data folder once instead of linked. "
                                                      "If the program works without this, leave it empty.")
            self.data_row.connect("notify::active", lambda *_: self.writ_row.set_visible(self.data_row.get_active()))
            for r in (self.name_row, self.ver_row, self.comment_row, self.icon_row, self.cat_row, self.term_row, self.bundle_row,
                      self.data_row, self.writ_row): g.add(r)
            page.add(g)

            g = Adw.PreferencesGroup(title="Result")
            self.out_row = Adw.ActionRow(title="Save in", subtitle=self.out_dir, use_markup=False)
            self.pick_button(self.out_row, "Choose…", self.choose_out)
            self.go = Gtk.Button(label="Build AppImage", css_classes=["suggested-action", "pill"])
            self.go.connect("clicked", self.start)
            self.try_btn = Gtk.Button(label="Try it first", css_classes=["pill"],
                                      tooltip_text="Start the program for a few seconds to see that it works")
            self.try_btn.connect("clicked", self.try_it)
            self.prev_btn = Gtk.Button(label="Preview files", css_classes=["pill"],
                                       tooltip_text="Show the desktop file and AppRun that will be made, without building")
            self.prev_btn.connect("clicked", self.preview_it)
            buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=12)
            buttons.append(self.prev_btn); buttons.append(self.try_btn); buttons.append(self.go)
            g.add(self.out_row); g.add(buttons); page.add(g)

            self.log_view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                         left_margin=8, right_margin=8, top_margin=6, bottom_margin=6)
            self.log_buf = self.log_view.get_buffer()
            self.log_end = self.log_buf.create_mark(None, self.log_buf.get_end_iter(), False)
            self.log_group = Adw.PreferencesGroup(title="Log", visible=False)
            self.log_group.add(Gtk.ScrolledWindow(child=self.log_view, min_content_height=180, css_classes=["card"]))
            page.add(self.log_group)
            self.refresh_tool()
            drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)       # drop a program, folder or icon on the window
            drop.connect("drop", self.dropped); self.add_controller(drop)
            self.connect("close-request", self.closing)
            keys = Gtk.ShortcutController()
            keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Control>b"),
                                               Gtk.CallbackAction.new(self.shortcut_build)))
            self.add_controller(keys)

        def refresh_self(self):
            done = self_installed()
            self.self_row.set_subtitle(f"In your menu. Copy: {SELF_BIN}/satchel" if done else "Copies Satchel to ~/.local/bin and adds it to your menu")
            self.self_add.set_label("Update" if done else "Add"); self.self_rm.set_visible(done)

        def menu_self(self, add):
            try:
                if add:
                    dest, _entry, note = install_self()
                    msg = f"Satchel is in your menu (copy: {dest})"
                else: uninstall_self(); note, msg = "", "Satchel was removed from your menu"
            except BuildError as e: self.alert("Could not change the menu", str(e)); return
            self.refresh_self()
            if note: self.alert("Done", f"{msg}\n\n{note}")
            else: self.toasts.add_toast(Adw.Toast(title=msg))

        def shortcut_build(self, *_):
            if self.go.get_sensitive(): self.start()
            return True

        def about(self):
            kw = dict(application_name="Satchel", version=APP_VERSION, license_type=Gtk.License.GPL_3_0,
                      comments="Pick a program, name it, pick an icon, build an AppImage. It packs exactly what you give it.")
            try: Adw.AboutDialog(**kw).present(self)
            except AttributeError: Adw.AboutWindow(transient_for=self, **kw).present()      # older libadwaita

        def name_changed(self, *_):
            if self.name_row.get_text().strip(): self.name_row.remove_css_class("error")
            else: self.name_row.add_css_class("error")

        def remember(self, path):
            """Open the next file dialog in the folder this path is in."""
            d = os.path.dirname(path.rstrip("/"))
            if d and os.path.isdir(d): self.last_dir = d

        def dialog(self, title):
            dlg = Gtk.FileDialog(title=title)
            if self.last_dir: dlg.set_initial_folder(Gio.File.new_for_path(self.last_dir))
            return dlg

        def save(self):
            save_settings({"out_dir": self.out_dir, "category": CATEGORIES[self.cat_row.get_selected()],
                           "terminal": self.term_row.get_active(), "sandbox": self.box_row.get_active(),
                           **({"last_dir": self.last_dir} if self.last_dir else {})})

        def dropped(self, _target, value, _x, _y):
            path = value.get_path() if value is not None else None
            if not path or not os.path.exists(path): return False
            if os.path.isfile(path) and os.path.splitext(path)[1].lower() in ICON_EXT: self.set_icon(path)
            else: self.set_source(path)
            return True

        def set_icon(self, path):
            why = icon_problem(path)
            if why: return self.alert("Can't use that icon", why)
            self.icon = path; self.icon_auto = False; self.icon_row.set_subtitle(path); self.remember(path)

        def show_folder(self, path):
            try:
                def done(launcher, res):
                    try: launcher.open_containing_folder_finish(res)
                    except GLib.Error: pass
                Gtk.FileLauncher.new(Gio.File.new_for_path(path)).open_containing_folder(self, None, done)
            except AttributeError:                      # GTK older than 4.10
                Gtk.show_uri(self, "file://" + os.path.dirname(path), Gdk.CURRENT_TIME)

        def refresh_tool(self):
            tool, self.dl = find_tool(), find_download()
            self.tool_row.set_subtitle(tool or "Not found")
            self.get_btn.set_visible(not tool); self.have_btn.set_visible(not tool)
            self.use_dl.set_visible(not tool and bool(self.dl))
            if self.dl: self.use_dl.set_label("Use " + os.path.basename(self.dl))
            self.go.set_sensitive(bool(tool))
            rt, self.rt_dl = find_runtime(), find_runtime_download()
            self.rt_row.set_subtitle((rt or "Optional: if not set, appimagetool downloads it itself on every build, so the sandbox gets network (runtime-%s)" % platform.machine()))
            for b in (self.rt_get, self.rt_have): b.set_visible(not rt)
            self.rt_use.set_visible(not rt and bool(self.rt_dl))
            if self.rt_dl: self.rt_use.set_label("Use " + os.path.basename(self.rt_dl))
            ld, self.ld_dl = find_linuxdeploy(), find_linuxdeploy_download()
            self.ld_row.set_subtitle(ld or "Optional: only needed for Bundle libraries")
            for b in (self.ld_get, self.ld_have): b.set_visible(not ld)
            self.ld_use.set_visible(not ld and bool(self.ld_dl))
            if self.ld_dl: self.ld_use.set_label("Use " + os.path.basename(self.ld_dl))
            self.bundle_row.set_sensitive(bool(ld))
            self.bundle_row.set_subtitle("Copies the libraries the program needs into the AppImage" if ld
                                         else "Needs linuxdeploy: use the Download button in the tools section above")
            if not ld: self.bundle_row.set_active(False)

        def install_rt(self, path):
            try: install_runtime(path)
            except BuildError as e: return self.alert("Can't use that file", str(e))
            self.refresh_tool(); self.toasts.add_toast(Adw.Toast(title="Runtime is ready"))

        def choose_rt(self):
            dlg = self.dialog("Choose the runtime file you downloaded")
            def done(d, res):
                try: f = d.open_finish(res)
                except GLib.Error: return
                self.install_rt(f.get_path())
            dlg.open(self, None, done)

        def install_ld(self, path):
            try: install_tool(path, LD_HOME)
            except BuildError as e: return self.alert("Can't use that file", str(e))
            self.refresh_tool(); self.toasts.add_toast(Adw.Toast(title="linuxdeploy is ready"))

        def choose_ld(self):
            dlg = self.dialog("Choose the linuxdeploy file you downloaded")
            def done(d, res):
                try: f = d.open_finish(res)
                except GLib.Error: return
                self.install_ld(f.get_path())
            dlg.open(self, None, done)

        def install(self, path):
            try: install_tool(path)
            except BuildError as e: return self.alert("Can't use that file", str(e))
            self.refresh_tool(); self.toasts.add_toast(Adw.Toast(title="appimagetool is ready"))

        def choose_tool(self):
            dlg = self.dialog("Choose the appimagetool file you downloaded")
            def done(d, res):
                try: f = d.open_finish(res)
                except GLib.Error: return
                self.install(f.get_path())
            dlg.open(self, None, done)

        def fetch(self, fn, btn):
            """Download in the background: the file comes from the AppImage project's GitHub releases over https."""
            btn.set_sensitive(False); btn.set_label("Downloading…")
            def work():
                try: fn(); err = None
                except BuildError as e: err = str(e)
                GLib.idle_add(self.fetched, btn, err)
            threading.Thread(target=work, daemon=True).start()

        def fetched(self, btn, err):
            btn.set_sensitive(True); btn.set_label("Download")
            self.refresh_tool()
            if err: self.alert("Download failed", err)
            else: self.toasts.add_toast(Adw.Toast(title="Downloaded and installed"))

        def pick_button(self, row, label, cb):
            b = Gtk.Button(label=label, valign=Gtk.Align.CENTER, css_classes=["flat"])
            b.connect("clicked", lambda *_: cb()); row.add_suffix(b)

        def alert(self, heading, body, extra=None):
            """A message you can select with the mouse, with a Copy button (long errors scroll). extra is an optional
            (label, function) pair for one more button."""
            d = Adw.AlertDialog(heading=heading)
            lab = Gtk.Label(label=body, wrap=True, selectable=True, xalign=0, css_classes=["monospace"])
            d.set_extra_child(Gtk.ScrolledWindow(child=lab, propagate_natural_height=True, max_content_height=320, min_content_width=360))
            d.add_response("copy", "Copy")
            if extra: d.add_response("extra", extra[0])
            d.add_response("ok", "OK")
            d.set_default_response("ok"); d.set_close_response("ok")
            def answered(_d, r):
                if r == "copy": self.get_clipboard().set(body)
                elif r == "extra": extra[1]()
            d.connect("response", answered)
            d.present(self)

        def choose(self, folder):
            dlg = self.dialog("Choose the program folder" if folder else "Choose the program file")
            def done(d, res):
                try: f = d.select_folder_finish(res) if folder else d.open_finish(res)
                except GLib.Error: return
                self.set_source(f.get_path())
            (dlg.select_folder if folder else dlg.open)(self, None, done)

        def data_arg(self):
            """None when the writable data folder is off, else the extra names to copy."""
            if not self.data_row.get_active(): return None
            return [n.strip() for n in self.writ_row.get_text().split(",") if n.strip()]

        def bundling(self):
            return self.bundle_row.get_active() and self.bundle_row.get_sensitive()

        def closing(self, *_):
            self.drop_package()
            return False

        def drop_package(self):
            """Delete the folder an opened package was unpacked into (and forget an icon taken from it)."""
            tmp, self.pkg_tmp = self.pkg_tmp, None
            if not tmp: return
            if self.icon and self.icon.startswith(tmp + os.sep):
                self.icon, self.icon_auto = None, False; self.icon_row.set_subtitle("None: a plain placeholder is made")
            shutil.rmtree(tmp, ignore_errors=True)

        def open_package(self, path):
            if self.pkg_busy: return
            self.pkg_busy = True
            self.prog_row.set_subtitle(f"Unpacking {os.path.basename(path)}…")
            for b in (self.go, self.try_btn, self.prev_btn): b.set_sensitive(False)
            def work():
                tmp = tempfile.mkdtemp(prefix="satchel-pkg-")
                try:
                    root = unpack_package(path, f"{tmp}/root")
                    res = (tmp, root, package_info(root, package_hint(path)), None)
                except BuildError as e:
                    shutil.rmtree(tmp, ignore_errors=True); res = (None, None, None, str(e))
                GLib.idle_add(self.package_ready, path, *res)
            threading.Thread(target=work, daemon=True).start()

        def package_ready(self, path, tmp, root, info, err):
            self.pkg_busy = False
            for b in (self.go, self.try_btn, self.prev_btn): b.set_sensitive(True)
            if err:
                self.prog_row.set_subtitle(self.src or "Nothing chosen yet")
                return self.alert("Can't open the package", err)
            self.drop_package(); self.pkg_tmp = tmp
            self.src = root
            self.prog_row.set_subtitle(f"{os.path.basename(path)} (unpacked)")
            self.exes = info["exes"]
            self.exe_row.set_visible(True); self.exe_row.set_model(Gtk.StringList.new(self.exes))
            if info["exe"] in self.exes: self.exe_row.set_selected(self.exes.index(info["exe"]))
            else: self.alert("No program found", "There is no program in that file (looked in usr/bin, usr/games, opt and the top folders).")
            new = info["name"] or package_hint(path)
            self.name_row.set_text(auto_name(self.name_row.get_text(), self.auto_name, new)); self.auto_name = new
            if info["comment"] and not self.comment_row.get_text(): self.comment_row.set_text(info["comment"])
            if info["category"]: self.cat_row.set_selected(CATEGORIES.index(info["category"]))
            if info["terminal"]: self.term_row.set_active(True)
            if info["icon"] and (self.icon is None or self.icon_auto):
                self.icon, self.icon_auto = info["icon"], True
                self.icon_row.set_subtitle(os.path.relpath(info["icon"], self.src) + "  (found in the package)")
            self.remember(path)

        def set_source(self, path):
            if is_package(path): return self.open_package(path)
            self.drop_package()
            self.src = path
            self.prog_row.set_subtitle(path)
            is_dir = os.path.isdir(path)
            self.exes = find_executables(path) if is_dir else []
            self.exe_row.set_visible(is_dir)
            self.exe_row.set_model(Gtk.StringList.new(self.exes))
            if self.exes: self.exe_row.set_selected(best_exe(self.exes, [default_name(path), os.path.basename(os.path.realpath(path))]))
            if is_dir and not self.exes: self.alert("No program found", "That folder has no executable file in it (two levels deep).")
            new = default_name(path)
            self.name_row.set_text(auto_name(self.name_row.get_text(), self.auto_name, new)); self.auto_name = new
            if is_dir and (self.icon is None or self.icon_auto):                # never replaces an icon you chose
                found = find_icon(path, [os.path.basename(os.path.realpath(path)), *(os.path.basename(e) for e in self.exes[:3])])
                if found: self.icon, self.icon_auto = found, True; self.icon_row.set_subtitle(found + "  (found in the folder)")
            self.remember(path)

        def choose_icon(self):
            dlg = self.dialog("Choose an icon (.png or .svg)")
            def done(d, res):
                try: f = d.open_finish(res)
                except GLib.Error: return
                self.set_icon(f.get_path())
            dlg.open(self, None, done)

        def choose_out(self):
            dlg = self.dialog("Where to save the AppImage")
            def done(d, res):
                try: f = d.select_folder_finish(res)
                except GLib.Error: return
                self.out_dir = f.get_path(); self.out_row.set_subtitle(self.out_dir)
            dlg.select_folder(self, None, done)

        def log_line(self, line):
            GLib.idle_add(self.append_log, line)        # called from the worker thread

        def append_log(self, line):
            self.log_buf.insert(self.log_buf.get_end_iter(), line + "\n")
            self.log_view.scroll_to_mark(self.log_end, 0.0, False, 0.0, 0.0)

        def show_log(self):
            self.log_buf.set_text(""); self.log_group.set_visible(True)

        def start(self, *_):
            if self.cancel_ev:                          # a build is running: this button is Cancel now
                self.cancel_ev.set(); self.go.set_sensitive(False); self.go.set_label("Cancelling…")
                return
            if not self.src: return self.alert("Nothing to pack", "Choose a program file or folder first.")
            exe = self.exes[self.exe_row.get_selected()] if self.exes else ""
            sandbox = self.box_row.get_active() and self.box_row.get_visible()
            self.cancel_ev = cancel = threading.Event()
            args = (self.src, self.name_row.get_text(), exe, self.icon, CATEGORIES[self.cat_row.get_selected()],
                    self.term_row.get_active(), self.out_dir, None, sandbox, self.ver_row.get_text(), self.log_line, cancel,
                    self.comment_row.get_text(), self.bundling(), self.data_arg())
            self.save()
            self.show_log(); self.try_btn.set_sensitive(False); self.prev_btn.set_sensitive(False); self.tool_group.set_sensitive(False)
            self.go.set_label("Cancel"); self.go.remove_css_class("suggested-action"); self.go.add_css_class("destructive-action")
            def work():
                note = packing_notes(self.src, exe, self.bundling())
                try: res = (build(*args), None, False)
                except Cancelled: res = (None, None, True)
                except BuildError as e: res = (None, str(e), False)
                GLib.idle_add(self.finished, *res, note)
            threading.Thread(target=work, daemon=True).start()

        def finished(self, out, err, cancelled, note=""):
            self.cancel_ev = None
            self.go.set_label("Build AppImage"); self.go.remove_css_class("destructive-action"); self.go.add_css_class("suggested-action")
            self.try_btn.set_sensitive(True); self.prev_btn.set_sensitive(True); self.tool_group.set_sensitive(True); self.refresh_tool()
            if cancelled: self.toasts.add_toast(Adw.Toast(title="Build cancelled"))
            elif err: self.alert("Could not build it", err)
            else: self.alert("Done", out + (f"\n\n{note}" if note else ""), ("Show in folder", lambda: self.show_folder(out)))

        def try_it(self, *_):
            if not self.src: return self.alert("Nothing to try", "Choose a program file or folder first.")
            exe = self.exes[self.exe_row.get_selected()] if self.exes else ""
            sandbox = self.box_row.get_active() and self.box_row.get_visible()
            args = (self.src, self.name_row.get_text(), exe, self.icon, CATEGORIES[self.cat_row.get_selected()],
                    self.term_row.get_active(), sandbox, 5, self.log_line, None, self.bundling(), self.data_arg())
            self.save()
            self.show_log(); self.try_btn.set_sensitive(False); self.go.set_sensitive(False); self.prev_btn.set_sensitive(False)
            self.tool_group.set_sensitive(False); self.try_btn.set_label("Trying…")
            def work():
                try: res = (try_run(*args), None)
                except BuildError as e: res = (None, str(e))
                GLib.idle_add(self.tried, *res)
            threading.Thread(target=work, daemon=True).start()

        def preview_it(self, *_):
            if not self.src: return self.alert("Nothing to preview", "Choose a program file or folder first.")
            exe = self.exes[self.exe_row.get_selected()] if self.exes else ""
            try: ident, desktop, apprun = preview(self.src, self.name_row.get_text(), exe, self.icon, CATEGORIES[self.cat_row.get_selected()],
                                                  self.term_row.get_active(), self.ver_row.get_text(), self.comment_row.get_text(), self.bundling(), self.data_arg())
            except BuildError as e: return self.alert("Can't preview it", str(e))
            self.alert("Files that will be made", f"{ident}.desktop\n{desktop}\nAppRun\n{apprun}")

        def tried(self, result, err):
            self.try_btn.set_sensitive(True); self.try_btn.set_label("Try it first"); self.prev_btn.set_sensitive(True)
            self.tool_group.set_sensitive(True); self.refresh_tool()
            if err: self.alert("Could not try it", err)
            else: self.alert("It works" if result[0] else "It did not start", result[1])

    class App(Adw.Application):
        def __init__(self): super().__init__()                  # no application id: not needed, only gives single-instance and D-Bus
        def do_activate(self): (self.props.active_window or Win(self)).present()

    return App().run(sys.argv[:1])
