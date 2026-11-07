#!/usr/bin/env bash
# Build the slim Resta Odoo 19 tree from full community + enterprise sources.
# Usage: build_slim.sh <community_root> <enterprise_dir> <keep.json> <out_dir>
set -euo pipefail
COMM="$1"; ENT="$2"; KEEP="$3"; OUT="$4"
rm -rf "$OUT"; mkdir -p "$OUT/odoo" "$OUT/enterprise"

# 1) Odoo server core (no doc/debian/setup), only base + kept core addons
(cd "$COMM" && tar --exclude=./addons --exclude=./doc --exclude=./debian --exclude=./setup --exclude=./.git --exclude="./odoo/addons/*" -cf - .) | (cd "$OUT/odoo" && tar -xf -)
mkdir -p "$OUT/odoo/addons" "$OUT/odoo/odoo/addons"
cp "$COMM/odoo/addons/__init__.py" "$OUT/odoo/odoo/addons/" 2>/dev/null || true

python3 -I - "$KEEP" "$COMM" "$ENT" "$OUT" <<'EOF'
import json, os, shutil, sys
keep, comm, ent, out = sys.argv[1:]
mods = json.load(open(keep))["keep"]
for m in mods:
    for src, dst in ((os.path.join(comm, "odoo/addons", m), os.path.join(out, "odoo/odoo/addons", m)),
                     (os.path.join(comm, "addons", m), os.path.join(out, "odoo/addons", m)),
                     (os.path.join(ent, m), os.path.join(out, "enterprise", m))):
        if os.path.isdir(src):
            shutil.copytree(src, dst)
            break
    else:
        print("NOT FOUND", m)
EOF

# 2) Strip tests and non ar/en translations inside kept modules
find "$OUT" -type d -name tests -path '*/addons/*' -prune -exec rm -rf {} + 2>/dev/null || true
find "$OUT/enterprise" -maxdepth 2 -type d -name tests -exec rm -rf {} + 2>/dev/null || true
find "$OUT" -path '*/static/tests' -type d -prune -exec rm -rf {} +
find "$OUT" -path '*/i18n/*.po' ! -name 'ar.po' ! -name 'ar_SA.po' -delete
find "$OUT" -name '__pycache__' -type d -prune -exec rm -rf {} +
echo "done"; du -sh "$OUT/odoo" "$OUT/enterprise"

# 3) Drop "upsell" app records in base for modules that are no longer shipped
python3 -I - "$OUT" <<'PY'
import os, sys
from lxml import etree
out = sys.argv[1]
f = os.path.join(out, "odoo/odoo/addons/base/data/ir_module_module.xml")
present = set()
for d in ("odoo/addons", "odoo/odoo/addons", "enterprise"):
    p = os.path.join(out, d)
    present |= {m for m in os.listdir(p) if os.path.isdir(os.path.join(p, m))}
tree = etree.parse(f)
dropped = []
for rec in tree.iter("record"):
    name = rec.find("field[@name='name']")
    if name is not None and name.text not in present:
        rec.getparent().remove(rec)
        dropped.append(name.text)
tree.write(f, encoding="utf-8", xml_declaration=True)
print("upsell records dropped:", len(dropped))
PY

# 4) In every shipped data file, drop records whose module_id points to a module
#    that is not shipped (payment providers, website configurator features, ...)
python3 -I - "$OUT" <<'PY'
import os, re, sys
from lxml import etree
out = sys.argv[1]
roots = ["odoo/addons", "odoo/odoo/addons", "enterprise"]
present = {m for r in roots for m in os.listdir(os.path.join(out, r))}
total = 0
for r in roots:
    for dirpath, dirs, files in os.walk(os.path.join(out, r)):
        if "/static" in dirpath:
            continue
        for f in files:
            if not f.endswith(".xml"):
                continue
            p = os.path.join(dirpath, f)
            raw = open(p, "rb").read()
            if b'name="module_id" ref="base.module_' not in raw:
                continue
            tree = etree.parse(p)
            dropped = []
            for rec in list(tree.iter("record")):
                mod = rec.find("field[@name='module_id']")
                if mod is not None and mod.get("ref", "").startswith("base.module_") \
                        and mod.get("ref")[len("base.module_"):] not in present:
                    rec.getparent().remove(rec)
                    dropped.append(rec.get("id"))
            if dropped:
                tree.write(p, encoding="utf-8", xml_declaration=True)
                total += len(dropped)
                print("  %s: dropped %d" % (os.path.relpath(p, out), len(dropped)))
print("records pointing to missing modules dropped:", total)
PY
