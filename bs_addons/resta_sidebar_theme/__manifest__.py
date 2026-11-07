{
    "name": "Resta Sidebar Theme",
    "summary": "Foodics-style side navigation with Resta brand colors, light and dark modes",
    "description": """
Replaces Odoo's app-grid navigation with a persistent side menu inspired by
Foodics Console, styled with the Resta brand (resta.sa):
- Collapsible, searchable sidebar grouped into sections, RTL-first
- Clean top bar (breadcrumb title, systray, light/dark toggle)
- Resta palette (#FF5A1F orange, warm neutrals), IBM Plex Sans Arabic bundled locally
- Light and dark modes (dark uses Odoo's own dark asset bundle)
- Logos (light / dark / collapsed mark) configurable from Settings
- Monochrome Resta line icons for every app (black in light, white in dark)
""",
    "version": "19.0.1.5.1",
    "category": "Themes/Backend",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": ["web", "base_setup"],
    "data": [
        "views/ir_ui_menu_views.xml",
        "views/res_config_settings_views.xml",
        "views/webclient_templates.xml",
    ],
    "assets": {
        # Brand variables must win over Odoo's defaults: prepend them.
        "web._assets_primary_variables": [
            ("prepend", "resta_sidebar_theme/static/src/scss/primary_variables.scss"),
        ],
        # Dark values are prepended to the dark bundle so they are read first.
        "web.assets_web_dark": [
            ("prepend", "resta_sidebar_theme/static/src/scss/primary_variables.dark.scss"),
        ],
        "web.assets_backend": [
            "resta_sidebar_theme/static/src/scss/fonts.scss",
            "resta_sidebar_theme/static/src/scss/tokens.scss",
            "resta_sidebar_theme/static/src/scss/layout.scss",
            "resta_sidebar_theme/static/src/scss/navbar.scss",
            "resta_sidebar_theme/static/src/scss/views.scss",
            "resta_sidebar_theme/static/src/scss/apps.scss",
            "resta_sidebar_theme/static/src/js/theme_scheme.js",
            "resta_sidebar_theme/static/src/icons/icons.js",
            "resta_sidebar_theme/static/src/icons/settings_icons.js",
            "resta_sidebar_theme/static/src/sidebar/sidebar.js",
            "resta_sidebar_theme/static/src/sidebar/sidebar.xml",
            "resta_sidebar_theme/static/src/sidebar/sidebar.scss",
            "resta_sidebar_theme/static/src/icons/icons.scss",
            "resta_sidebar_theme/static/src/theme_toggle/theme_toggle.js",
            "resta_sidebar_theme/static/src/theme_toggle/theme_toggle.xml",
            "resta_sidebar_theme/static/src/webclient/webclient_patch.js",
        ],
        "web.assets_frontend": [
            "resta_sidebar_theme/static/src/scss/fonts.scss",
            "resta_sidebar_theme/static/src/scss/login.scss",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
