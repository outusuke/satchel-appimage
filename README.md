# satchel

A small GTK4 + libadwaita front end for `appimagetool`: pick a program (a file or a folder), name it, pick an icon,
build an `.AppImage`. It packs exactly what you give it. It does **not** copy in shared libraries, so the result
runs only where those libraries exist (the program warns you about libraries it can see are missing).

## Needs

- Python 3, PyGObject, GTK 4, libadwaita (for the window only; the command line needs just Python 3)
- `appimagetool` (the window can download it, or use a file you downloaded)
- optional: `bubblewrap` (sandboxed builds and test runs), `unsquashfs` from squashfs-tools (to unpack an AppImage-form appimagetool)

## Window

Run `satchel`. Drop a program, folder or icon on the window, or use the buttons.
**Preview files** shows the desktop file and `AppRun` that would be made. **Try it first** starts the program for a few
seconds. **Ctrl+B** builds.

## Command line

    satchel PROGRAM [--name N] [--exe E] [--icon I] [--category C] [--version V] [--comment T]
                         [--terminal] [--out DIR] [--sandbox] [--try [--seconds S]] [--dry-run] [-v]

`--dry-run` prints the files that would be made and builds nothing. `--try` only starts the program to see that it works.

## Tests

    python3 test_satchel.py

The tests need neither GTK nor a real appimagetool.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
