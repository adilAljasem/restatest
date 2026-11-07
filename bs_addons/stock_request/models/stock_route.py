# Copyright 2018 ForgeFlow, S.L.
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class StockRoute(models.Model):
    _inherit = "stock.route"

    is_request_stock = fields.Boolean(
        string="Request Stock",
        default=False,
        help="If set, this route is available for selection on Stock Requests "
        "and request lines.",
    )

    def _is_stock_request_route_allowed(self):
        """Only stock-move rules are valid for warehouse transfers."""
        self.ensure_one()
        allowed_actions = {"pull", "push", "pull_push"}
        return all(rule.action in allowed_actions for rule in self.rule_ids)

    @api.constrains("is_request_stock", "rule_ids")
    def _check_stock_request_route_rules(self):
        for route in self.filtered("is_request_stock"):
            if not route._is_stock_request_route_allowed():
                raise ValidationError(
                    _(
                        "A route available to Stock Requests can only contain "
                        "internal transfer rules."
                    )
                )

    @api.constrains("company_id")
    def _check_company_stock_request(self):
        if any(
            rec.company_id
            and self.env["stock.request"].search(
                [("company_id", "!=", rec.company_id.id), ("route_id", "=", rec.id)],
                limit=1,
            )
            for rec in self
        ):
            raise ValidationError(
                _(
                    "You cannot change the company of the route, as it is "
                    "already assigned to stock requests that belong to "
                    "another company."
                )
            )
