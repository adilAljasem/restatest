{
    "name": "POS Disable Decimal Quantity",
    "summary": "يعطّل زر الفاصلة في نقطة البيع عند إدخال الكمية لمنع البيع المجزأ",
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "depends": ["point_of_sale"],
    "assets": {
        "point_of_sale._assets_pos": [
            "pos_disable_decimal/static/src/js/disable_decimal.js",
        ],
    },
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
