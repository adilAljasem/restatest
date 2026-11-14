{
    "name": "POS Kitchen Resend | إعادة إرسال للمطبخ",
    "version": "19.0.1.1.0",
    "summary": "Resend paid orders to the kitchen printer / preparation display, marked as RESEND",
    "author": "Resta",
    "website": "https://resta.sa",
    "category": "Sales/Point of Sale",
    "license": "LGPL-3",
    "depends": ["point_of_sale"],
    "data": ["views/pos_order_views.xml"],
    "assets": {
        "point_of_sale._assets_pos": [
            "bs_pos_kitchen_resend/static/src/**/*",
        ],
    },
    "installable": True,
}
