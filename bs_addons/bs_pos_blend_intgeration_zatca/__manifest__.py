{
    "name": "POS Blend Integration - ZATCA Total Guard",
    "version": "19.0.1.0.0",
    "category": "Accounting/Localizations/EDI",
    "summary": "Hold ZATCA submission while incoming Blend order totals do not match",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": ["bs_pos_blend_intgeration", "l10n_sa_edi"],
    "data": ["views/pos_order_views.xml"],
    "auto_install": True,
    "installable": True,
}
