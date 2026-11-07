import hashlib
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


_logger = logging.getLogger(__name__)


class DeliveryCategoryBinding(models.Model):
    _name = "bs.delivery.category.binding"
    _description = "Delivery Category Mapping"
    _order = "connection_id, external_category_id, id"
    _check_company_auto = True

    connection_id = fields.Many2one(
        "bs.delivery.connection", required=True, index=True, ondelete="cascade", check_company=True
    )
    store_binding_id = fields.Many2one(
        "bs.delivery.store.binding", index=True, ondelete="cascade", check_company=True
    )
    company_id = fields.Many2one(related="connection_id.company_id", store=True, index=True)
    provider_id = fields.Many2one(related="connection_id.provider_id", store=True, index=True)
    external_category_id = fields.Char(required=True, index=True)
    external_parent_id = fields.Char(index=True)
    external_name = fields.Char()
    external_name_en = fields.Char(string="Platform Name (English)")
    external_name_ar = fields.Char(string="Platform Name (Arabic)")
    pos_category_id = fields.Many2one("pos.category", ondelete="restrict")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    payload_hash = fields.Char(index=True, copy=False)
    last_sync_date = fields.Datetime(copy=False)
    last_sync_error = fields.Text(copy=False)

    _external_category_global_unique = models.UniqueIndex(
        "(connection_id, external_category_id) WHERE store_binding_id IS NULL",
        "A global external category mapping can only exist once per delivery connection.",
    )
    _external_category_store_unique = models.UniqueIndex(
        "(connection_id, store_binding_id, external_category_id) "
        "WHERE store_binding_id IS NOT NULL",
        "An external category can only be mapped once per delivery store.",
    )

    @api.constrains("store_binding_id", "connection_id")
    def _check_store_connection(self):
        for mapping in self:
            if mapping.store_binding_id and mapping.store_binding_id.connection_id != mapping.connection_id:
                raise ValidationError(_("The category mapping store belongs to another connection."))


class DeliveryModifierBinding(models.Model):
    _name = "bs.delivery.modifier.binding"
    _description = "Delivery Modifier Mapping"
    _order = "connection_id, external_modifier_id, id"
    _check_company_auto = True

    connection_id = fields.Many2one(
        "bs.delivery.connection", required=True, index=True, ondelete="cascade", check_company=True
    )
    store_binding_id = fields.Many2one(
        "bs.delivery.store.binding", index=True, ondelete="cascade", check_company=True
    )
    company_id = fields.Many2one(related="connection_id.company_id", store=True, index=True)
    provider_id = fields.Many2one(related="connection_id.provider_id", store=True, index=True)
    external_modifier_id = fields.Char(required=True, index=True)
    external_group_id = fields.Char(index=True)
    external_name = fields.Char()
    external_name_en = fields.Char(string="Platform Name (English)")
    external_name_ar = fields.Char(string="Platform Name (Arabic)")
    product_id = fields.Many2one(
        "product.product",
        required=True,
        ondelete="restrict",
        check_company=True,
    )
    platform_price = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", store=True, readonly=True
    )
    active = fields.Boolean(default=True)
    payload_hash = fields.Char(index=True, copy=False)
    last_sync_date = fields.Datetime(copy=False)
    last_sync_error = fields.Text(copy=False)

    _external_modifier_global_unique = models.UniqueIndex(
        "(connection_id, external_modifier_id) WHERE store_binding_id IS NULL",
        "A global external modifier mapping can only exist once per delivery connection.",
    )
    _external_modifier_store_unique = models.UniqueIndex(
        "(connection_id, store_binding_id, external_modifier_id) "
        "WHERE store_binding_id IS NOT NULL",
        "An external modifier can only be mapped once per delivery store.",
    )

    @api.constrains("store_binding_id", "connection_id")
    def _check_store_connection(self):
        for mapping in self:
            if mapping.store_binding_id and mapping.store_binding_id.connection_id != mapping.connection_id:
                raise ValidationError(_("The modifier mapping store belongs to another connection."))


class DeliveryPaymentBinding(models.Model):
    _name = "bs.delivery.payment.binding"
    _description = "Delivery Payment Method Mapping"
    _order = "connection_id, external_source, external_payment_code, id"
    _check_company_auto = True

    connection_id = fields.Many2one(
        "bs.delivery.connection", required=True, index=True, ondelete="cascade", check_company=True
    )
    store_binding_id = fields.Many2one(
        "bs.delivery.store.binding", index=True, ondelete="cascade", check_company=True
    )
    company_id = fields.Many2one(related="connection_id.company_id", store=True, index=True)
    provider_id = fields.Many2one(related="connection_id.provider_id", store=True, index=True)
    external_source = fields.Char(
        string="Platform Payment Source",
        required=True,
        default="default",
        index=True,
        help=(
            "Provider-side source or sub-brand. This keeps mappings such as "
            "FeedUs/Platform A/cash and FeedUs/Platform B/cash independent."
        ),
    )
    external_payment_code = fields.Char(required=True, index=True)
    external_name = fields.Char()
    collector_scope = fields.Selection(
        [
            ("unassigned", "Needs Collector Setup"),
            ("restaurant", "Restaurant Collects"),
            ("aggregator", "Platform Collects"),
        ],
        required=True,
        default="unassigned",
        index=True,
        help=(
            "Who collects this payment. Existing mappings require an explicit "
            "choice before the Core v1 payment resolver can use them. "
            "The Prepaid flag is not proof of collection."
        ),
    )
    payment_method_id = fields.Many2one(
        "pos.payment.method", required=True, ondelete="restrict", check_company=True
    )
    income_account_id = fields.Many2one(
        "account.account",
        string="Income Account",
        ondelete="restrict",
        help=(
            "Optional revenue account for this exact platform payment source. "
            "When empty, the normal product/category income account is used."
        ),
    )
    is_prepaid = fields.Boolean(
        default=True,
        help="Used by legacy connector workflows; this does not prove who collected a Core v1 payment.",
    )
    active = fields.Boolean(default=True)

    _external_payment_global_unique = models.UniqueIndex(
        "(connection_id, external_source, external_payment_code) "
        "WHERE store_binding_id IS NULL",
        "A global external payment mapping can only exist once per source and connection.",
    )
    _external_payment_store_unique = models.UniqueIndex(
        "(connection_id, store_binding_id, external_source, external_payment_code) "
        "WHERE store_binding_id IS NOT NULL",
        "An external payment method can only be mapped once per source and delivery store.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for incoming in vals_list:
            values = dict(incoming)
            values["external_source"] = self._normalize_external_source(
                values.get("external_source")
            )
            if "external_payment_code" in values:
                values["external_payment_code"] = self._normalize_external_payment_code(
                    values["external_payment_code"]
                )
            prepared.append(values)
        return super().create(prepared)

    def write(self, vals):
        values = dict(vals)
        if "external_source" in values:
            values["external_source"] = self._normalize_external_source(
                values.get("external_source")
            )
        if "external_payment_code" in values:
            values["external_payment_code"] = self._normalize_external_payment_code(
                values["external_payment_code"]
            )
        return super().write(values)

    @staticmethod
    def _normalize_external_source(value):
        return str(value or "default").strip().lower() or "default"

    @staticmethod
    def _normalize_external_payment_code(value):
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValidationError(_("A platform payment code is required."))
        code = str(value).strip()
        if not code:
            raise ValidationError(_("A platform payment code is required."))
        return code

    @api.constrains(
        "store_binding_id",
        "connection_id",
        "payment_method_id",
        "income_account_id",
    )
    def _check_mapping_scope(self):
        for mapping in self:
            if mapping.store_binding_id and mapping.store_binding_id.connection_id != mapping.connection_id:
                raise ValidationError(_("The payment mapping store belongs to another connection."))
            if (
                mapping.payment_method_id.company_id
                and mapping.payment_method_id.company_id != mapping.company_id
            ):
                raise ValidationError(_("The payment method belongs to another company."))
            account = mapping.income_account_id
            if not account:
                continue
            if not account.active:
                raise ValidationError(_("The income account must be active."))
            if "company_ids" in account._fields and mapping.company_id not in account.company_ids:
                raise ValidationError(_("The income account is unavailable to this company."))
            if (
                "company_id" in account._fields
                and account.company_id
                and account.company_id != mapping.company_id
            ):
                raise ValidationError(_("The income account belongs to another company."))


class DeliveryMenuVersion(models.Model):
    _name = "bs.delivery.menu.version"
    _description = "Delivery Menu Synchronization Version"
    _order = "create_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, default=lambda self: _("New Menu Synchronization"))
    connection_id = fields.Many2one(
        "bs.delivery.connection", required=True, index=True, ondelete="cascade", check_company=True
    )
    store_binding_id = fields.Many2one(
        "bs.delivery.store.binding", index=True, ondelete="cascade", check_company=True
    )
    company_id = fields.Many2one(related="connection_id.company_id", store=True, index=True)
    provider_id = fields.Many2one(related="connection_id.provider_id", store=True, index=True)
    direction = fields.Selection(
        [("pull", "Pull from Platform"), ("push", "Push to Platform")], required=True, default="pull"
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("preview", "Preview"),
            ("queued", "Queued"),
            ("processing", "Processing"),
            ("done", "Done"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        required=True,
        default="draft",
        index=True,
        copy=False,
    )
    payload_hash = fields.Char(index=True, copy=False)
    added_count = fields.Integer(readonly=True, copy=False)
    updated_count = fields.Integer(readonly=True, copy=False)
    removed_count = fields.Integer(readonly=True, copy=False)
    conflict_count = fields.Integer(readonly=True, copy=False)
    started_at = fields.Datetime(readonly=True, copy=False)
    completed_at = fields.Datetime(readonly=True, copy=False)
    last_error = fields.Text(readonly=True, copy=False)

    @api.constrains("store_binding_id", "connection_id")
    def _check_store_connection(self):
        for version in self:
            if version.store_binding_id and version.store_binding_id.connection_id != version.connection_id:
                raise ValidationError(_("The menu synchronization store belongs to another connection."))

    def _resolve_operation(self):
        self.ensure_one()
        adapter = self.connection_id._get_adapter()
        operation = "pull_menu" if self.direction == "pull" else "push_menu"
        catalog_capability = "menu_pull" if self.direction == "pull" else "menu_push"
        if (
            not self.provider_id.supports_catalog_capability(catalog_capability)
            or not adapter.supports(operation)
        ):
            raise ValidationError(
                _("%(provider)s does not support %(direction)s menu synchronization.")
                % {
                    "provider": self.provider_id.display_name,
                    "direction": self.direction,
                }
            )
        return operation

    def action_run_now(self):
        """Execute selected menu jobs now and retain a durable result record.

        Platform-form actions use this entry point so a button never leaves an
        unprocessed pseudo-queue record behind.  The cron-oriented queue API is
        still available for deployments that schedule it externally.
        """
        runnable = self.filtered(
            lambda record: record.state in ("draft", "preview", "queued", "failed")
        )
        for version in runnable:
            if version.state != "queued":
                version._resolve_operation()
                version.write(
                    {
                        "state": "queued",
                        "started_at": False,
                        "completed_at": False,
                        "last_error": False,
                    }
                )
            if not version._claim_for_processing():
                continue
            try:
                with self.env.cr.savepoint():
                    result = version._dispatch_menu_sync()
                    version._mark_sync_done(result)
            except Exception as error:
                _logger.exception("Menu synchronization %s failed", version.id)
                version.write(
                    {
                        "state": "failed",
                        "completed_at": fields.Datetime.now(),
                        "last_error": str(error)[:4000],
                    }
                )
        return True

    def action_queue(self):
        for version in self.filtered(lambda record: record.state in ("draft", "preview", "failed")):
            version._resolve_operation()
            version.write(
                {
                    "state": "queued",
                    "started_at": False,
                    "completed_at": False,
                    "last_error": False,
                }
            )
        return True

    @api.model
    def _cron_process_queue(self, limit=20):
        """Claim and execute queued menu jobs without double-processing them."""
        limit = max(1, min(int(limit or 20), 200))
        queued = self.search([("state", "=", "queued")], order="create_date, id", limit=limit)
        processed = 0
        cron = self.env["ir.cron"]
        commit_progress = bool(self.env.context.get("cron_id"))
        for version in queued:
            with self.env.cr.savepoint():
                if not version._claim_for_processing():
                    continue
                try:
                    with self.env.cr.savepoint():
                        result = version._dispatch_menu_sync()
                        version._mark_sync_done(result)
                except Exception as error:
                    _logger.exception("Menu synchronization %s failed", version.id)
                    version.write(
                        {
                            "state": "failed",
                            "completed_at": fields.Datetime.now(),
                            "last_error": str(error)[:4000],
                        }
                    )
            processed += 1
            if commit_progress and not cron._commit_progress(1):
                break
        return processed

    def _claim_for_processing(self):
        self.ensure_one()
        # ``action_run_now`` may have moved this record to ``queued`` only a
        # few lines earlier.  Flush that ORM write before the SQL claim, or
        # PostgreSQL can still see the previous state and the synchronous
        # platform button silently leaves the job queued.
        self.flush_recordset(["state"])
        self.env.cr.execute(
            "SELECT id FROM bs_delivery_menu_version "
            "WHERE id = %s AND state = 'queued' FOR UPDATE SKIP LOCKED",
            [self.id],
        )
        if not self.env.cr.fetchone():
            return False
        self.invalidate_recordset(["state"])
        if self.state != "queued":
            return False
        self.write(
            {
                "state": "processing",
                "started_at": fields.Datetime.now(),
                "completed_at": False,
                "last_error": False,
            }
        )
        return True

    def _dispatch_menu_sync(self):
        self.ensure_one()
        operation = self._resolve_operation()
        return self.connection_id._dispatch(
            operation,
            store_binding=self.store_binding_id,
            menu_version=self,
            idempotency_key="delivery-menu-version:%s" % self.id,
        )

    def _mark_sync_done(self, result):
        self.ensure_one()
        values = result if isinstance(result, dict) else {}
        count_values = (
            values.get("result")
            if isinstance(values.get("result"), dict)
            else values
        )
        serialized = json.dumps(values, sort_keys=True, default=str, ensure_ascii=False)
        payload_hash = values.get("payload_hash") or count_values.get("payload_hash")
        self.write(
            {
                "state": "done",
                "payload_hash": str(payload_hash)
                if payload_hash
                else hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                "added_count": self._result_count(count_values, "added", "created"),
                "updated_count": self._result_count(count_values, "updated"),
                "removed_count": self._result_count(
                    count_values, "removed", "deactivated"
                ),
                "conflict_count": self._result_count(
                    count_values, "conflicts", "conflict"
                ),
                "completed_at": fields.Datetime.now(),
                "last_error": False,
            }
        )
        return True

    @staticmethod
    def _result_count(values, *keys):
        value = next((values[key] for key in keys if key in values), 0)
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def action_cancel(self):
        for version in self:
            self.env.cr.execute(
                "SELECT id FROM bs_delivery_menu_version WHERE id = %s FOR UPDATE",
                [version.id],
            )
            version.invalidate_recordset(["state"])
            if version.state in ("draft", "preview", "queued", "failed"):
                version.write({"state": "cancelled"})
        return True
