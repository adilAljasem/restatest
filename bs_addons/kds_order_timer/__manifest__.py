{
    "name": "KDS Order Number & Seconds Timer",
    "version": "19.0.4.0.0",
    "summary": "رقم الطلب (آخر 3 أرقام) وعداد التحضير بالساعات والدقائق والثواني في شاشة مطبخ ريستا",
    "description": """
على شاشة مطبخ ريستا (resta_kds، تعمل على Community):
- رقم الطلب المختصر (آخر 3 أرقام) بدل الرقم الكامل، ليسهل نداؤه
- العداد يعرض الساعات بعد مرور ساعة (1:05:09) بدل 65:09
""",
    "category": "Sales/Point of Sale",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": ["resta_kds"],
    "assets": {
        "web.assets_backend": [
            "kds_order_timer/static/src/order_patch.js",
            "kds_order_timer/static/src/order_patch.xml",
            "kds_order_timer/static/src/order_patch.scss",
        ],
    },
    "installable": True,
    "application": False,
}
