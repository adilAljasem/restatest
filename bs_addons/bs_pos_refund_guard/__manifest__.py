{
    "name": "BS POS Refund Guard",
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "summary": "يمنع استرجاع مبلغ أعلى من الصافي المدفوع عند وجود خصم عام في الطلب الأصلي",
    "author": "Resta",
    "website": "https://resta.sa",
    "depends": ["point_of_sale"],
    "assets": {
        "point_of_sale._assets_pos": [
            "bs_pos_refund_guard/static/src/**/*",
        ],
    },
    "installable": True,
    "license": "LGPL-3",
}
