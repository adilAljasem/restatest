from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import str2bool

from .res_config_settings import RECURRING_PURCHASE_ORDER_PARAMETER


class PurchaseRequisition(models.Model):
    _inherit = "purchase.requisition"

    bs_recurring_purchase_order_feature_enabled = fields.Boolean(
        string="Recurring Purchase Order Feature Enabled",
        compute="_compute_bs_recurring_purchase_order_feature_enabled",
    )
    bs_recurring_purchase_order = fields.Boolean(
        string="Recurring Purchase Order",
        copy=False,
    )
    bs_recurring_plan = fields.Selection(
        selection=[
            ("daily", "Daily"),
            ("weekly", "Weekly"),
            ("monthly", "Monthly"),
        ],
        string="Recurring Plan",
        copy=False,
    )
    bs_next_recurring_date = fields.Date(
        string="Next Recurring Date",
        copy=False,
        readonly=True,
    )

    _BS_RECURRING_DELTAS = {
        "daily": relativedelta(days=1),
        "weekly": relativedelta(weeks=1),
        "monthly": relativedelta(months=1),
    }

    @api.model
    def _bs_recurring_purchase_order_is_enabled(self):
        return str2bool(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(RECURRING_PURCHASE_ORDER_PARAMETER, "False")
        )

    def _compute_bs_recurring_purchase_order_feature_enabled(self):
        enabled = self._bs_recurring_purchase_order_is_enabled()
        for requisition in self:
            requisition.bs_recurring_purchase_order_feature_enabled = enabled

    @api.onchange("requisition_type")
    def _onchange_bs_recurring_requisition_type(self):
        for requisition in self.filtered(
            lambda record: record.requisition_type != "blanket_order"
        ):
            requisition.bs_recurring_purchase_order = False
            requisition.bs_recurring_plan = False

    @api.constrains(
        "bs_recurring_purchase_order",
        "bs_recurring_plan",
        "requisition_type",
        "date_start",
        "date_end",
        "vendor_id",
    )
    def _check_bs_recurring_configuration(self):
        for requisition in self.filtered("bs_recurring_purchase_order"):
            if requisition.requisition_type != "blanket_order":
                raise ValidationError(
                    _("Recurring Purchase Order is only available for Blanket Orders.")
                )
            if not requisition.bs_recurring_plan:
                raise ValidationError(
                    _("Select a Recurring Plan for the recurring Blanket Order.")
                )
            if not requisition.vendor_id:
                raise ValidationError(
                    _("Select a vendor for the recurring Blanket Order.")
                )
            if not requisition.date_start or not requisition.date_end:
                raise ValidationError(
                    _(
                        "Set both Start Date and End Date for the recurring "
                        "Blanket Order."
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        requisitions = super().create(vals_list)
        for requisition in requisitions.filtered("bs_recurring_purchase_order"):
            requisition.bs_next_recurring_date = requisition.date_start
        return requisitions

    def write(self, values):
        reset_schedule = bool(
            {"date_start", "bs_recurring_plan"}.intersection(values)
        )
        result = super().write(values)
        for requisition in self.filtered("bs_recurring_purchase_order"):
            should_initialize = (
                values.get("bs_recurring_purchase_order")
                and not requisition.bs_next_recurring_date
            )
            if reset_schedule or should_initialize:
                requisition.bs_next_recurring_date = requisition.date_start
        return result

    def _bs_get_next_recurring_date(self, schedule_date):
        self.ensure_one()
        return schedule_date + self._BS_RECURRING_DELTAS[self.bs_recurring_plan]

    def _bs_prepare_recurring_purchase_order_values(self, schedule_date):
        self.ensure_one()
        company = self.company_id
        partner = self.vendor_id.with_company(company)
        fiscal_position = (
            self.env["account.fiscal.position"]
            .with_company(company)
            ._get_fiscal_position(partner)
        )
        order_lines = []
        for line in self.line_ids:
            product = line.product_id.with_context(
                lang=partner.lang or self.env.user.lang,
                partner_id=partner.id,
            )
            name = product.display_name
            if product.description_purchase:
                name += "\n" + product.description_purchase
            taxes = fiscal_position.map_tax(
                line.product_id.supplier_taxes_id.filtered(
                    lambda tax: tax.company_id in company.parent_ids
                )
            )
            order_lines.append(
                Command.create(
                    line._prepare_purchase_order_line(
                        name=name,
                        product_qty=line.bs_recurring_qty,
                        price_unit=line.price_unit,
                        taxes_ids=taxes.ids,
                    )
                )
            )

        return {
            "partner_id": partner.id,
            "requisition_id": self.id,
            "company_id": company.id,
            "currency_id": self.currency_id.id,
            "user_id": self.user_id.id,
            "payment_term_id": partner.property_supplier_payment_term_id.id,
            "fiscal_position_id": fiscal_position.id,
            "picking_type_id": self.picking_type_id.id,
            "date_order": fields.Datetime.now(),
            "origin": self.name,
            "note": self.description,
            "bs_recurring_schedule_date": schedule_date,
            "order_line": order_lines,
        }

    def _bs_create_recurring_purchase_order(self, schedule_date):
        self.ensure_one()
        values = self._bs_prepare_recurring_purchase_order_values(schedule_date)
        purchase_order = (
            self.env["purchase.order"].with_company(self.company_id).create(values)
        )
        purchase_order.button_confirm()
        if purchase_order.state != "purchase":
            raise UserError(
                _(
                    "The recurring Purchase Order %(order)s could not be confirmed.",
                    order=purchase_order.display_name,
                )
            )
        return purchase_order

    def _bs_generate_due_recurring_purchase_orders(self, today=None):
        self.ensure_one()
        purchase_orders = self.env["purchase.order"]
        if (
            not self._bs_recurring_purchase_order_is_enabled()
            or not self.active
            or not self.bs_recurring_purchase_order
            or self.requisition_type != "blanket_order"
            or self.state != "confirmed"
            or not self.date_start
            or not self.date_end
        ):
            return purchase_orders

        today = fields.Date.to_date(today) if today else fields.Date.context_today(self)
        last_due_date = min(today, self.date_end)
        schedule_date = self.bs_next_recurring_date or self.date_start
        if schedule_date < self.date_start:
            schedule_date = self.date_start

        while schedule_date <= last_due_date:
            existing_order = self.env["purchase.order"].search(
                [
                    ("requisition_id", "=", self.id),
                    ("bs_recurring_schedule_date", "=", schedule_date),
                ],
                limit=1,
            )
            if not existing_order:
                purchase_orders |= self._bs_create_recurring_purchase_order(
                    schedule_date
                )
            schedule_date = self._bs_get_next_recurring_date(schedule_date)

        if self.bs_next_recurring_date != schedule_date:
            self.bs_next_recurring_date = schedule_date
        return purchase_orders

    @api.model
    def _cron_bs_create_recurring_purchase_orders(self, today=None):
        if not self._bs_recurring_purchase_order_is_enabled():
            return True

        today = fields.Date.to_date(today) if today else fields.Date.context_today(self)
        requisitions = self.sudo().search(
            [
                ("active", "=", True),
                ("state", "=", "confirmed"),
                ("requisition_type", "=", "blanket_order"),
                ("bs_recurring_purchase_order", "=", True),
                ("date_start", "<=", today),
                ("date_end", ">=", today),
                "|",
                ("bs_next_recurring_date", "=", False),
                ("bs_next_recurring_date", "<=", today),
            ]
        )
        for requisition in requisitions:
            requisition._bs_generate_due_recurring_purchase_orders(today=today)
        return True


class PurchaseRequisitionLine(models.Model):
    _inherit = "purchase.requisition.line"

    bs_recurring_qty = fields.Float(
        string="Recurring Qty",
        digits="Product Unit",
    )
