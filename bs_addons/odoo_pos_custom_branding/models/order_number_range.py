# -*- coding: utf-8 -*-
"""Order number of 3 digits, cycling between a start and an end number, per POS.

The order number (pos.order.tracking_number) is shown on the POS screen, the
customer receipt and the kitchen ticket. It is always 3 digits (001 ... 999):

* "Order Number Range" enabled: the number runs from the start number to the
  end number set on the POS (Receipt Settings), then starts again from the
  start number: 001, 002, ... 100, 001 ... It starts again from the start
  number at each new session.
* Disabled: 001 ... 999 then 001 again (Odoo 19 prints the device number in
  front of it: "1005", which is 4 digits).

It follows the order's invoice sequence (pos_reference "2610-1-000123" ->
sequence 123).
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

ORDER_NUMBER_DIGITS = 3
ORDER_NUMBER_MAX = 10 ** ORDER_NUMBER_DIGITS - 1  # 999


def cycle_order_number(sequence, start=1, end=ORDER_NUMBER_MAX):
    """3-digit number of the order at position `sequence` (1, 2, 3...).

    1-100 -> "001" ... "100", then "001" again.
    """
    start, end = int(start), int(end)
    if start < 1 or end > ORDER_NUMBER_MAX or end <= start:
        start, end = 1, ORDER_NUMBER_MAX
    sequence = max(int(sequence or 0), 1)
    size = end - start + 1
    number = start + (sequence - 1) % size
    return str(number).zfill(ORDER_NUMBER_DIGITS)


class PosConfig(models.Model):
    _inherit = 'pos.config'

    order_number_range_enabled = fields.Boolean(
        string='Order Number Range',
        help='Order numbers (3 digits) run from the start number to the end number, '
             'then start again from the start number. The same number is shown on '
             'the POS screen, the customer receipt and the kitchen ticket.')
    order_number_start = fields.Integer(string='Order Number From', default=1)
    order_number_end = fields.Integer(string='Order Number To', default=100)

    @api.constrains('order_number_range_enabled', 'order_number_start', 'order_number_end')
    def _check_order_number_range(self):
        for config in self:
            if not config.order_number_range_enabled:
                continue
            if config.order_number_start < 1:
                raise ValidationError(_('The start order number must be 1 or more.'))
            if config.order_number_end > ORDER_NUMBER_MAX:
                raise ValidationError(
                    _('The order number has 3 digits: the end order number cannot be more than 999.'))
            if config.order_number_end <= config.order_number_start:
                raise ValidationError(
                    _('The end order number must be greater than the start order number.'))

    def _order_number_bounds(self):
        self.ensure_one()
        if self.order_number_range_enabled:
            return self.order_number_start, self.order_number_end
        return 1, ORDER_NUMBER_MAX

    def _get_next_order_refs(self, device_identifier='0'):
        # Orders created on the server (delivery platforms, self ordering...).
        reference, tracking_number = super()._get_next_order_refs(device_identifier)
        try:
            sequence = int(reference.rsplit('-', 1)[-1] or 0)
        except ValueError:
            return reference, tracking_number
        position = sequence
        session = self.current_session_id
        if session and self.order_number_range_enabled:
            # Numbering starts again from the start number at each session.
            if not session.order_number_backend_base:
                session.sudo().order_number_backend_base = sequence
            base = session.order_number_backend_base
            position = sequence - base + 1 if sequence >= base else 1
        start, end = self._order_number_bounds()
        return reference, cycle_order_number(position, start, end)


class PosSession(models.Model):
    _inherit = 'pos.session'

    order_number_backend_base = fields.Integer(
        string='First Server Order Sequence', readonly=True, copy=False,
        help='Invoice sequence of the first order created on the server in this '
             'session, used to start the order numbers again at each session.')
