# -*- coding: utf-8 -*-
{
    'name': 'Bonat Loyalty (Odoo Online)',
    # Basic Solution (Odoo.sh): a version IS set here, so Odoo.sh upgrades the
    # module automatically on each build where it increases (new field
    # x_bonat_codes, translations). Bump it on every change. Remove this key
    # only if the module must be imported on Odoo Online (see below).
    'version': '19.0.1.1.0',
    # No 'version' key, deliberately.
    #
    # Odoo Online runs a SaaS series: release.major_version is 'saas~19.4', not
    # '19.0'. Odoo 19's Manifest loader calls check_version() against that series
    # and, on a mismatch, silently forces installable=False, which then trips the
    # "Module not installable" assert in base_import_module._import_module. A
    # manifest pinned to '19.0.x.y.z' is therefore rejected outright on Odoo
    # Online. Omitting the key lets Odoo prefix the running series itself.
    #
    # Consequence: this module cannot share a version string with the classic
    # pos_bonat_loyalty. The Apps Store copy (pos-bonat-loyalty-store#19.0, synced
    # by odoo-store-sync) carries no 'version' either — the Store accepts that and
    # prefixes the running series itself. Track the equivalence in CHANGELOG.md.
    'category': 'Sales/Point of Sale',
    'summary': 'Loyalty and Customer Engagement for Odoo Online (SaaS) databases',
    'description': """
Bonat Loyalty for Odoo Online
=============================

Same loyalty and customer-engagement flows as the standard **Bonat Loyalty**
app, repackaged as an importable module so it can be installed on Odoo Online
(SaaS) databases, where third-party Python code cannot be deployed.

Requires an active Bonat subscription. Turn on Demo Mode to try the full POS
flow with no API key.
""",
    'author': 'Bonat',
    'website': 'https://bonat.io',
    'support': 'odoo@bonat.io',
    'depends': ['point_of_sale'],
    'data': [
        'data/bonat_company_fields.xml',
        'data/bonat_pos_config_fields.xml',
        'data/bonat_order_fields.xml',
        'data/bonat_settings_fields.xml',
        # data/bonat_config_parameters.xml was removed deliberately: it shipped
        # ir.config_parameter records for pos_bonat_loyalty.api_url(_staging),
        # keys the on-premise module also creates. On a database that ever ran
        # the classic plugin the keys pre-exist without this module's xml-ids,
        # so the import CREATE hits ir_config_parameter_key_uniq and the whole
        # import aborts (seen live on odoo19.bonat.io, 2026-08-04). The POS
        # client reads these parameters by KEY inside a try/catch and falls
        # back to the company fields then the built-in defaults, so the module
        # must never own the records — an admin can still create them by hand.
        #
        # data/pos_bonat_payment.xml (the seeded Bonat_Voucher pos.payment.method
        # record) was removed for the same reason (BON-2579): the module must
        # never create a payment method for the merchant. The merchant creates
        # their own non-cash method under Point of Sale > Payment Methods and
        # selects it in Bonat Integration settings — see
        # x_bonat_voucher_payment_method_id's help text below.
        'views/bonat_settings_view.xml',
    ],
    # Asset paths are enumerated one by one on purpose.
    #
    # base_import_module._import_module() rejects any asset path containing a
    # glob ('*' or '**'). The on-premise manifest declares
    #   'pos_bonat_loyalty/static/src/**/*'
    # which would be refused here. Every new static file MUST be added to the
    # relevant list below by hand, or it will silently not load.
    'assets': {
        'point_of_sale._assets_pos': [
            # Load order matters: the API client and the config loader must be
            # evaluated before anything that reads pos.bonat / pos.bonatApi.
            'pos_bonat_loyalty_online/static/src/app/bonat_const.js',
            'pos_bonat_loyalty_online/static/src/app/bonat_api.js',
            'pos_bonat_loyalty_online/static/src/app/bonat_runtime.js',
            'pos_bonat_loyalty_online/static/src/app/bonat_codes.js',
            'pos_bonat_loyalty_online/static/src/app/bonat_store.js',
            'pos_bonat_loyalty_online/static/src/app/models.js',
            'pos_bonat_loyalty_online/static/src/app/orderline.js',
            'pos_bonat_loyalty_online/static/src/app/orderline.xml',
            'pos_bonat_loyalty_online/static/src/app/orderline_popup.js',
            'pos_bonat_loyalty_online/static/src/app/orderline_popup.xml',
            'pos_bonat_loyalty_online/static/src/app/orderline_popup.css',
            'pos_bonat_loyalty_online/static/src/app/promo_code_button.js',
            'pos_bonat_loyalty_online/static/src/app/promo_code_button.xml',
            'pos_bonat_loyalty_online/static/src/app/product_screen.js',
            'pos_bonat_loyalty_online/static/src/app/product_screen.xml',
            'pos_bonat_loyalty_online/static/src/app/bonat_validation.js',
            'pos_bonat_loyalty_online/static/src/app/payment_screen.js',
        ],
        'web.assets_backend': [
            'pos_bonat_loyalty_online/static/src/settings/bonat_settings_const.js',
            'pos_bonat_loyalty_online/static/src/settings/bonat_api_key_field.js',
            'pos_bonat_loyalty_online/static/src/settings/bonat_enable_field.js',
            'pos_bonat_loyalty_online/static/src/settings/bonat_test_connection.js',
            'pos_bonat_loyalty_online/static/src/settings/bonat_widgets.xml',
        ],
    },
    # Store-only key: the banner/screenshots/index.html under static/description
    # exist only in this submission repo, not in odoo-modules (they are listing
    # assets, dead weight in the direct-import zip). Restore them after a sync.
    'images': ['static/description/banner.png'],
    'installable': True,
    'license': 'OPL-1',
    # static/description/icon.png is picked up automatically and gives the module
    # a proper tile in the Apps list. The Store artefacts (banner, screenshots,
    # index.html) are deliberately NOT carried over: this build is distributed as a
    # zip, not through the Apps Store, so they would be dead weight in the archive.
    #
    # i18n/ar.po is carried over verbatim from the on-premise module; the msgid
    # ("Use Bonat Coupon") is identical in both builds so the existing Arabic
    # translation applies unchanged.
    #
    # Confirmed on import: base_import_module DOES ingest translations for an
    # importable module, storing the file as an ir.attachment named
    # "pos_bonat_loyalty_online_ar.po". Not confirmed end to end, because Arabic is
    # not activated on the test database, so the string was never rendered in ar.
}
