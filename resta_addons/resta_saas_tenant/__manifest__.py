{
    "name": "Resta SaaS - Restaurant Client",
    "summary": "Links a restaurant database to the Resta platform: subscription status, account manager, "
               "updates & messages panel, feedback, sign-in from resta.sa",
    "version": "19.0.1.0.0",
    "category": "Hidden",
    "author": "Basic Solution",
    "website": "https://resta.sa",
    "license": "OPL-1",
    "depends": ["web", "mail", "point_of_sale", "resta_saas_base"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "resta_saas_tenant/static/src/systray/*",
        ],
    },
    "installable": True,
}
