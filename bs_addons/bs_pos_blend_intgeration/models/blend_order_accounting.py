"""Opt-in financial acceptance; no generic payment hooks or remote I/O."""

import logging
import math

from markupsafe import Markup

from odoo import _, api, Command, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY
from odoo.tools.misc import format_amount

from .blend_payment_mapping import aggregator_key

_logger = logging.getLogger(__name__)

FINANCIAL_FIELDS = {
    "blend_financial_required", "blend_financial_state", "blend_financial_error",
    "blend_taxes_prepared", "blend_payment_mapping_id", "blend_payment_method_id",
    "blend_financial_completed_at", "blend_financial_attempts", "blend_refund_required",
    "blend_tax_warning",
}
FINANCIAL_ORDER_INPUTS = {
    "blend_payload", "blend_partner_reference", "blend_aggregator",
    "session_id", "config_id", "company_id", "currency_id", "pricelist_id",
    "partner_id", "fiscal_position_id", "lines", "payment_ids", "account_move", "to_invoice",
    "amount_total", "amount_tax", "amount_paid", "amount_return", "amount_difference",
    "state", "date_order", "is_refund", "user_id",
}
FINANCIAL_LINE_INPUTS = {
    "order_id", "product_id", "qty", "price_unit", "discount", "tax_ids",
    "price_subtotal", "price_subtotal_incl", "attribute_value_ids",
    "custom_attribute_value_ids", "combo_parent_id", "combo_line_ids", "combo_item_id",
    "parent_line_id", "modifier_selection_qty",
}


class PosOrder(models.Model):
    _inherit = "pos.order"

    blend_financial_required = fields.Boolean(readonly=True, copy=False)
    blend_financial_state = fields.Selection(
        [("pending", "Awaiting Acceptance"), ("failed", "Accounting Failed"), ("done", "Paid and Invoiced")],
        string="Blend Accounting", readonly=True, copy=False,
    )
    blend_financial_error = fields.Text(string="Blend Accounting Error", readonly=True, copy=False)
    blend_tax_warning = fields.Text(string="Blend Tax Warning", compute="_compute_blend_tax_warning")
    blend_taxes_prepared = fields.Boolean(readonly=True, copy=False)
    blend_payment_mapping_id = fields.Many2one("blend.payment.mapping", readonly=True, copy=False, ondelete="restrict")
    blend_payment_method_id = fields.Many2one("pos.payment.method", string="Blend Payment Method", readonly=True, copy=False, ondelete="restrict")
    blend_financial_completed_at = fields.Datetime(readonly=True, copy=False)
    blend_financial_attempts = fields.Integer(readonly=True, copy=False)
    blend_refund_required = fields.Boolean(string="Blend Cancellation Requires Refund Review", readonly=True, copy=False)
    blend_can_finalize = fields.Boolean(compute="_compute_blend_can_finalize")

    @api.depends("blend_financial_required", "blend_financial_state", "blend_status", "state", "payment_ids", "amount_paid", "account_move", "config_id.blend_enabled")
    def _compute_blend_can_finalize(self):
        for order in self:
            order.blend_can_finalize = bool(
                order.config_id.blend_enabled and order.blend_financial_required
                and order.blend_financial_state != "done" and order.state == "draft"
                and not order.payment_ids and not order.amount_paid and not order.account_move
                and order.blend_status in (
                    "accepted", "preparing", "prepared", "ready_to_pick_up",
                    "assigned", "at_pick_up", "on_the_way", "delivered", "completed",
                )
            )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and (
            any(FINANCIAL_FIELDS.intersection(vals) for vals in vals_list)
            or any("default_" + name in self.env.context for name in FINANCIAL_FIELDS)
        ):
            raise AccessError(_("Blend accounting fields are managed by the integration."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and FINANCIAL_FIELDS.intersection(vals):
            raise AccessError(_("Blend accounting fields are managed by the integration."))
        if (not self.env.su and FINANCIAL_ORDER_INPUTS.intersection(vals)
                and any(self.mapped("blend_financial_required"))):
            raise AccessError(_("The financial Blend order inputs are locked. Use the Blend acceptance or standard refund actions."))
        return super().write(vals)

    @api.model
    def sync_from_ui(self, orders):
        """Refresh server-owned orders without replaying their accounting inputs.

        Restaurant clients can send an old Blend draft while saving an unrelated
        cashier order, even after frontend filtering (explicit sync/offline cache).
        Resolve ownership from accessible DB records, never from client flags.
        Keep the normal ORM locks and native cashier/refund path unchanged.
        """
        managed = self.search([
            ("uuid", "in", [data.get("uuid") for data in orders if data.get("uuid")]),
            ("blend_financial_required", "=", True),
            ("blend_partner_reference", "!=", False),
        ])
        if not managed:
            return super().sync_from_ui(orders)
        by_uuid = {order.uuid: order for order in managed}
        ordinary = []
        for data in orders:
            order = by_uuid.get(data.get("uuid"))
            if order is None:
                ordinary.append(data)
            elif order.state == "draft" and data.get("state", "draft") != "draft":
                # Do not acknowledge a POS payment that was never recorded.
                raise UserError(_(
                    "Complete Blend order %(reference)s using its acceptance action in the order form. "
                    "Cashier payment cannot finalize this order. No payment was recorded.",
                    reference=order.blend_partner_reference,
                ))

        # Do not run the preparation-context hooks on the refreshed Blend orders.
        # They can create tickets or cancel existing kitchen lines on a stale sync.
        result = super().sync_from_ui(ordinary) if ordinary else {}
        for config, records in managed.grouped("config_id").items():
            current = records.read_pos_data([], config)
            for model, rows in current.items():
                merged = {row["id"]: row for row in result.get(model, [])}
                merged.update({row["id"]: row for row in rows})
                result[model] = list(merged.values())
        return result

    def add_payment(self, data):
        if not self.env.su and any(self.mapped("blend_financial_required")):
            raise AccessError(_("Use Accept Order to pay and invoice this Blend order atomically."))
        return super().add_payment(data)

    @api.depends("blend_financial_required", "blend_taxes_prepared", "blend_partner_reference",
                 "blend_payload", "amount_tax", "amount_total", "currency_id")
    @api.depends_context("lang")
    def _compute_blend_tax_warning(self):
        for order in self:
            order.blend_tax_warning = False
            if not (order.blend_financial_required and order.blend_taxes_prepared
                    and order.blend_partner_reference and order.currency_id):
                continue
            try:
                incoming_tax = float((order.blend_payload or {})["tax"])
            except (KeyError, TypeError, ValueError):
                continue
            difference = order.amount_tax - incoming_tax
            if not math.isfinite(incoming_tax) or order.currency_id.is_zero(difference):
                continue
            order.blend_tax_warning = _(
                "Blend tax mismatch (non-blocking).\n"
                "Blend reference: %(reference)s\n"
                "Received tax (Blend payload.tax): %(blend_tax)s\n"
                "Calculated tax (Odoo product taxes and fiscal position): %(odoo_tax)s\n"
                "Difference (Odoo - Blend): %(difference)s\n"
                "Order total: %(total)s\n"
                "Accounting uses Odoo tax and keeps the Blend gross prices. "
                "Review Blend's tax calculation and Odoo tax settings to identify the cause.",
                reference=order.blend_partner_reference,
                blend_tax=format_amount(order.env, incoming_tax, order.currency_id),
                odoo_tax=format_amount(order.env, order.amount_tax, order.currency_id),
                difference=format_amount(order.env, difference, order.currency_id),
                total=format_amount(order.env, order.amount_total, order.currency_id),
            )

    def _blend_assert_financial_totals(self):
        """Gross totals must match; native Odoo tax is authoritative for accounting."""
        self.ensure_one()
        payload = self.blend_payload or {}
        for field, value in (("total", self.amount_total), ("tax", self.amount_tax)):
            try:
                expected = float(payload[field])
            except (KeyError, TypeError, ValueError):
                raise ValidationError(_("The original Blend financial totals are missing.")) from None
            if not math.isfinite(expected):
                raise ValidationError(_("The original Blend financial totals are invalid."))
            if field == "total" and not self.currency_id.is_zero(value - expected):
                raise ValidationError(_(
                    "Blend %(field)s (%(expected)s) differs from Odoo (%(actual)s). Check product taxes and the fiscal position; no payment or invoice was recorded.",
                    field=field, expected=expected, actual=value,
                ))

    def _blend_prepare_financial_taxes(self):
        """Convert already-discounted, tax-inclusive extended totals into native lines.

        Original product taxes remain on the line; the native fiscal-position
        mapping applies exactly once. Never derive a tax rate from payload totals.
        """
        self.ensure_one()
        if not self.blend_financial_required:
            return
        if not self.blend_taxes_prepared:
            if self.state != "draft" or self.payment_ids or self.account_move:
                raise UserError(_("Only an untouched draft Blend order can prepare its financial lines."))
            partner = self.partner_id or self.config_id.blend_invoice_partner_id
            if not partner or not partner.active:
                raise ValidationError(_("A customer or Default Blend Invoice Customer is required."))
            if partner.company_id and partner.company_id != self.company_id:
                raise ValidationError(_("The Blend invoice customer belongs to another company."))
            fpos = self.env["account.fiscal.position"].with_company(self.company_id)._get_fiscal_position(partner)
            fpos = fpos or self.config_id.default_fiscal_position_id
            self.write({"partner_id": partner.id, "fiscal_position_id": fpos.id})
            for line in self.lines.filtered(lambda line: not line.blend_is_discount_line):
                if line.qty <= 0:
                    raise ValidationError(_("Blend sale quantities must be positive."))
                taxes = line.product_id.taxes_id._filter_taxes_by_company(self.company_id)
                mapped = fpos.map_tax(taxes)
                if any(not tax.active for tax in mapped):
                    raise ValidationError(_("An archived tax is configured on a Blend product."))
                gross = line.blend_gross_total
                details = mapped._get_tax_details(
                    gross / line.qty, line.qty, special_mode="total_included",
                    rounding_method="round_globally", product=line.product_id,
                )
                net_unit = details["total_excluded"] / line.qty
                included = mapped._get_tax_details(
                    net_unit, 1.0, special_mode="total_excluded",
                    rounding_method="round_globally", product=line.product_id,
                )
                unit = net_unit + sum(
                    item["tax_amount"] for item in included["taxes_data"] if item["tax"].price_include
                )
                line.write({"tax_ids": [Command.set(taxes.ids)], "price_unit": unit, "discount": 0.0})
                line.write(line._compute_amount_line_all())
                if not self.currency_id.is_zero(line.price_subtotal_incl - gross):
                    raise ValidationError(_("Odoo cannot represent the Blend line total with the configured taxes."))
            self._blend_prepare_discount_taxes()
            self.blend_taxes_prepared = True
        for line in self.lines:
            amounts = line._compute_amount_line_all()
            if line.qty <= 0 or not self.currency_id.is_zero(amounts["price_subtotal_incl"] - line.blend_gross_total):
                raise ValidationError(_("A Blend line no longer matches its original total. Review the order before acceptance."))
            line.write(amounts)
        self._compute_prices()
        self._blend_assert_financial_totals()

    def _blend_record_financial_failure(self, error):
        self.ensure_one()
        message = str(error)[:4000]
        previous = self.blend_financial_error
        self.write({"blend_financial_state": "failed", "blend_financial_error": message})
        if message != previous:
            self.message_post(body=Markup("<p>%s</p><p>%s</p>") % (
                _("Blend accounting did not complete. The order remains pending."), message,
            ))

    def _blend_prepare_imported_accounting(self):
        """A bad tax/customer setup must not discard the received order."""
        self.ensure_one()
        if self.blend_financial_required:
            try:
                with self.env.cr.savepoint():
                    self._blend_prepare_financial_taxes()
            except (UserError, ValidationError) as error:
                self._blend_record_financial_failure(error)

    def _blend_finalize_accounting(self):
        self.ensure_one()
        if not self.blend_financial_required or not self.blend_partner_reference:
            raise UserError(_("This order is not enrolled in Blend financial acceptance."))
        if not self.config_id.blend_finalize_on_accept:
            raise UserError(_("Financial acceptance is disabled on this POS."))
        if self.state != "draft" or self.payment_ids or self.amount_paid or self.account_move:
            raise UserError(_("This order already has payments or accounting entries. Review it manually; no additional payment was created."))
        self.session_id._blend_lock_accounting()
        journal = self.config_id.invoice_journal_id
        if not journal or journal.type != "sale" or journal.company_id != self.company_id:
            raise ValidationError(_("A valid POS invoice journal in the order company is required."))
        mapping = self.env["blend.payment.mapping"].search([
            ("pos_config_id", "=", self.config_id.id),
            ("company_id", "=", self.company_id.id),
            ("aggregator_key", "=", aggregator_key(self.blend_aggregator)),
        ], limit=1)
        if not mapping:
            raise ValidationError(_("Configure a Blend payment mapping for '%s' on this POS.", self.blend_aggregator))
        if error := mapping._configuration_error():
            raise ValidationError(error)
        self._blend_prepare_financial_taxes()
        self.write({
            "to_invoice": True, "blend_payment_mapping_id": mapping.id,
            "blend_payment_method_id": mapping.payment_method_id.id,
        })
        # Use the same wizard as the backend Payment button. This invokes native
        # paid checks, picking, invoice posting and POS-payment reconciliation.
        self.env["pos.make.payment"].with_context(
            active_model="pos.order", active_id=self.id, active_ids=self.ids,
            blend_financial_processing=True, blend_skip_status_sync=True, generate_pdf=False,
        ).create({
            "config_id": self.config_id.id, "amount": self.amount_total,
            "payment_method_id": mapping.payment_method_id.id,
            "payment_name": self.blend_partner_reference,
        }).check()
        invoice = self.account_move
        if self.state != "done" or not invoice or invoice.state != "posted":
            raise ValidationError(_("Odoo did not complete the Blend order invoice."))
        if not self.currency_id.is_zero(self.amount_paid - self.amount_total):
            raise ValidationError(_("The Blend order is not fully paid."))
        if (not self.currency_id.is_zero(invoice.amount_total - self.amount_total)
                or not self.currency_id.is_zero(invoice.amount_tax - self.amount_tax)
                or not self.currency_id.is_zero(invoice.amount_residual)):
            raise ValidationError(_("The Blend invoice totals or payment reconciliation do not match the order."))
        self._blend_assert_financial_totals()
        self.write({
            "blend_financial_state": "done", "blend_financial_error": False,
            "blend_financial_completed_at": fields.Datetime.now(),
        })

    def _blend_accept_with_accounting(self, source, user_id=False):
        """Private, called only after decision access/config checks and a row lock.

        Savepoint encompasses accounting, decision and outbox. Known failures are
        recorded afterwards without raising an RPC exception that would lose them.
        PostgreSQL concurrency errors must reach Odoo's whole-request retry layer.
        """
        self.ensure_one()
        self.blend_financial_attempts += 1
        try:
            with self.env.cr.savepoint():
                order = self.with_context(blend_skip_status_sync=True, blend_financial_processing=True)
                previous_status = order.blend_status
                order._blend_finalize_accounting()
                order.write({
                    "blend_status": "accepted" if previous_status in ("pending", "scheduled") else previous_status,
                    "blend_decision_source": source,
                    "blend_decision_at": fields.Datetime.now(), "blend_decision_user_id": user_id,
                })
                if previous_status not in ("ready_to_pick_up", "at_pick_up", "on_the_way", "completed"):
                    order._blend_send_to_preparation()
                if previous_status in ("pending", "scheduled") and not order.blend_recovered_fulfilled:
                    order._blend_enqueue_status("accepted")
                    order.message_post(body=_("Blend order accepted, paid and invoiced. Acceptance notification queued."))
                else:
                    order.message_post(body=_("Blend order paid and invoiced locally. Its remote status was preserved; no duplicate or older acceptance was sent."))
                order.config_id.notify_synchronisation(order.session_id.id, 0)
            return True
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            raise
        except Exception as error:
            if not isinstance(error, (UserError, ValidationError)):
                _logger.exception("Blend financial acceptance failed for order %s", self.id)
                error = UserError(_("Accounting failed unexpectedly. Review the server log before retrying."))
            self._blend_record_financial_failure(error)
            return False

    def _blend_mark_refund_review(self):
        for order in self.filtered(lambda item: item.blend_financial_required and (item.payment_ids or item.account_move)):
            if not order.blend_refund_required:
                order.blend_refund_required = True
                order.message_post(body=_("Blend reports cancellation/rejection after payment. A manager must review the standard POS refund and credit note. No automatic refund was made."))


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    blend_gross_total = fields.Monetary(readonly=True, copy=False, help="Original Blend extended line total, including taxes and excluding child modifiers.")

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and (
            any("blend_gross_total" in vals for vals in vals_list) or "default_blend_gross_total" in self.env.context
        ):
            raise AccessError(_("Original Blend line totals are managed by the integration."))
        if not self.env.su:
            default_id = self.default_get(["order_id"]).get("order_id") if any("order_id" not in vals for vals in vals_list) else False
            orders = self.env["pos.order"].browse([order_id for vals in vals_list if (order_id := vals.get("order_id", default_id))])
            if any(orders.mapped("blend_financial_required")):
                raise AccessError(_("Lines cannot be added to a financial Blend order."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and "blend_gross_total" in vals:
            raise AccessError(_("Original Blend line totals are managed by the integration."))
        if not self.env.su and FINANCIAL_LINE_INPUTS.intersection(vals):
            orders = self.order_id | self.env["pos.order"].browse(vals.get("order_id", []))
            if any(orders.mapped("blend_financial_required")):
                raise AccessError(_("Financial Blend line inputs cannot be edited."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and any(self.order_id.mapped("blend_financial_required")):
            raise AccessError(_("Financial Blend order lines cannot be deleted."))
        return super().unlink()


class PosPayment(models.Model):
    _inherit = "pos.payment"

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            default_id = self.default_get(["pos_order_id"]).get("pos_order_id") if any("pos_order_id" not in vals for vals in vals_list) else False
            orders = self.env["pos.order"].browse([order_id for vals in vals_list if (order_id := vals.get("pos_order_id", default_id))])
            if any(orders.mapped("blend_financial_required")):
                raise AccessError(_("Use Blend acceptance to create financial Blend payments."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and {"pos_order_id", "amount", "payment_method_id", "payment_date", "is_change", "account_move_id"}.intersection(vals):
            orders = self.pos_order_id | self.env["pos.order"].browse(vals.get("pos_order_id", []))
            if any(orders.mapped("blend_financial_required")):
                raise AccessError(_("Financial Blend payments cannot be edited. Use the standard refund workflow."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and any(self.pos_order_id.mapped("blend_financial_required")):
            raise AccessError(_("Financial Blend payments cannot be deleted. Use the standard refund workflow."))
        return super().unlink()


class PosSession(models.Model):
    _inherit = "pos.session"

    blend_accounting_revision = fields.Integer(readonly=True, copy=False, groups="base.group_system")

    def _blend_lock_accounting(self):
        """Serialize import/acceptance with session closure before creating money.

        The persisted write makes a concurrent closing transaction with an older
        snapshot retry, rather than omitting a newly imported/paid order.
        """
        self.ensure_one()
        self.lock_for_update()
        self.invalidate_recordset(["state", "blend_accounting_revision"])
        if self.state != "opened":
            raise UserError(_("The order's POS session must still be open."))
        self.blend_accounting_revision += 1
        self.flush_recordset(["blend_accounting_revision"])
