{
    "name": "Resta Sidebar Theme - Enterprise Bridge",
    "summary": "Makes the Resta sidebar theme take precedence over Odoo Enterprise's home menu and colors",
    "version": "19.0.1.5.1",
    "category": "Hidden",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": ["resta_sidebar_theme", "web_enterprise"],
    "auto_install": True,
    "assets": {
        # Keep Resta variables ahead of web_enterprise's own. "before" is a no-op when the
        # base theme already prepended them; unlike remove+prepend it stays correct when
        # the primary variables are included twice in one bundle (website editor does).
        "web._assets_primary_variables": [
            ("before", "web_enterprise/static/src/scss/primary_variables.scss",
             "resta_sidebar_theme/static/src/scss/primary_variables.scss"),
        ],
        "web.assets_web_dark": [
            ("before", "web_enterprise/static/src/scss/primary_variables.dark.scss",
             "resta_sidebar_theme/static/src/scss/primary_variables.dark.scss"),
        ],
        "web.assets_backend": [
            "resta_sidebar_theme_enterprise/static/src/webclient_enterprise.js",
            "resta_sidebar_theme_enterprise/static/src/enterprise.scss",
        ],
    },
    "installable": True,
}
