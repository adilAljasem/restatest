from odoo import fields, models


class PosConfig(models.Model):
    """Kitchen ticket options (Point of Sale > Configuration > Kitchen Ticket)."""
    _inherit = 'pos.config'

    kt_enabled = fields.Boolean(
        string='Use Kitchen Ticket Design', default=True,
        help='Print kitchen / preparation tickets with this design. '
             'Off: the standard Odoo kitchen ticket.')

    # Header
    kt_show_reference = fields.Boolean(string='Order Reference', default=True,
                                       help='Order reference at the top (e.g. 238469).')
    kt_show_order_number = fields.Boolean(string='Order Number', default=True,
                                          help='"Order# 39" (order tracking number).')
    kt_header_text = fields.Char(string='Header Text',
                                 help='Optional line under the order number, e.g. the brand name.')
    kt_show_printed_at = fields.Boolean(string='Printed At', default=True)

    # Details
    kt_show_type = fields.Boolean(string='Order Type', default=True)
    kt_show_guests = fields.Boolean(string='Guests', default=True)
    kt_show_device = fields.Boolean(string='Device (POS Name)', default=True)
    kt_show_date = fields.Boolean(string='Order Date', default=True)
    kt_show_time = fields.Boolean(string='Order Time', default=True)
    kt_show_employee = fields.Boolean(string='Employee', default=False)
    kt_show_table = fields.Boolean(string='Table', default=True)
    kt_show_platform = fields.Boolean(
        string='Delivery Platform', default=True,
        help='Delivery app and its order number (Connect / FeedUs), e.g. "Jahez" and "208307412".')

    # Items
    kt_name_language = fields.Selection(
        [('both', 'Arabic and English'), ('ar', 'Arabic only'), ('en', 'English only')],
        string='Item Names', default='both', required=True)
    kt_show_attributes = fields.Boolean(string='Options (Attributes)', default=True)
    kt_show_combo_items = fields.Boolean(string='Combo Items', default=True)
    kt_show_modifiers = fields.Boolean(
        string='Modifiers', default=True,
        help='Modifiers (extras / instructions) of the item, printed under it. '
             'Works with product_modifiers and pos_restaurant_combo_modifiers.')
    kt_show_notes = fields.Boolean(string='Notes', default=True)
    kt_line_separators = fields.Boolean(string='Line Separators', default=True)
    kt_title_language = fields.Selection(
        [('en', 'English (New)'), ('ar', 'Arabic (جديد)'), ('both', 'Both (جديد / New)')],
        string='Title Language', default='en', required=True,
        help='Language of "New", "Cancelled", "Note Update"...')
    kt_font_size = fields.Selection(
        [('small', 'Small'), ('normal', 'Normal'), ('large', 'Large')],
        string='Font Size', default='normal', required=True)
    kt_print_width = fields.Selection(
        [('576', '80 mm – 576 dots (Epson TM-T20III, TM-T82, TM-T88)'),
         ('512', '512 dots (Odoo default)'),
         ('420', '58 mm – 420 dots')],
        string='Printer Width', default='576', required=True,
        help='Width of the ticket image sent to the kitchen printer. Use the printer '
             'printable width so the ticket is centered and nothing is cut.')
