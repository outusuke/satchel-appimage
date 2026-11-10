# satchel

A small GTK4 + libadwaita front end for `appimagetool`: pick a program (a file, a folder, a `.deb` package, or an archive), name it, pick an icon,
build an `.AppImage`. It packs exactly what you give it. It does **not** copy in shared libraries unless you switch on **Bundle libraries**, so the result
runs only where those libraries exist (the program warns you about libraries it can see are missing).

## Needs

- Python 3, PyGObject, GTK 4, libadwaita (for the window only; the command line needs just Python 3)
- `appimagetool` (the window can download it, or use a file you downloaded)
- optional: `linuxdeploy` (the **Bundle libraries** switch / `--bundle-libs`)
- optional: `zstd` (only for `.zst` packages and archives, unless Python is 3.14 or newer)
- optional: `bubblewrap` (sandboxed builds and test runs), `unsquashfs` from squashfs-tools (to unpack an AppImage-form appimagetool)

## Window

Run `satchel`. Drop a program, folder or icon on the window, or use the buttons.
**Preview files** shows the desktop file and `AppRun` that would be made. **Try it first** starts the program for a few
seconds. **Ctrl+B** builds.

## Packages and archives

Drop a `.deb`, an Arch package (`.pkg.tar.zst`), a `.tar.gz` / `.tar.xz` / `.tar.bz2` / `.tar.zst` archive or a `.zip` on the window
(or pass it on the command line). It is unpacked to a temporary folder. For a package, the program, name, description,
category and icon are read from its own `.desktop` file, and `AppRun` points `PATH`, `XDG_DATA_DIRS` and `LD_LIBRARY_PATH`
into the package. For a plain archive the program and icon are found inside it like in any folder. `.rpm` is not supported yet.

## Command line

    satchel PROGRAM [--name N] [--exe E] [--icon I] [--category C] [--version V] [--comment T]
                         [--terminal] [--bundle-libs]
                         [--writable-data [--writable NAME]...] [--out DIR] [--sandbox] [--try [--seconds S]] [--dry-run] [-v]

If you give a folder and no icon, the most likely one inside it is used (it follows the `Icon=` line of a `.desktop` file there, then the file and folder names, then the size). In the window, when a folder holds several programs, the one named like the folder is chosen to start first.

A program, or a folder it sits in, that is a symbolic link leading outside the packed folder is refused, since the link would not be packed.

`--dry-run` prints the files that would be made and builds nothing. `--try` only starts the program to see that it works.

## Programs that write next to themselves

An AppImage is read-only, so a program that reads `translations/` and saves `settings.json` or downloads files into its own folder fails.
Switch on **Writable data folder** (`--writable-data`) and `AppRun` starts the program in `~/.local/share/<name>` (or `$XDG_DATA_HOME/<name>`).
Nothing has to be set up for the program. Each time it starts, `AppRun` rebuilds the packed folder's tree there: every folder is a real
folder and every file is a link to the packed file. The program sees all its files (they follow app updates) and can create files and
folders anywhere, such as downloads, caches or saves. Small settings-like files (`.json`, `.ini`, `.conf`, `.toml`, `.yaml`, `.xml`, under 64 KB)
are copied once, at any depth, and then left alone so the program can change them. Files the program made are never touched. Links left by an
earlier start, including whole-folder links from an older Satchel, are replaced. If the data folder cannot be written (for example a sandbox
that keeps `~/.local/share` read-only), `AppRun` says so and stops.

Only one copy of such an app should run at a time: starting a second one relinks the files to its own mount.
A program that changes a big existing file in place cannot do it through a link. For that rare case list the file or folder with
`--writable PATH` (the window has a box for it); it is copied once instead of linked. `--dry-run` shows the exact `AppRun`.
`AppRun` uses `cp -rs` and `find` from coreutils and findutils.

## The sandbox

`--sandbox` (the switch in the window) hides your home folder and, once a runtime file is set, the network. It is a convenience, not a security
boundary: the rest of the system stays readable, there is no system-call filter, and a test run shares your screen (on X11 a program can see
your keystrokes). Do not run programs you do not trust. Unpacking limits: an archive with more than 500,000 entries is refused, and symbolic
links that lead out of the unpacked folder are removed.

## Development

`satchel` is built from the fragments in `src/` (named `NN_name.py`, joined in name order; a fragment may only use names from its own or a lower-numbered one).
Edit `src/`, then run `python3 build.py` to rewrite `satchel`; `python3 build.py --check` fails if `satchel` is not exactly what `src/` builds.
The build refuses to run when the layer rule is broken. The installable program is still the single file `satchel`.

## Tests

    python3 test_satchel.py

The tests need neither GTK nor a real appimagetool.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
