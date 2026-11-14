{
    "name": "POS Ticket Screen - Customer Phone",
    "summary": "Show the customer's phone number under the customer name in the POS orders list, and search orders by phone.",
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": ["point_of_sale"],
    "assets": {
        "point_of_sale._assets_pos": [
            "pos_ticket_partner_phone/static/src/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
