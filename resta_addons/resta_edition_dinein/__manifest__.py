{
    "name": "Resta Edition - Dine-in Restaurant",
    "summary": "Pre-configured dine-in restaurant: family/singles floors, QR table ordering, kitchen & drinks displays (Beno model)",
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "OPL-1",
    "depends": ["resta_saas_base", "pos_self_order", "resta_kds"],
    "data": [
        "data/ir_config_parameter_data.xml",
        "data/pos_category_data.xml",
        "data/restaurant_floor_data.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
}
