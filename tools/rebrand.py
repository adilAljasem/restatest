#!/usr/bin/env python3
"""Re-skin Basic Solution modules with the Resta palette (resta_sidebar_theme tokens).

Only brand colours are touched; status colours (success/warning/danger) and
delivery-partner colours (Blend, TryOrder…) are left as they are.
Usage: rebrand.py <addons_dir>   — prints every change.
"""
import os, re, sys

ROOT = sys.argv[1]
ORANGE, ORANGE_DARK, TINT, BLACK, WARM, MUTED, BORDER = "#FF5A1F", "#E9430A", "#FFF4EF", "#161616", "#F9F7F3", "#706D69", "#DED9D3"
FONT = '"IBM Plex Sans Arabic", '

GLOBAL = [  # (regex, replacement) applied to every module
    (r"#714b67\b", ORANGE),            # Odoo purple
    (r"#017e84\b", ORANGE),            # Odoo teal
    (r"#f8f6f8\b", TINT),              # Odoo purple tint
]
PER_MODULE = {
    "bs_hr_ess_base": [(r"#0F766E\b", ORANGE), (r"--ess-bg:\s*#F4F6F5", f"--ess-bg: {WARM}"),
                       (r"--ess-text:\s*#1F2A28", f"--ess-text: {BLACK}"), (r"--ess-muted:\s*#6B7774", f"--ess-muted: {MUTED}"),
                       (r"--ess-border:\s*#E3E8E6", f"--ess-border: {BORDER}"),
                       (r"(\.o_bs_ess \{\n)", r"\1    font-family: " + FONT + "system-ui, sans-serif;\n")],
    "bs_hr_ess_attendance": [(r"#0F766E\b", ORANGE), (r"#042F2E\b", BLACK)],
    "bs_hr_ess_reports": [(r"#0F766E\b", ORANGE)],
    "bs_pos_delivery_core": [(r"(--bs-aggregator-color:\s*)#7c3aed", r"\g<1>" + ORANGE)],
    "the_dash": [(r"#2BACC9\b", ORANGE), (r"rgba\(43,\s*172,\s*201", "rgba(255,90,31"), (r"#1C3B69\b", BLACK),
                 (r"#4a7bb5\b", "#FF7443"), (r'font-family:system-ui', "font-family:" + FONT + "system-ui")],
}
EXT = (".scss", ".css", ".xml", ".py", ".js", ".html")


def main():
    total = 0
    for module in sorted(os.listdir(ROOT)):
        if module.startswith("resta_sidebar_theme"):
            continue
        rules = GLOBAL + PER_MODULE.get(module, [])
        for dirpath, dirs, files in os.walk(os.path.join(ROOT, module)):
            dirs[:] = [d for d in dirs if d not in ("tests", "description", "i18n", "__pycache__")]
            for f in files:
                if not f.endswith(EXT):
                    continue
                p = os.path.join(dirpath, f)
                s = open(p, encoding="utf-8").read()
                n = s
                for rx, rep in rules:
                    n = re.sub(rx, rep, n, flags=re.I)
                if n != s:
                    open(p, "w", encoding="utf-8").write(n)
                    cnt = sum(1 for a, b in zip(s.splitlines(), n.splitlines()) if a != b)
                    total += cnt
                    print(f"{os.path.relpath(p, ROOT)}: {cnt} lines")
    print("total lines changed:", total)


if __name__ == "__main__":
    main()
