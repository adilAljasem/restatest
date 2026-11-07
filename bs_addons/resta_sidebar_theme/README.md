# Resta Sidebar Theme — Odoo 19

Foodics-style side navigation for the Odoo 19 backend, in the Resta brand (resta.sa).

## What it does
- Replaces the app grid / top menus with a fixed sidebar on the start side (right in Arabic).
- Apps grouped into sections (automatic, or set per app in *Settings → Resta Theme → Edit app sections*).
- Accordion sub-menus (3 levels), live menu search (Arabic-aware), collapse to icons with flyouts, mobile drawer.
- Light and dark modes: sun/moon button in the top bar and the sidebar. Dark mode uses Odoo's own dark asset bundle, so the page reloads once when switching.
- Resta palette (#FF5A1F, #171717, #F9F7F3 …) and IBM Plex Sans Arabic bundled locally (SIL OFL 1.1).
- Logos (light, dark, collapsed mark) uploaded from Settings; also used on the login page.

## Brand logo
Upload the real wordmark in *Settings → General Settings → Resta Theme*.
Alternatively place `resta-wordmark.png` (or `.svg`) in `static/img/` before deploying; it is picked up automatically.
The included `resta-wordmark-default.svg` is only a placeholder.

## Changing colors
All brand values live in `static/src/scss/primary_variables.scss` (light) and
`primary_variables.dark.scss` (dark). Edit, then upgrade the module.

## Enterprise
Install `resta_sidebar_theme_enterprise` alongside (it auto-installs when `web_enterprise` is present):
it opens the first app instead of the home-menu grid and keeps Resta colors above Enterprise's.

## App icons
48 monochrome line icons in the Resta product-icon style (`static/src/icons/icons.js`),
black in light mode and white in dark mode; the current app's tile inverts.
They replace Odoo's colored icons in the sidebar and in the Settings tabs.
Choose another icon per app in *Settings → Resta Theme → Edit app sections*, or switch
back to the original icons with *App Icon Style*.
