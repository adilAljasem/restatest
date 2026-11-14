# -*- coding: utf-8 -*-
{
    "name": "POS Kitchen Display Sound | صوت شاشة المطبخ",
    "version": "19.0.1.0.0",
    "summary": "تنبيه صوتي عند وصول طلب جديد إلى شاشة المطبخ (Preparation Display)",
    "description": """
يشغّل صوت تنبيه على شاشة المطبخ / شاشة التحضير عند وصول طلب جديد.
- نغمات مدمجة (بدون ملفات) أو ملف صوتي مخصص
- التحكم بمستوى الصوت وعدد التكرار
- تكرار التنبيه حتى يلمس أحد الشاشة (اختياري)
- زر عائم على الشاشة لتفعيل/كتم الصوت
""",
    "category": "Sales/Point of Sale",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": ["point_of_sale"],
    "data": [
        "security/ir.model.access.csv",
        "data/kds_sound_data.xml",
        "views/kds_sound_settings_views.xml",
    ],
    "assets": {
        # Loaded in the backend too, in case the kitchen display runs as a
        # backend client action. The script only activates on a kitchen
        # display page, so it stays silent everywhere else.
        "web.assets_backend": [
            "pos_kds_sound/static/src/kds_sound.js",
        ],
        # Odoo 17/18 Enterprise bundle name. Other kitchen-display bundles
        # are discovered automatically at install (see hooks.py).
        "pos_preparation_display.assets": [
            "pos_kds_sound/static/src/kds_sound.js",
        ],
    },
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
}
