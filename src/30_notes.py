def size_note(src):
    """A warning when the program folder is very big, or ''. The folder is copied once into the app folder and again into the image."""
    if os.path.isdir(src) and folder_size(src, BIG_FOLDER) > BIG_FOLDER:
        return ("Heads up: the program folder is over 1 GB. Building copies it twice, so it needs plenty of free disk space "
                "and takes a while, and the AppImage will be big.")
    return ""


def links_note(links):
    """A warning about links that were left out of the AppImage, or '' when there are none."""
    if not links: return ""
    shown = ", ".join(links[:8]) + (f" and {len(links) - 8} more" if len(links) > 8 else "")
    return ("Heads up: these symbolic links point outside the program folder (or use an absolute path), so they were "
            "left out of the AppImage: " + shown + ". If the program needs them, copy the real files into the folder.")


def packing_notes(src, exe, bundle=False):
    """All the warnings for the result message (wrong kind of computer, missing libraries, links left out, very big
    folder), joined;
    '' when there are none."""
    prog = os.path.join(src, exe) if os.path.isdir(src) else src
    links = outside_links(src) if os.path.isdir(src) else []
    notes = (arch_problem(prog) if os.path.isfile(prog) else "", ("" if bundle else missing_note(missing_libraries(src, exe))), links_note(links),
             size_note(src))
    return "\n\n".join(n for n in notes if n)


def missing_note(missing):
    """A warning for the result message, or '' when nothing is missing."""
    if not missing: return ""
    shown = ", ".join(missing[:12]) + (f" and {len(missing) - 12} more" if len(missing) > 12 else "")
    return ("Heads up: these shared libraries were not found on this system or inside the packed folder: " + shown
            + ". The AppImage will only run where they exist.")
