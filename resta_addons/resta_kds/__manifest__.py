{
    "name": "Resta - شاشة المطبخ وتتبع الطلب",
    "summary": "شاشة مطبخ حية (KDS) وشاشة «طلبك جاهز» للزبائن، على أودو كوميونتي",
    "description": """
Resta Kitchen Display (KDS) - written for Odoo 19 Community, independent of Odoo Enterprise.

* Every order sent from the POS (Send / payment / self-order) becomes a ticket on the kitchen
  screens configured for its point of sale, split by station (POS categories).
* Stages per screen (to prepare -> ready -> done), timers with warning colours, line check-off,
  recall, "all day" totals, Arabic + second-language product names, sound on new tickets.
* Customer "order ready" screen (TV) by public link, and a hook for order-ready integrations.
""",
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "OPL-1",
    "depends": ["point_of_sale", "pos_restaurant", "pos_self_order", "bus"],
    "data": [
        "security/resta_kds_security.xml",
        "security/ir.model.access.csv",
        "views/kds_display_views.xml",
        "views/kds_ticket_views.xml",
        "views/pos_config_views.xml",
        "views/menus.xml",
        "views/tracking_templates.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "resta_kds/static/src/scss/kds.scss",
            "resta_kds/static/src/kds/*",
        ],
        "point_of_sale._assets_pos": [
            "resta_kds/static/src/pos/*",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
