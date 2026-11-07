#!/usr/bin/env python3
"""Inventory Basic Solution client repos: versions, deps, overlap, diffs, missing deps vs slim build."""
import ast, hashlib, json, os, sys

REPOS = {
    "pino": "/home/claude/clients/pino-main-01-11/pino-main-01-11",
    "bunbastic": "/home/claude/clients/bunbastic-main-03-11/bunbastic-main",
    "puls": "/home/claude/clients/081d8a25-puls-c-main/puls-c-main",
}
SLIM = ["/home/claude/build/odoo/addons", "/home/claude/build/odoo/odoo/addons", "/home/claude/build/enterprise",
        "/home/claude/resta_addons"]
FULL = ["/home/claude/community/addons", "/home/claude/src/enterprise-19.0"]


def tree_hash(path):
    h = hashlib.sha1()
    for root, dirs, files in sorted(os.walk(path)):
        dirs[:] = sorted(d for d in dirs if d not in ("__pycache__", ".git"))
        for f in sorted(files):
            if f.endswith((".pyc",)) or f == ".DS_Store":
                continue
            p = os.path.join(root, f)
            h.update(os.path.relpath(p, path).encode())
            h.update(open(p, "rb").read())
    return h.hexdigest()[:10]


def size(path):
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(path) for f in fs)


mods = {}
for repo, root in REPOS.items():
    for dirpath, dirs, files in os.walk(root):
        if "__MACOSX" in dirpath:
            continue
        if "__manifest__.py" in files:
            name = os.path.basename(dirpath)
            man = ast.literal_eval(open(os.path.join(dirpath, "__manifest__.py"), encoding="utf-8").read())
            mods.setdefault(name, {})[repo] = {
                "path": dirpath, "version": man.get("version", "?"), "depends": man.get("depends", []),
                "hash": tree_hash(dirpath), "size_kb": size(dirpath) // 1024,
                "summary": (man.get("summary") or man.get("name") or "")[:70],
                "installable": man.get("installable", True), "license": man.get("license", ""),
                "auto_install": man.get("auto_install", False),
            }
            dirs[:] = []

slim = {m for d in SLIM if os.path.isdir(d) for m in os.listdir(d)}
full = {m for d in FULL for m in os.listdir(d)}
for name, per in mods.items():
    for info in per.values():
        info["missing_in_slim"] = [d for d in info["depends"] if d not in slim and d not in mods]
        info["missing_everywhere"] = [d for d in info["depends"] if d not in full and d not in mods and d not in slim]

json.dump(mods, open("/home/claude/out/bs_inventory.json", "w"), indent=1, ensure_ascii=False)

print(f"{'module':38} {'repos':22} {'ver(s)':28} same  KB   missing-in-slim")
for name in sorted(mods):
    per = mods[name]
    repos = ",".join(per)
    vers = ",".join(sorted({i["version"] for i in per.values()}))
    same = "yes" if len({i["hash"] for i in per.values()}) == 1 else "DIFF"
    miss = sorted({d for i in per.values() for d in i["missing_in_slim"]})
    kb = max(i["size_kb"] for i in per.values())
    print(f"{name:38} {repos:22} {vers:28} {same:5} {kb:5} {' '.join(miss)}")
