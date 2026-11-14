{
    "name": "Resta - الهوية",
    "summary": "Resta brand everywhere Odoo shows its own: page titles, login, PWA, dialogs, user menu, "
               "emails, portal and website footers, the system bot",
    "description": """
يستبدل اسم وشعار أودو في الواجهة بهوية ريستا (resta.sa):
عنوان الصفحات، صفحة الدخول، تطبيق الجوال (PWA)، عناوين النوافذ، قائمة المستخدم،
تذييل الإيميلات والبوابة والموقع، وحساب البوت. إشعارات الترخيص داخل ملفات أودو المصدرية لا تُمس.
""",
    "version": "19.0.1.0.0",
    "category": "Hidden",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": ["web", "mail", "portal", "auth_signup", "base_setup", "resta_sidebar_theme"],
    "data": [
        "views/brand_templates.xml",
        "views/auth_templates.xml",
        "data/brand_data.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "resta_brand/static/src/js/brand.js",
            "resta_brand/static/src/js/brand_backend.js",
            "resta_brand/static/src/xml/brand.xml",
        ],
        "web.assets_frontend": [
            "resta_brand/static/src/scss/auth.scss",
            "resta_brand/static/src/js/brand.js",
            "resta_brand/static/src/xml/brand.xml",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "auto_install": False,
}
