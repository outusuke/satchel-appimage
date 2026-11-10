def need_bwrap():
    """The bubblewrap program, or a BuildError that says how to install it."""
    bwrap = shutil.which("bwrap")
    if not bwrap: raise BuildError(f"The sandbox is switched on but bubblewrap (bwrap) was not found: {install_hint('bubblewrap')}")
    return bwrap


def display_args(env):
    """(bubblewrap arguments, environment pairs) that let a program in the sandbox use your screen: the Wayland socket
    and/or the X11 socket with its login file, plus /dev/dri for graphics. Nothing else of your session is shown, and
    the network stays off. With no screen in env this adds nothing."""
    a, e = [], []
    run = env.get("XDG_RUNTIME_DIR", "")
    wl = env.get("WAYLAND_DISPLAY", "")
    if run and wl:
        sock = wl if os.path.isabs(wl) else os.path.join(run, wl)
        if os.path.exists(sock):
            a += ["--ro-bind", sock, f"/run/satchel-display/{os.path.basename(sock)}"]
            e += [("XDG_RUNTIME_DIR", "/run/satchel-display"), ("WAYLAND_DISPLAY", os.path.basename(sock))]
    disp = env.get("DISPLAY", "")
    if disp and os.path.isdir("/tmp/.X11-unix"):
        a += ["--ro-bind", "/tmp/.X11-unix", "/tmp/.X11-unix"]
        e.append(("DISPLAY", disp))
        xa = env.get("XAUTHORITY") or os.path.expanduser("~/.Xauthority")
        if os.path.isfile(xa):
            a += ["--ro-bind", os.path.realpath(xa), "/run/satchel-display/Xauthority"]
            e.append(("XAUTHORITY", "/run/satchel-display/Xauthority"))
    if e: a += ["--dev-bind-try", "/dev/dri", "/dev/dri"]
    return a, e


def bwrap_base(bwrap, show, work, arch, home, net, version="", display=False):
    """The bubblewrap part of a command, ending in '--': the system is read-only, your real home and other users are
    hidden, /tmp is fresh, and only the work folder is writable. Network only if net (it is needed to download the
    runtime when you have not set one). Files in show that lie inside your home are shown read-only. No system-call
    filter: this is a convenience sandbox, not a security boundary. With display, the screen is let through (see
    display_args) so a window program can open its window; the network stays off."""
    parent = os.path.dirname(home)
    a = [bwrap, "--unshare-all", *(["--share-net"] if net else []), "--die-with-parent", "--new-session", "--clearenv",
         "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", "/run"]
    if net: a += [x for d in ("/run/systemd/resolve", "/run/NetworkManager") for x in ("--ro-bind-try", d, d)]   # DNS
    a += ["--tmpfs", parent] if parent != "/" else []
    a += ["--tmpfs", home]
    for d in ("/root", "/mnt", "/media", "/srv"):
        if os.path.isdir(d) and not os.path.islink(d) and home != d and not home.startswith(d + "/"): a += ["--tmpfs", d]
    for q in dict.fromkeys(os.path.realpath(x) for x in show if x):
        if q.startswith(home + "/"): a += ["--ro-bind", q, q]
    a += ["--bind", work, work]
    envs = []
    if display:
        more, envs = display_args(os.environ)
        a += more
    for k, v in (*envs, ("HOME", home), ("PATH", "/usr/local/bin:/usr/bin:/usr/sbin"), ("LANG", "C.UTF-8"), ("ARCH", arch),
                 *((("VERSION", version),) if version else ())):
        a += ["--setenv", k, v]
    return a + ["--chdir", work, "--"]


def bwrap_cmd(bwrap, exe, share, runtime, appdir, out, work, arch, home, net, version=""):
    """appimagetool inside bubblewrap (see bwrap_base). A tool or runtime file inside your home is shown read-only."""
    return (bwrap_base(bwrap, (share or exe, runtime), work, arch, home, net, version)
            + [exe, *(["--runtime-file", runtime] if runtime else []), appdir, out])


def run_streaming(cmd, env, cwd, log=None, cancel=None, timeout=600, timeout_is_error=True, own_group=True):
    """Run cmd and hand each line of its output (errors included) to log as it appears. The program is stopped when
    cancel is set (raises Cancelled), on Ctrl-C, or after timeout seconds (raises BuildError, or just returns when
    timeout_is_error is False). With own_group it runs in a process group of its own and the whole group is stopped;
    without it only cmd itself is stopped (bubblewrap cannot be a group leader because it starts its own session, and it
    takes everything inside it down with it). Returns (exit code or None, the last lines of output, timed out)."""
    if log: log("$ " + shlex.join(cmd))
    try: p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", env=env,
                              cwd=cwd, stdin=subprocess.DEVNULL, start_new_session=own_group)
    except OSError as e: raise BuildError(f"Could not start {os.path.basename(cmd[0])}: {e}")
    tail = collections.deque(maxlen=200)

    def pump():
        for line in p.stdout:
            line = line.rstrip("\n"); tail.append(line)
            if log: log(line)
    reader = threading.Thread(target=pump, daemon=True); reader.start()

    def stop():
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try: os.killpg(p.pid, sig) if own_group else p.send_signal(sig)
            except OSError: break
            try: p.wait(timeout=3); break
            except subprocess.TimeoutExpired: pass
    why, deadline = None, time.monotonic() + timeout
    try:
        while True:
            try: p.wait(timeout=0.1); break
            except subprocess.TimeoutExpired: pass
            if cancel is not None and cancel.is_set(): why = "cancel"
            elif time.monotonic() > deadline: why = "timeout"
            if why: stop(); break
    except BaseException:
        stop(); raise
    reader.join(timeout=5)
    if not reader.is_alive(): p.stdout.close()
    text = "\n".join(tail)
    if why == "cancel": raise Cancelled("Cancelled.")
    if why == "timeout" and timeout_is_error: raise BuildError(f"It ran for more than {timeout:g} seconds, so it was stopped.")
    return (None if why else p.returncode), text, why == "timeout"
