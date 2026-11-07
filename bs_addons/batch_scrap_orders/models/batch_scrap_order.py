from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class BatchScrapOrder(models.Model):
    _name = "batch.scrap.order"
    _description = "Batch Scrap Order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char(
        string="Batch Number",
        required=True,
        readonly=True,
        copy=False,
        default=lambda self: _("New"),
        index="btree_not_null",
    )
    source_document = fields.Char(string="Source Document", tracking=True)
    reference = fields.Char(copy=False, tracking=True)
    analytic_account_id = fields.Many2one(
        "account.analytic.account",
        string="Analytic Account",
        check_company=True,
        tracking=True,
        help="Allocate 100% of the scrap expense to this analytic account.",
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    location_id = fields.Many2one(
        "stock.location",
        string="Source Location",
        compute="_compute_location_id",
        store=True,
        required=True,
        precompute=True,
        readonly=False,
        domain="[('usage', '=', 'internal')]",
        check_company=True,
        tracking=True,
    )
    scrap_location_id = fields.Many2one(
        "stock.location",
        string="Scrap Location",
        compute="_compute_scrap_location_id",
        store=True,
        required=True,
        precompute=True,
        readonly=False,
        domain="[('usage', '=', 'inventory')]",
        check_company=True,
        tracking=True,
    )
    line_ids = fields.One2many(
        "batch.scrap.order.line",
        "batch_id",
        string="Scrap Lines",
        copy=True,
    )
    scrap_ids = fields.One2many(
        "stock.scrap",
        "batch_scrap_order_id",
        string="Scrap Orders",
        readonly=True,
        copy=False,
    )
    scrap_count = fields.Integer(
        string="Scrap Order Count",
        compute="_compute_scrap_count",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("done", "Done"),
            ("cancel", "Cancelled"),
        ],
        string="Status",
        required=True,
        readonly=True,
        copy=False,
        default="draft",
        index=True,
        tracking=True,
    )

    _name_company_unique = models.Constraint(
        "UNIQUE(name, company_id)",
        "The Batch Number must be unique per company.",
    )

    @api.depends("company_id")
    def _compute_location_id(self):
        warehouses = self.env["stock.warehouse"].search(
            [("company_id", "in", self.company_id.ids)]
        )
        if not warehouses and self.company_id:
            self.env["stock.warehouse"]._warehouse_redirect_warning()
        groups = warehouses._read_group(
            [("company_id", "in", self.company_id.ids)],
            ["company_id"],
            ["lot_stock_id:array_agg"],
        )
        locations_per_company = {
            company.id: location_ids[0] if location_ids else False
            for company, location_ids in groups
        }
        for batch in self:
            if batch.company_id:
                batch.location_id = locations_per_company.get(batch.company_id.id)

    @api.depends("company_id")
    def _compute_scrap_location_id(self):
        groups = self.env["stock.location"]._read_group(
            [
                ("company_id", "in", self.company_id.ids),
                ("usage", "=", "inventory"),
            ],
            ["company_id"],
            ["id:min"],
        )
        locations_per_company = {
            company.id: location_id for company, location_id in groups
        }
        for batch in self:
            if batch.company_id:
                batch.scrap_location_id = locations_per_company.get(
                    batch.company_id.id
                )

    @api.depends("scrap_ids")
    def _compute_scrap_count(self):
        for batch in self:
            batch.scrap_count = len(batch.scrap_ids)

    @api.model_create_multi
    def create(self, values_list):
        sequence = self.env["ir.sequence"]
        for values in values_list:
            if values.get("name", _("New")) == _("New"):
                values["name"] = sequence.next_by_code("batch.scrap.order") or _(
                    "New"
                )
        return super().create(values_list)

    def write(self, values):
        if "analytic_account_id" in values and any(
            batch.state != "draft"
            and batch.analytic_account_id.id != values["analytic_account_id"]
            for batch in self
        ):
            raise UserError(_("The analytic account can only be changed in Draft."))
        return super().write(values)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_with_scrap_orders(self):
        if any(batch.scrap_ids for batch in self):
            raise UserError(
                _("A batch linked to generated Scrap Orders cannot be deleted.")
            )

    def _check_can_validate(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("Only Draft batches can be validated."))
        if self.scrap_ids:
            raise UserError(_("Scrap Orders have already been created for this batch."))
        if not self.location_id or not self.scrap_location_id:
            raise ValidationError(
                _("Source Location and Scrap Location are required.")
            )
        if not self.line_ids:
            raise ValidationError(
                _("Add at least one scrap line before validating the batch.")
            )
        invalid_lines = self.line_ids.filtered(
            lambda line: not line.product_id
            or line.quantity <= 0
            or not line.product_uom_id
        )
        if invalid_lines:
            raise ValidationError(
                _(
                    "Every scrap line must have a Product, a Unit, and a Quantity "
                    "greater than zero."
                )
            )
        for line in self.line_ids:
            if line._has_kit_bom() and not line._get_kit_bom():
                raise ValidationError(
                    _(
                        "Product %(product)s is a Kit with no available Kit BoM. "
                        "Create or unarchive its BoM (Type = Kit) before scrapping it.",
                        product=line.product_id.display_name,
                    )
                )

    def action_validate(self):
        self.ensure_one()
        self._check_can_validate()
        with self.env.cr.savepoint():
            scraps = self.env["stock.scrap"].with_company(self.company_id).create(
                [line._prepare_scrap_values() for line in self.line_ids]
            )
            for scrap in scraps:
                result = scrap.action_validate()
                if result is not True or scrap.state != "done":
                    raise UserError(
                        _(
                            "Scrap %(scrap)s could not be completed automatically. "
                            "Check the available quantity and any lot or serial "
                            "requirements, then validate the batch again.",
                            scrap=scrap.display_name,
                        )
                    )
            if any(scrap.state != "done" for scrap in scraps):
                raise UserError(
                    _("The batch cannot be completed until every Scrap Order is Done.")
                )
            self.state = "done"
        return True

    def action_cancel(self):
        for batch in self:
            if batch.state != "draft":
                raise UserError(_("Only Draft batches can be cancelled."))
            if batch.scrap_ids:
                raise UserError(
                    _("A batch with generated Scrap Orders cannot be cancelled.")
                )
            batch.state = "cancel"
        return True

    def action_view_scraps(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "stock.action_stock_scrap"
        )
        action["domain"] = [("batch_scrap_order_id", "=", self.id)]
        action["context"] = {"create": False}
        if self.scrap_count == 1:
            action["views"] = [
                (self.env.ref("stock.stock_scrap_form_view").id, "form")
            ]
            action["res_id"] = self.scrap_ids.id
        return action


class BatchScrapOrderLine(models.Model):
    _name = "batch.scrap.order.line"
    _description = "Batch Scrap Order Line"
    _order = "sequence, id"
    _check_company_auto = True

    sequence = fields.Integer(default=10)
    batch_id = fields.Many2one(
        "batch.scrap.order",
        required=True,
        ondelete="cascade",
        index="btree_not_null",
        check_company=True,
    )
    company_id = fields.Many2one(
        related="batch_id.company_id",
        store=True,
        index=True,
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        required=True,
        domain="[('type', '=', 'consu')]",
        check_company=True,
    )
    quantity = fields.Float(
        string="Quantity",
        required=True,
        default=1.0,
        digits="Product Unit",
    )
    product_uom_id = fields.Many2one(
        "uom.uom",
        string="Unit",
        related="product_id.uom_id",
        store=True,
        readonly=True,
    )
    should_replenish = fields.Boolean(
        string="Replenish Quantities",
        help="Trigger replenishment for scrapped products",
    )
    scrap_reason_tag_ids = fields.Many2many(
        "stock.scrap.reason.tag",
        "batch_scrap_line_reason_rel",
        "line_id",
        "reason_id",
        string="Scrap Reason",
    )
    scrap_ids = fields.One2many(
        "stock.scrap",
        "batch_scrap_line_id",
        string="Scrap Orders",
        readonly=True,
        copy=False,
    )

    _positive_quantity = models.Constraint(
        "CHECK(quantity > 0)",
        "Quantity must be greater than zero.",
    )

    @api.model_create_multi
    def create(self, values_list):
        batch_ids = {values.get("batch_id") for values in values_list}
        batches = self.env["batch.scrap.order"].browse(
            [batch_id for batch_id in batch_ids if batch_id]
        )
        if any(batch.state != "draft" for batch in batches):
            raise UserError(_("Scrap lines can only be added in Draft."))
        return super().create(values_list)

    def write(self, values):
        protected_fields = {
            "product_id",
            "quantity",
            "should_replenish",
            "scrap_reason_tag_ids",
            "sequence",
            "batch_id",
        }
        if protected_fields.intersection(values) and any(
            line.batch_id.state != "draft" for line in self
        ):
            raise UserError(_("Validated scrap lines cannot be changed."))
        return super().write(values)

    def unlink(self):
        if any(line.batch_id.state != "draft" for line in self):
            raise UserError(_("Validated scrap lines cannot be deleted."))
        return super().unlink()

    product_is_kit = fields.Boolean(related="product_id.is_kits")

    def _has_kit_bom(self):
        """Whether the product has an active Kit (phantom) BoM, in any company."""
        self.ensure_one()
        return bool(
            self.env["mrp.bom"].sudo().search(
                [
                    ("type", "=", "phantom"),
                    ("active", "=", True),
                    "|",
                    ("product_id", "=", self.product_id.id),
                    "&",
                    ("product_id", "=", False),
                    ("product_tmpl_id", "=", self.product_id.product_tmpl_id.id),
                ],
                limit=1,
            )
        )

    def _get_kit_bom(self):
        """Return the Kit BoM usable in the batch company, or an empty recordset.

        Mirrors the mrp scrap form onchange, which cannot run when scraps are
        created programmatically from a batch. Only kit products are resolved,
        exactly like the core onchange does.
        """
        self.ensure_one()
        if not self.product_is_kit:
            return self.env["mrp.bom"]
        return self.env["mrp.bom"]._bom_find(
            self.product_id,
            company_id=self.batch_id.company_id.id,
            bom_type="phantom",
        )[self.product_id]

    def _prepare_scrap_values(self):
        self.ensure_one()
        batch = self.batch_id
        return {
            "product_id": self.product_id.id,
            "product_uom_id": self.product_uom_id.id,
            "scrap_qty": self.quantity,
            "location_id": batch.location_id.id,
            "scrap_location_id": batch.scrap_location_id.id,
            "origin": batch.source_document,
            "should_replenish": self.should_replenish,
            "scrap_reason_tag_ids": [Command.set(self.scrap_reason_tag_ids.ids)],
            "company_id": batch.company_id.id,
            "batch_reference": batch.name,
            "batch_scrap_order_id": batch.id,
            "batch_scrap_line_id": self.id,
            "bom_id": self._get_kit_bom().id,
        }
