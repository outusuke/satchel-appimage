def elf_dynamic(path):
    """(shared libraries it asks for, folders from its RPATH/RUNPATH, machine number) for an ELF program, read straight
    from the file without running it. ([], [], None) for anything that is not ELF or cannot be read; the machine number
    is still given for a static program, which has no dynamic section."""
    try:
        with open(path, "rb") as f:
            h = f.read(64)
            if len(h) < 64 or h[:4] != b"\x7fELF" or h[4] not in (1, 2) or h[5] not in (1, 2): return [], [], None
            e, wide = ("<" if h[5] == 1 else ">"), h[4] == 2
            machine, = struct.unpack_from(e + "H", h, 0x12)
            none = ([], [], machine)
            if wide: phoff, = struct.unpack_from(e + "Q", h, 0x20); phsz, phnum = struct.unpack_from(e + "HH", h, 0x36)
            else: phoff, = struct.unpack_from(e + "I", h, 0x1C); phsz, phnum = struct.unpack_from(e + "HH", h, 0x2A)
            if not 0 < phnum <= 256 or phsz < 32: return none
            f.seek(phoff)
            raw = f.read(phsz * phnum)
            loads, dyn = [], None
            for i in range(phnum):
                if wide: kind, _fl, off, vaddr, _pa, size, _ms, _al = struct.unpack_from(e + "IIQQQQQQ", raw, i * phsz)
                else: kind, off, vaddr, _pa, size, _ms, _fl, _al = struct.unpack_from(e + "IIIIIIII", raw, i * phsz)
                if kind == 1: loads.append((vaddr, off, size))
                elif kind == 2: dyn = (off, size)
            if not dyn or dyn[1] > 1024 * 1024: return none
            f.seek(dyn[0])
            data, step = f.read(dyn[1]), (16 if wide else 8)
            names, paths, strtab = [], [], None
            for i in range(0, len(data) - step + 1, step):
                tag, val = struct.unpack_from(e + ("qQ" if wide else "iI"), data, i)
                if tag == 0: break
                if tag == 1: names.append(val)
                elif tag in (15, 29): paths.append(val)             # DT_RPATH, DT_RUNPATH
                elif tag == 5: strtab = val
            if strtab is None: return none
            base = next((off + strtab - vaddr for vaddr, off, size in loads if vaddr <= strtab < vaddr + size), None)
            if base is None: return none
            def text(n):
                f.seek(base + n)
                return f.read(1024).split(b"\0", 1)[0].decode("utf-8", "replace")
            return [text(n)[:256] for n in names], [d for n in paths for d in text(n).split(":") if d], machine
    except (OSError, struct.error):
        return [], [], None


def needed_libs(path):
    """The shared libraries an ELF program asks for (its DT_NEEDED entries). Empty for scripts, static programs, anything
    that is not ELF, or a file that cannot be read."""
    return elf_dynamic(path)[0]


MACHINES = {"x86_64": 62, "amd64": 62, "aarch64": 183, "arm64": 183, "i386": 3, "i486": 3, "i586": 3, "i686": 3,
            "armv7l": 40, "armv8l": 40, "riscv64": 243, "ppc64le": 21, "ppc64": 21, "s390x": 22, "loongarch64": 258}


MACHINE_NAMES = {62: "x86_64", 183: "aarch64", 3: "i686", 40: "arm", 243: "riscv64", 21: "ppc64", 22: "s390x", 258: "loongarch64"}


def arch_problem(path, arch=None):
    """A warning when path is an ELF program built for another kind of computer than this one, else ''. Scripts and
    anything it cannot read give ''. The AppImage runtime is always made for this machine, so such a program cannot run in it."""
    arch = arch or platform.machine()
    want, have = MACHINES.get(arch), elf_dynamic(path)[2]
    if not want or not have or have == want: return ""
    return (f"Heads up: '{os.path.basename(path)}' is a program for {MACHINE_NAMES.get(have, 'machine type %d' % have)} "
            f"computers, but this one is {arch}. The AppImage will not run on a {arch} computer.")


def system_libraries():
    """The names of the shared libraries this system has: from 'ldconfig -p' where that works, plus the usual folders."""
    names = set()
    ld = shutil.which("ldconfig") or next((p for p in ("/sbin/ldconfig", "/usr/sbin/ldconfig") if os.access(p, os.X_OK)), None)
    if ld:
        try:
            r = subprocess.run([ld, "-p"], capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL, timeout=20)
            for line in r.stdout.splitlines():
                if "=>" in line and line.split("=>")[0].split(): names.add(line.split("=>")[0].split()[0])
        except (OSError, subprocess.TimeoutExpired): pass
    for d in ("/lib", "/lib64", "/usr/lib", "/usr/lib64", "/usr/local/lib", "/usr/local/lib64",
              *glob.glob("/lib/*-linux-*"), *glob.glob("/usr/lib/*-linux-*")):
        try: names.update(os.listdir(d))
        except OSError: pass
    return names


def missing_libraries(src, exe):
    """Sorted names of shared libraries that the program (and the .so files packed with it) ask for but that are neither
    inside the packed folder nor installed on this system. Folders named in a program's RPATH/RUNPATH count too: one
    inside the packed folder ($ORIGIN/..., which travels with the AppImage) or an absolute one on this system.
    Empty when there are none, or when it cannot tell."""
    src = os.path.realpath(src)
    scan, have, folder = [], set(), os.path.isdir(src)
    try:
        if os.path.isfile(src): scan = [src]
        elif folder:
            prog = os.path.realpath(os.path.join(src, exe or ""))
            if not exe or not prog.startswith(src + os.sep) or not os.path.isfile(prog): return []
            scan = [prog]
            for rel in library_dirs(src):
                d = os.path.join(src, rel)
                for f in sorted(os.listdir(d)):
                    have.add(f)
                    if _is_lib(f) and os.path.isfile(os.path.join(d, f)) and len(scan) < 300: scan.append(os.path.join(d, f))
        else: return []
        wanted, seen = set(), set()
        for p in scan:
            names, paths, _ = elf_dynamic(p)
            wanted.update(names)
            for raw in paths:
                origin = "$ORIGIN" in raw or "${ORIGIN}" in raw
                d = raw.replace("${ORIGIN}", "$ORIGIN").replace("$ORIGIN", os.path.dirname(p))
                if "$" in d or not os.path.isabs(d): continue            # $LIB, $PLATFORM and relative folders: cannot tell
                d = os.path.realpath(d)
                if origin and not (folder and (d == src or d.startswith(src + os.sep))): continue     # would not travel
                if d in seen or len(seen) >= 30: continue
                seen.add(d)
                try: have.update(os.listdir(d))
                except OSError: pass
        wanted -= have
    except OSError:
        return []
    return sorted(wanted - system_libraries()) if wanted else []
