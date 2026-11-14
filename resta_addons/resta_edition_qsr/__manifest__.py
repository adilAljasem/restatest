{
    "name": "Resta Edition - Fast Food & Cafe",
    "summary": "Pre-configured QSR / cafe: counter service, order-ready board, kitchen & coffee-bar displays, breakfast daypart (Bun Fastic / Puls model)",
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "OPL-1",
    "depends": ["resta_saas_base", "resta_kds"],
    "data": [
        "data/ir_config_parameter_data.xml",
        "data/pos_category_data.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
}
