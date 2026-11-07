"""Explicit connection and branch payment methods for end aggregators."""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


def aggregator_key(value):
    return str(value or "").strip().casefold()


class DeliveryPosPaymentMapping(models.Model):
    _name = "bs.delivery.pos.payment.mapping"
    _description = "POS Aggregator Payment Mapping"
    _rec_name = "aggregator_name"
    _check_company_auto = True

    connection_id = fields.Many2one(
        "bs.delivery.connection", required=True, ondelete="cascade", index=True,
        check_company=True,
    )
    pos_config_id = fields.Many2one(
        "pos.config", required=True, ondelete="cascade", index=True, check_company=True,
    )
    company_id = fields.Many2one(related="connection_id.company_id", store=True, index=True)
    aggregator_name = fields.Char(required=True)
    aggregator_key = fields.Char(compute="_compute_aggregator_key", store=True, precompute=True)
    payment_type = fields.Selection(
        [("cash", "Cash"), ("credit_visa", "Credit/Visa"), ("wallet", "Wallet")],
        required=True,
    )
    payment_method_id = fields.Many2one(
        "pos.payment.method", required=True, ondelete="restrict", check_company=True,
    )
    available_payment_method_ids = fields.Many2many(related="pos_config_id.payment_method_ids")

    _unique_connection_branch_payment = models.Constraint(
        "unique (connection_id, pos_config_id, aggregator_key, payment_type)",
        "Configure each aggregator and payment type only once per connection and POS branch.",
    )

    @api.depends("aggregator_name")
    def _compute_aggregator_key(self):
        for mapping in self:
            mapping.aggregator_key = aggregator_key(mapping.aggregator_name)

    @api.constrains(
        "aggregator_name", "connection_id", "pos_config_id", "payment_type", "payment_method_id",
    )
    def _check_configuration(self):
        for mapping in self:
            method = mapping.payment_method_id
            config = mapping.pos_config_id
            connection = mapping.connection_id
            if not mapping.aggregator_key:
                raise ValidationError(_("Aggregator name is required."))
            if config.company_id != connection.company_id or config not in connection.pos_config_ids:
                raise ValidationError(_("Choose a POS branch enabled on this delivery connection."))
            if (not method.active or method.company_id != config.company_id
                    or method not in config.payment_method_ids):
                raise ValidationError(_("Choose an active payment method enabled on this POS branch."))
            if method.type == "pay_later" or method.journal_id.type not in ("cash", "bank"):
                raise ValidationError(_("Choose a cash or bank POS payment method."))
            if method.use_payment_terminal or method.payment_method_type != "none" or method._is_online_payment():
                raise ValidationError(_("Choose a POS payment method without terminal or online collection."))
            if method.journal_id.currency_id and method.journal_id.currency_id != config.currency_id:
                raise ValidationError(_("The payment journal currency must match the POS currency."))
