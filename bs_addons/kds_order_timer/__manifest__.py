{
    "name": "KDS Order Number & Seconds Timer",
    "version": "19.0.3.0.0",
    "summary": "رقم الطلب (آخر 3 أرقام) بجوار الطاولة، وعداد التحضير بالدقائق والثواني في شاشة المطبخ",
    "category": "Sales/Point of Sale",
    "author": "Custom",
    "license": "LGPL-3",
    "depends": ["pos_enterprise", "pos_restaurant_preparation_display"],
    "assets": {
        "pos_preparation_display.assets": [
            "kds_order_timer/static/src/order_patch.js",
            "kds_order_timer/static/src/order_patch.xml",
            "kds_order_timer/static/src/order_patch.scss",
        ],
    },
    "installable": True,
    "application": False,
}
