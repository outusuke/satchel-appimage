SETTINGS = os.path.expanduser("~/.config/satchel/settings.json")


def load_settings(path=None):
    """The choices saved from the last run. Each value is checked; anything missing or wrong is left out."""
    try:
        with open(path or SETTINGS) as f: raw = json.load(f)
    except (OSError, ValueError): return {}
    if not isinstance(raw, dict): return {}
    out = {}
    if isinstance(raw.get("out_dir"), str) and os.path.isdir(raw["out_dir"]): out["out_dir"] = raw["out_dir"]
    if isinstance(raw.get("last_dir"), str) and os.path.isdir(raw["last_dir"]): out["last_dir"] = raw["last_dir"]
    if raw.get("category") in CATEGORIES: out["category"] = raw["category"]
    for k in ("terminal", "sandbox"):
        if isinstance(raw.get(k), bool): out[k] = raw[k]
    return out


def save_settings(data, path=None):
    """Remember the choices. This is a convenience, so a failure to write is ignored and never stops a build."""
    path = path or SETTINGS
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".new", "w") as f: json.dump(data, f, indent=1)
        os.replace(path + ".new", path)
    except OSError: pass
