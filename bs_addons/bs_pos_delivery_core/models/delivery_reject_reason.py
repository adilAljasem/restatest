import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class DeliveryRejectReason(models.Model):
    _name = "bs.delivery.reject.reason"
    _description = "POS Delivery Rejection Reason"
    _order = "sequence, name, id"
    _check_company_auto = True

    _CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", index=True, ondelete="cascade",
        help="Leave empty to make the reason available to every company.",
    )
    provider_id = fields.Many2one(
        "bs.delivery.provider", index=True, ondelete="cascade",
        help="Leave empty for a provider-neutral rejection reason.",
    )
    external_code = fields.Char(
        string="Provider Reason Code",
        help="Optional code sent by the provider adapter.",
    )
    requires_note = fields.Boolean(
        help="Require an operator note whenever this reason is selected.",
    )

    _code_unique = models.Constraint(
        "UNIQUE(code)", "A delivery rejection reason code must be unique.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        return super().create(
            [self._normalize_reason_values(values) for values in vals_list]
        )

    def write(self, vals):
        return super().write(self._normalize_reason_values(vals))

    @api.model
    def _normalize_reason_values(self, values):
        values = dict(values)
        if "code" in values:
            values["code"] = str(values["code"] or "").strip().lower()
        if "external_code" in values:
            values["external_code"] = (
                str(values["external_code"] or "").strip() or False
            )
        return values

    @api.constrains("code")
    def _check_reason_code(self):
        for reason in self:
            if not self._CODE_PATTERN.fullmatch(reason.code or ""):
                raise ValidationError(
                    _(
                        "Rejection reason codes must start with a lowercase letter and "
                        "contain only lowercase letters, numbers, and underscores."
                    )
                )
