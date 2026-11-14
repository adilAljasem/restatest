{
    "name": "Resta - الباركود والمخزون",
    "summary": "Receive, transfer and count stock by scanning (scanner gun, phone camera or typing) on Odoo Community",
    "version": "19.0.1.0.0",
    "category": "Inventory/Inventory",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "OPL-1",
    "depends": ["stock"],
    "data": ["views/views.xml"],
    "assets": {
        "web.assets_backend": [
            "resta_barcode/static/src/*",
        ],
    },
    "installable": True,
    "application": True,
}
