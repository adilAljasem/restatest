"""Evidence and reconciliation link for a provider payment allocation."""

from odoo import fields, models


class DeliveryPaymentAllocation(models.Model):
    _name = "bs.delivery.payment.allocation"
    _description = "Delivery Payment Allocation"
    _order = "order_id, id"
    _check_company_auto = True

    order_id = fields.Many2one(
        "bs.delivery.order", required=True, index=True, ondelete="cascade", check_company=True,
    )
    company_id = fields.Many2one(related="order_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="order_id.currency_id", store=True)
    external_source = fields.Char(required=True, index=True)
    external_payment_code = fields.Char(required=True, index=True)
    external_name = fields.Char()
    amount = fields.Monetary(currency_field="currency_id", required=True)
    collected_by = fields.Selection(
        [("restaurant", "Restaurant"), ("aggregator", "Platform"), ("unknown", "Unknown")],
        required=True,
    )
    collection_status = fields.Selection(
        [("expected", "Expected"), ("collected", "Collected")], required=True,
    )
    external_payment_reference = fields.Char(index=True, copy=False)
    payment_method_id = fields.Many2one(
        "pos.payment.method", ondelete="restrict", check_company=True, copy=False,
    )
    pos_payment_id = fields.Many2one(
        "pos.payment", ondelete="set null", check_company=True, copy=False,
    )
    webhook_event_id = fields.Many2one(
        "bs.delivery.webhook.event", ondelete="set null", check_company=True, copy=False,
    )

    _positive_amount = models.Constraint(
        "CHECK(amount > 0)", "A delivery payment allocation must be positive.",
    )
    _reference_unique = models.UniqueIndex(
        "(order_id, external_payment_reference) WHERE external_payment_reference IS NOT NULL",
        "A delivery payment reference can only be used once per order.",
    )
    _pos_payment_unique = models.Constraint(
        "UNIQUE(pos_payment_id)", "A POS payment can only belong to one delivery allocation.",
    )
