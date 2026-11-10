#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# build.py - joins the fragments in src/ into the single installable file `satchel`.
#
#   python3 build.py            write ./satchel from src/*.py
#   python3 build.py --check    write nothing; fail if ./satchel is not exactly what src/ builds (use in CI / before commit)
#
# The fragments are named NN_name.py and are joined in name order. NN is the layer: a fragment may use names defined in
# its own fragment or in a LOWER one, never in a higher one. The build refuses to run when that rule is broken, when two
# fragments define the same top-level name, or when the result does not parse. Nothing is added to the code: the output is
# the fragments joined by two blank lines. Python standard library only.
import ast, glob, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "satchel")


def fragments():
    paths = sorted(glob.glob(os.path.join(HERE, "src", "[0-9][0-9]_*.py")))
    if not paths: sys.exit("build.py: no fragments found in src/")
    return [(os.path.basename(p), open(p, encoding="utf-8").read()) for p in paths]


def top_name(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): return [node.name]
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return [n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]
    return []


def check_layers(frags):
    """Returns a list of problems (empty when the layer rule holds and no name is defined twice)."""
    parsed, layer_of, problems = [], {}, []
    for layer, (fname, text) in enumerate(frags):
        try: tree = ast.parse(text, fname)
        except SyntaxError as e: return [f"{fname}: does not parse: {e}"]
        parsed.append(tree)
        for node in tree.body:
            for n in top_name(node):
                if n in layer_of: problems.append(f"{fname}: '{n}' is already defined in {frags[layer_of[n]][0]}")
                else: layer_of[n] = layer
    for layer, tree in enumerate(parsed):
        for node in tree.body:
            for n in ast.walk(node):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and layer_of.get(n.id, -1) > layer:
                    owner = (top_name(node) or ["<statement>"])[0]
                    problems.append(f"{frags[layer][0]}: '{owner}' (line {n.lineno}) uses '{n.id}' from the higher layer {frags[layer_of[n.id]][0]}")
    return problems


def join(frags):
    return "\n\n".join(text if text.endswith("\n") else text + "\n" for _, text in frags)


def main(argv):
    if any(a not in ("--check",) for a in argv): sys.exit(__doc__)
    frags = fragments()
    problems = check_layers(frags)
    if problems:
        print("build.py: layer rule broken:", *problems, sep="\n  ", file=sys.stderr)
        return 1
    out = join(frags)
    try: ast.parse(out, "satchel")
    except SyntaxError as e:
        print(f"build.py: the joined file does not parse: {e}", file=sys.stderr)
        return 1
    if "--check" in argv:
        try:
            with open(OUT, encoding="utf-8") as f: same = f.read() == out
        except OSError: same = False
        print("satchel is up to date with src/" if same else "satchel is NOT what src/ builds: run python3 build.py", file=sys.stderr if not same else sys.stdout)
        return 0 if same else 1
    with open(OUT + ".new", "w", encoding="utf-8") as f: f.write(out)
    os.chmod(OUT + ".new", 0o755)
    os.replace(OUT + ".new", OUT)
    print(f"wrote satchel ({out.count(chr(10))} lines from {len(frags)} fragments)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
