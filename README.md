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
                         [--terminal] [--bundle-libs] [--out DIR] [--sandbox] [--try [--seconds S]] [--dry-run] [-v]

If you give a folder and no icon, the most likely one inside it is used (it follows the `Icon=` line of a `.desktop` file there, then the file and folder names, then the size). In the window, when a folder holds several programs, the one named like the folder is chosen to start first.

A program, or a folder it sits in, that is a symbolic link leading outside the packed folder is refused, since the link would not be packed.

`--dry-run` prints the files that would be made and builds nothing. `--try` only starts the program to see that it works.

## Tests

    python3 test_satchel.py

The tests need neither GTK nor a real appimagetool.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
