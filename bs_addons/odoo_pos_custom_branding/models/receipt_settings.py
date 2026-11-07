# -*- coding: utf-8 -*-
import base64
import io
import logging

from PIL import Image

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Width of the logo prepared for the receipt printer (print width is 512-576 dots).
RECEIPT_LOGO_WIDTH = 512

DEFAULT_BOTTOM_TEXT = 'غالي وسير على غالي\nشكراً لزيارتكم – نراكم قريباً'


class PosConfig(models.Model):
    """Display options of the Arabic thermal receipt (Puls / RYVK design)."""
    _inherit = 'pos.config'

    receipt_show_reference = fields.Boolean(
        string='Show Invoice Sequence Number', default=True,
        help='Print the sequential invoice reference under the receipt title.')
    receipt_show_order_number = fields.Boolean(
        string='Show Order Number', default=True,
        help='Print the order number in the large box.')
    receipt_show_unit_price = fields.Boolean(
        string='Show Unit Price Column', default=True,
        help='Print the unit price (before VAT) column in the items table.')
    receipt_show_item_english = fields.Boolean(
        string='Show English Item Names', default=True,
        help='Print the English name under each item and English column headers.')
    receipt_show_totals_english = fields.Boolean(
        string='Show English Totals Labels', default=True,
        help='Print English labels under the totals, VAT, payment lines and the title.')
    receipt_show_header_text = fields.Boolean(
        string='Show Header Lines', default=True,
        help='Print the lines above the invoice (company name / header text and '
             'customer service phone). The VAT and CR numbers are always printed.')
    receipt_top_text = fields.Text(
        string='Header Lines',
        help='Lines printed under the logo. Empty: the POS receipt header or the company name.')
    receipt_show_bottom_text = fields.Boolean(
        string='Show Footer Lines', default=True,
        help='Print the additional lines at the bottom of the receipt.')
    receipt_bottom_text = fields.Text(
        string='Footer Lines', default=DEFAULT_BOTTOM_TEXT,
        help='Lines printed at the bottom of the receipt, one per line.')

    receipt_show_customer_phone = fields.Boolean(
        string='Show Customer Phone', default=False,
        help='Print the phone number of the customer under the customer name. '
             'Hidden by default: the customer name is printed without the phone.')

    # ------------------------------------------------------------ Cashiers
    receipt_show_opened_by = fields.Boolean(
        string='Show Cashier Who Opened the Order', default=True,
        help='Print the name of the cashier who created the order, next to the '
             'point of sale.')
    receipt_show_closed_by = fields.Boolean(
        string='Show Cashier Who Closed the Order', default=True,
        help='Print the name of the cashier who took the payment and closed the '
             'order, next to the point of sale.')

    # ----------------------------------------------------------- Delivery
    receipt_show_delivery = fields.Boolean(
        string='Show Delivery Order Details', default=True,
        help='On delivery orders, print a delivery card with the platform name and the '
             'platform (external) order ID. Nothing is printed on dine-in or takeaway orders.')

    # ---------------------------------------------------- Identity pattern
    receipt_show_pattern = fields.Boolean(
        string='Show Identity Pattern', default=True,
        help='Print the decorative identity band on the receipt.')
    receipt_pattern_position = fields.Selection(
        [('both', 'Top and bottom'), ('top', 'Top only'), ('bottom', 'Bottom only')],
        string='Pattern Position', default='both', required=True)
    receipt_pattern_image = fields.Binary(
        string='Custom Pattern Image', attachment=True,
        help='Optional. A wide horizontal band (PNG with a transparent or white '
             'background, dark ink). Empty: the built-in identity pattern.')

    # ------------------------------------------------------------- Printer
    receipt_print_width = fields.Selection(
        [('576', '80 mm – 576 dots (Epson TM-T20III, TM-T82, TM-T88)'),
         ('512', '80 mm – 512 dots (Odoo default)'),
         ('420', '58 mm – 420 dots')],
        string='Printer Print Width', default='576', required=True,
        help='Width of the receipt image sent to the receipt printer. Use the printable '
             'width of the printer: the receipt is then centered with equal margins '
             'and nothing is cut. If the right side is cut off, use 512.')
    receipt_font_size = fields.Selection(
        [('small', 'Small'), ('normal', 'Normal'), ('large', 'Large')],
        string='Print Font Size', default='normal', required=True,
        help='Size of the text on the printed receipt.')
    receipt_print_sharp = fields.Boolean(
        string='Sharp Black Print', default=True,
        help='Convert the receipt image to pure black and white before printing (like '
             'the kitchen ticket): letters print solid black instead of grey dots. '
             'The logo keeps its shades.')

    # ------------------------------------------------------- Receipt logo
    receipt_logo_image = fields.Binary(
        string='Receipt Logo (print)',
        compute='_compute_receipt_logo_image',
        # Stored: prepared once when the company logo changes, instead of
        # being re-processed with PIL every time the POS loads its config.
        store=True,
        attachment=True,
        help='Company logo prepared for thermal printing: grayscale, at most '
             '512px wide. Loaded with the POS so it is printed on the first '
             'receipt without downloading it while printing.')

    @api.depends('company_id.logo')
    def _compute_receipt_logo_image(self):
        for config in self:
            config.receipt_logo_image = self._prepare_receipt_logo(config.company_id.logo)

    @api.model
    def _prepare_receipt_logo(self, logo):
        if not logo:
            return False
        try:
            image = Image.open(io.BytesIO(base64.b64decode(logo)))
            image.load()
            if image.mode in ('RGBA', 'LA', 'P'):
                image = image.convert('RGBA')
                background = Image.new('RGBA', image.size, (255, 255, 255, 255))
                background.alpha_composite(image)
                image = background
            image = image.convert('L')  # thermal printers print black only
            if image.width > RECEIPT_LOGO_WIDTH:
                height = max(1, round(image.height * RECEIPT_LOGO_WIDTH / image.width))
                image = image.resize((RECEIPT_LOGO_WIDTH, height), Image.LANCZOS)
            output = io.BytesIO()
            image.save(output, format='PNG', optimize=True)
            return base64.b64encode(output.getvalue())
        except Exception:  # noqa: BLE001 - an unreadable logo must not block the POS
            _logger.warning('Receipt logo could not be prepared', exc_info=True)
            return False
