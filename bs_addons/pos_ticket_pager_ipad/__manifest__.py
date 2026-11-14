{
    "name": "POS Ticket Screen Pager on Tablets",
    "author": "Resta",
    "website": "https://resta.sa",
    "summary": "Show the previous/next page arrows on the POS Orders screen on iPad and smaller screens",
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "license": "LGPL-3",
    "depends": ["point_of_sale"],
    "assets": {
        "point_of_sale._assets_pos": [
            "pos_ticket_pager_ipad/static/src/xml/ticket_screen.xml",
        ],
    },
    "installable": True,
    "application": False,
}
