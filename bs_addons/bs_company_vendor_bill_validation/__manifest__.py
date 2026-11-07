{
    "name": "BS Company and Vendor Bill Validation",
    "summary": "Validate company addresses and vendor bill dates",
    "version": "19.0.1.0.3",
    "category": "Accounting/Localizations",
    "author": "Mohamed Elmalky",
    "license": "LGPL-3",
    "depends": [
        "contacts",
        "account",
        "l10n_sa_edi",
        "l10n_sa_pos",
    ],
    "data": [
        "views/res_partner_views.xml",
        "views/account_move_views.xml",
    ],
    "installable": True,
    "application": False,
}
