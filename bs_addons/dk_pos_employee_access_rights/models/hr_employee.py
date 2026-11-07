# -*- coding: utf-8 -*-
from odoo import api, models


class HrEmployeeBase(models.Model):
    _inherit = 'hr.employee'

    @api.model
    def _load_pos_data_fields(self, config_id):
        fields = super()._load_pos_data_fields(config_id)
        return fields + [
            'sbl_hide_pos_new_order_button', 'sbl_hide_pos_delete_order_button',
            'sbl_hide_pos_customer_selection_button', 'sbl_hide_pos_actions_button',
            'sbl_hide_pos_numpad', 'sbl_disable_pos_numpad_plus_minus', 'sbl_disable_pos_qty',
            'sbl_disallow_pos_remove_sent_lines',
            'sbl_disable_pos_discount_button', 'sbl_hide_pos_payment', 'sbl_disable_pos_change_price',
            'sbl_hide_pos_finance_from_product_info', 'sbl_hide_pos_cash_in_out',
            'sbl_hide_pos_create_customer_button', 'sbl_hide_pos_edit_customer_button',
            'sbl_hide_pos_payment_customer_button', 'sbl_hide_pos_payment_invoice_button',
            'sbl_hide_pos_payment_validate_button', 'sbl_hide_pos_payment_ship_later_button',
            'sbl_hide_pos_tip_button', 'sbl_hide_pos_open_cashbox_button',
            'sbl_disabled_payment_method_ids',
            'sbl_hide_pos_install_app', 'sbl_hide_pos_orders_menu',
            'sbl_hide_pos_backend_menu', 'sbl_hide_pos_close_register',
            'sbl_show_pos_closing_register_details',
            'sbl_hide_pos_clear_cache', 'sbl_hide_pos_debug_window',
            'sbl_hide_pos_action_general_note', 'sbl_hide_pos_action_customer_note',
            'sbl_hide_pos_action_pricelist', 'sbl_hide_pos_action_refund',
            'sbl_hide_pos_action_fiscal_position', 'sbl_hide_pos_action_cancel_order',
            'sbl_hide_pos_action_product_info'
        ]

    @api.model
    def _load_pos_data_read(self, records, config):
        """Load the configured employee permissions for POS cashiers.

        Regular POS users read ``hr.employee`` through its public-profile
        fallback.  The custom permission fields are intentionally available
        to the POS client but are not fields of ``hr.employee.public``.  Read
        only the already-domain-filtered employee records in sudo mode so the
        public fallback does not reject those fields.
        """
        return super()._load_pos_data_read(records.sudo(), config)
