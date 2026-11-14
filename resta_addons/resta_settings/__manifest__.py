{
    "name": "Resta - الإعدادات المبسطة",
    "summary": "Settings in the Resta style: a settings centre with a setup checklist, and a simpler, "
               "restaurant-only settings page",
    "description": """
- مركز الإعدادات: قائمة تجهيز المطعم خطوة بخطوة، وبطاقات لكل قسم بنفس شكل الثيم الجانبي ورحلة النمو
- صفحة الإعدادات: ترتيب الأقسام مثل القائمة الجانبية، وإخفاء ما لا يخص المطاعم والميزات غير المتاحة
- الإعدادات المتقدمة تبقى متاحة لفريق الدعم (وضع المطور)
""",
    "version": "19.0.1.0.0",
    "category": "Hidden",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "OPL-1",
    "depends": ["base_setup", "resta_sidebar_theme"],
    "data": [
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "resta_settings/static/src/scss/settings.scss",
            "resta_settings/static/src/home/*",
            "resta_settings/static/src/settings_page/*",
        ],
    },
    "installable": True,
    "application": False,
}
