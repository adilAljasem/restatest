{
    "name": "Resta - رحلة النمو",
    "summary": "رحلة نمو المطعم: خمسة مستويات، مؤشر الانضباط، فتح الميزات، الترحيب، الجولات الإرشادية",
    "description": """
رحلة النمو (Growth Journey) لكل مطعم على منصة ريستا:
- خمسة مستويات تُفتح مع كل منها ميزة (المخزون، المحاسبة، ...)
- مؤشر الانضباط 0-1000 يُحسب ليلاً من بيانات المطعم الفعلية، مع لقطة يومية غير قابلة للتعديل
- شارة المستوى أعلى الشاشة لصاحب الحساب، صفحة «رحلتي»، معالج الترحيب، احتفال الترقية
- جولات إرشادية داخل المتصفح، ومحاكي لفريق الدعم والعروض
""",
    "version": "19.0.1.0.0",
    "category": "Productivity",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "OPL-1",
    "depends": [
        "resta_saas_tenant",
        "point_of_sale",
        "stock_account",
        "account",
        "web_tour",
        "bus",
        "resta_settings",
    ],
    "data": [
        "security/resta_growth_security.xml",
        "security/ir.model.access.csv",
        "data/restaurant_type_data.xml",
        "data/level_data.xml",
        "data/feature_data.xml",
        "data/lesson_data.xml",
        "data/condition_data.xml",
        "data/ir_cron_data.xml",
        "views/growth_profile_views.xml",
        "views/growth_config_views.xml",
        "views/growth_score_views.xml",
        "views/res_config_settings_views.xml",
        "views/menus.xml",
        "data/settings_menu_access.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "resta_growth/static/src/scss/*.scss",
            "resta_growth/static/src/growth_service.js",
            "resta_growth/static/src/systray/*",
            "resta_growth/static/src/journey/*",
            "resta_growth/static/src/welcome/*",
            "resta_growth/static/src/celebration/*",
            "resta_growth/static/src/locked/*",
            "resta_growth/static/src/tours/*",
        ],
        "web.assets_tests": [
            "resta_growth/static/tests/tours/*",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
