# -*- coding: utf-8 -*-
from odoo import fields, models


class HrEmployeePublic(models.Model):
    _inherit = "hr.employee.public"

    # Odoo 19 falls back to hr.employee.public when a POS cashier does not
    # have HR access.  These non-sensitive flags must exist on the public
    # model as well; otherwise relational prefetches on pos.config fail before
    # the POS data loader can return the current cashier's permissions.
    sbl_hide_pos_new_order_button = fields.Boolean(readonly=True)
    sbl_hide_pos_delete_order_button = fields.Boolean(readonly=True)
    sbl_hide_pos_customer_selection_button = fields.Boolean(readonly=True)
    sbl_hide_pos_actions_button = fields.Boolean(readonly=True)
    sbl_hide_pos_numpad = fields.Boolean(readonly=True)
    sbl_disable_pos_numpad_plus_minus = fields.Boolean(readonly=True)
    sbl_disable_pos_qty = fields.Boolean(readonly=True)
    sbl_disallow_pos_remove_sent_lines = fields.Boolean(readonly=True)
    sbl_disable_pos_discount_button = fields.Boolean(readonly=True)
    sbl_hide_pos_payment = fields.Boolean(readonly=True)
    sbl_disable_pos_change_price = fields.Boolean(readonly=True)
    sbl_hide_pos_finance_from_product_info = fields.Boolean(readonly=True)
    sbl_hide_pos_cash_in_out = fields.Boolean(readonly=True)
    sbl_hide_pos_create_customer_button = fields.Boolean(readonly=True)
    sbl_hide_pos_edit_customer_button = fields.Boolean(readonly=True)
    sbl_hide_pos_payment_customer_button = fields.Boolean(readonly=True)
    sbl_hide_pos_payment_invoice_button = fields.Boolean(readonly=True)
    sbl_hide_pos_payment_validate_button = fields.Boolean(readonly=True)
    sbl_hide_pos_payment_ship_later_button = fields.Boolean(readonly=True)
    sbl_hide_pos_tip_button = fields.Boolean(readonly=True)
    sbl_hide_pos_open_cashbox_button = fields.Boolean(readonly=True)
    sbl_disabled_payment_method_ids = fields.Many2many(
        related="employee_id.sbl_disabled_payment_method_ids",
        readonly=True,
    )
    sbl_hide_pos_install_app = fields.Boolean(readonly=True)
    sbl_hide_pos_orders_menu = fields.Boolean(readonly=True)
    sbl_hide_pos_backend_menu = fields.Boolean(readonly=True)
    sbl_hide_pos_close_register = fields.Boolean(readonly=True)
    sbl_show_pos_closing_register_details = fields.Boolean(readonly=True)
    sbl_hide_pos_clear_cache = fields.Boolean(readonly=True)
    sbl_hide_pos_debug_window = fields.Boolean(readonly=True)
    sbl_hide_pos_action_general_note = fields.Boolean(readonly=True)
    sbl_hide_pos_action_customer_note = fields.Boolean(readonly=True)
    sbl_hide_pos_action_pricelist = fields.Boolean(readonly=True)
    sbl_hide_pos_action_refund = fields.Boolean(readonly=True)
    sbl_hide_pos_action_fiscal_position = fields.Boolean(readonly=True)
    sbl_hide_pos_action_cancel_order = fields.Boolean(readonly=True)
    sbl_hide_pos_action_product_info = fields.Boolean(readonly=True)
