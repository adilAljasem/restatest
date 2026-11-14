{
    "name": "Resta SaaS - Restaurant Client",
    "summary": "Links a restaurant database to the Resta platform: subscription status and lock, plan limits, "
               "platform orders (features, levels, apps), no app installation by the restaurant, account manager, "
               "updates & messages panel, feedback, unified sign-in (account number + username + password)",
    "version": "19.0.1.2.0",
    "category": "Hidden",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "OPL-1",
    "depends": ["web", "mail", "auth_signup", "point_of_sale", "resta_saas_base"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "data/platform_data.xml",
        "views/login_templates.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "resta_saas_tenant/static/src/systray/*",
            "resta_saas_tenant/static/src/lock/*",
        ],
    },
    "installable": True,
}
