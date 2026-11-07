from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


def aggregator_key(value):
    """No fuzzy matching: only case and surrounding whitespace are insignificant."""
    return str(value or "").strip().casefold()


class BlendPaymentMapping(models.Model):
    _name = "blend.payment.mapping"
    _description = "Blend Branch Payment Mapping"
    _rec_name = "aggregator"
    _check_company_auto = True

    active = fields.Boolean(default=True)
    pos_config_id = fields.Many2one("pos.config", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="pos_config_id.company_id", store=True, index=True)
    aggregator = fields.Char(required=True, help="Exact aggregator code received from Blend, e.g. jahez.")
    aggregator_key = fields.Char(compute="_compute_aggregator_key", store=True, precompute=True)
    payment_method_id = fields.Many2one("pos.payment.method", required=True, check_company=True, ondelete="restrict")
    available_payment_method_ids = fields.Many2many(related="pos_config_id.payment_method_ids")

    _branch_aggregator_unique = models.Constraint(
        "unique (pos_config_id, aggregator_key)",
        "A payment mapping already exists for this aggregator and POS. Reuse the archived mapping if necessary.",
    )

    @api.depends("aggregator")
    def _compute_aggregator_key(self):
        for mapping in self:
            mapping.aggregator_key = aggregator_key(mapping.aggregator)

    def _configuration_error(self):
        self.ensure_one()
        method = self.payment_method_id
        if not self.active or not self.aggregator_key:
            return _("The Blend payment mapping is inactive or has no aggregator code.")
        if not method or not method.active:
            return _("The configured Blend payment method is missing or archived.")
        if method.company_id != self.company_id:
            return _("The Blend payment method must belong to the POS company.")
        if method not in self.pos_config_id.payment_method_ids:
            return _("The Blend payment method must be enabled on this POS.")
        if method.type == "pay_later" or method.journal_id.type not in ("cash", "bank"):
            return _("Select a cash or bank POS payment method, not Pay Later.")
        if method.use_payment_terminal or method.payment_method_type != "none" or method._is_online_payment():
            return _("Blend accounting cannot simulate terminal or online payment collection.")
        if method.journal_id.currency_id and method.journal_id.currency_id != self.pos_config_id.currency_id:
            return _("The payment journal currency must match the POS currency.")
        return False

    @api.constrains("active", "aggregator", "payment_method_id", "pos_config_id")
    def _check_configuration(self):
        for mapping in self:
            if not mapping.aggregator_key:
                raise ValidationError(_("An aggregator code is required."))
            if mapping.active and (error := mapping._configuration_error()):
                raise ValidationError(error)


class PosConfig(models.Model):
    _inherit = "pos.config"

    blend_finalize_on_accept = fields.Boolean(
        string="Pay and Invoice on Blend Acceptance", copy=False, groups="base.group_system",
        help="Opt in for newly imported orders only. Local manual/automatic acceptance records a configured POS payment and a posted invoice. It does not collect money from a payment gateway.",
    )
    blend_payment_mapping_ids = fields.One2many(
        "blend.payment.mapping", "pos_config_id", string="Blend Aggregator Payments", copy=False,
    )
    blend_invoice_partner_id = fields.Many2one(
        "res.partner", string="Default Blend Invoice Customer", copy=False, check_company=True,
        groups="base.group_system",
        help="Used only when an incoming financial order has no customer. No customer is invented during acceptance.",
    )

    @api.constrains("blend_finalize_on_accept", "blend_invoice_partner_id", "invoice_journal_id", "company_id")
    def _check_blend_financial_configuration(self):
        for config in self:
            partner = config.blend_invoice_partner_id
            if partner.company_id and partner.company_id != config.company_id:
                raise ValidationError(_("The default Blend invoice customer belongs to another company."))
            if config.blend_finalize_on_accept:
                journal = config.invoice_journal_id
                if not journal or journal.type != "sale" or journal.company_id != config.company_id:
                    raise ValidationError(_("Configure a sales invoice journal in the POS company before enabling Blend invoicing."))
