# Copyright 2018 ForgeFlow, S.L.
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"

    stock_request_responsible_user_ids = fields.Many2many(
        comodel_name="res.users",
        relation="stock_warehouse_request_responsible_user_rel",
        column1="warehouse_id",
        column2="user_id",
        string="Stock Request Responsible Users",
        domain=[("share", "=", False)],
        help=(
            "Users responsible for accepting and delivering requests from this "
            "warehouse, or receiving requests sent to it."
        ),
    )
    default_stock_request_source_warehouse_id = fields.Many2one(
        comodel_name="stock.warehouse",
        string="Default Source Warehouse",
        check_company=True,
        ondelete="restrict",
        domain="[('company_id', '=', company_id), ('id', '!=', id)]",
        help=(
            "Warehouse proposed as the source when this warehouse is selected "
            "as the destination of a Stock Request."
        ),
    )
    stock_request_receiving_transit_location_id = fields.Many2one(
        comodel_name="stock.location",
        string="Receiving Transit Location",
        check_company=True,
        ondelete="restrict",
        domain="[('usage', '=', 'transit'), ('company_id', '=', company_id)]",
        help=(
            "Optional transit location used for transfers received by this "
            "warehouse. Odoo's company Internal Transit Location is used when "
            "this field is empty."
        ),
    )
    stock_request_transit_mode = fields.Selection(
        selection=[
            ("custom", "Custom Warehouse Transit"),
            ("odoo_default", "Odoo Default Transit"),
            ("missing", "Missing Transit Configuration"),
        ],
        string="Transit Mode",
        compute="_compute_stock_request_setup_status",
    )
    stock_request_configuration_status = fields.Selection(
        selection=[
            ("ready", "Ready"),
            ("missing_responsible", "Missing Responsible Users"),
            ("missing_source", "Missing Default Source Warehouse"),
            ("missing_transit", "Missing Transit Location"),
        ],
        string="Configuration Status",
        compute="_compute_stock_request_setup_status",
    )

    is_project_wh = fields.Boolean(
        'Is Project WH ?',
    )

    @api.onchange('is_project_wh')
    def onchange_is_project_wh(self ):
        if self.is_project_wh == False:
            return
        count = self.env['stock.warehouse'].search_count([('is_project_wh' , '=' , True) ])
        if count:
            self.is_project_wh = False
            raise ValidationError(
                _('No 2 warehouse can has  "Is Project WH ?" ticked.')
            )

    def copy(self, default=None):
        res = super().copy(default)
        res.is_project_wh = False
        return res

    @api.depends(
        "stock_request_responsible_user_ids",
        "default_stock_request_source_warehouse_id",
        "stock_request_receiving_transit_location_id",
        "stock_request_receiving_transit_location_id.active",
        "stock_request_receiving_transit_location_id.usage",
        "stock_request_receiving_transit_location_id.company_id",
        "company_id.internal_transit_location_id",
        "company_id.internal_transit_location_id.active",
        "company_id.internal_transit_location_id.usage",
        "company_id.internal_transit_location_id.company_id",
    )
    def _compute_stock_request_setup_status(self):
        for warehouse in self:
            transit_location = (
                warehouse.stock_request_receiving_transit_location_id
            )
            if transit_location:
                warehouse.stock_request_transit_mode = "custom"
            else:
                transit_location = warehouse.company_id.internal_transit_location_id
            if not warehouse.stock_request_receiving_transit_location_id and (
                transit_location
            ):
                warehouse.stock_request_transit_mode = "odoo_default"
            elif not transit_location:
                warehouse.stock_request_transit_mode = "missing"

            valid_transit = (
                transit_location
                and transit_location.active
                and transit_location.usage == "transit"
                and transit_location.company_id == warehouse.company_id
            )

            if not warehouse.stock_request_responsible_user_ids.filtered("active"):
                warehouse.stock_request_configuration_status = (
                    "missing_responsible"
                )
            elif not valid_transit:
                warehouse.stock_request_configuration_status = "missing_transit"
            elif not warehouse.default_stock_request_source_warehouse_id:
                warehouse.stock_request_configuration_status = "missing_source"
            else:
                warehouse.stock_request_configuration_status = "ready"

    def _get_stock_request_transit_location(self):
        self.ensure_one()
        return (
            self.stock_request_receiving_transit_location_id
            or self.company_id.internal_transit_location_id
        )

    @api.constrains("stock_request_responsible_user_ids")
    def _check_stock_request_responsible_user_access(self):
        for warehouse in self:
            invalid_users = warehouse.stock_request_responsible_user_ids.filtered(
                "share"
            )
            if invalid_users:
                raise ValidationError(
                    _(
                        "Warehouse responsible users must be internal users: "
                        "%(users)s",
                        users=", ".join(invalid_users.mapped("display_name")),
                    )
                )

    @api.constrains(
        "default_stock_request_source_warehouse_id",
        "stock_request_receiving_transit_location_id",
    )
    def _check_stock_request_warehouse_setup(self):
        for warehouse in self:
            source_warehouse = warehouse.default_stock_request_source_warehouse_id
            if source_warehouse:
                if source_warehouse == warehouse:
                    raise ValidationError(
                        _(
                            "The default source warehouse must be different "
                            "from the receiving warehouse."
                        )
                    )
                if source_warehouse.company_id != warehouse.company_id:
                    raise ValidationError(
                        _(
                            "The default source warehouse must belong to the "
                            "same company."
                        )
                    )
            transit_location = (
                warehouse.stock_request_receiving_transit_location_id
            )
            if transit_location:
                if transit_location.usage != "transit":
                    raise ValidationError(
                        _("The receiving transit location must be a Transit Location.")
                    )
                if transit_location.company_id != warehouse.company_id:
                    raise ValidationError(
                        _(
                            "The receiving transit location must belong to the "
                            "warehouse company."
                        )
                    )

    @api.model
    def _sync_stock_request_warehouse_responsible_group(self):
        responsible_group = self.env.ref(
            "stock_request.group_stock_request_warehouse_responsible",
            raise_if_not_found=False,
        )
        if not responsible_group:
            return True
        eligible_users = self.sudo().search([]).mapped(
            "stock_request_responsible_user_ids"
        )
        manager_group = self.env.ref("stock_request.group_stock_request_manager")
        eligible_users |= self.env["res.users"].sudo().with_context(
            active_test=False
        ).search([("group_ids", "in", manager_group.ids)])
        current_users = self.env["res.users"].sudo().with_context(
            active_test=False
        ).search([("group_ids", "in", responsible_group.ids)])
        (eligible_users - current_users).write(
            {"group_ids": [Command.link(responsible_group.id)]}
        )
        (current_users - eligible_users).write(
            {"group_ids": [Command.unlink(responsible_group.id)]}
        )
        return True

    @api.model
    def _sync_stock_request_direct_transfer_group(self):
        """Compatibility hook for upgrades from releases before 19.0.1.7.0."""
        return self._sync_stock_request_warehouse_responsible_group()

    def _check_manage_stock_request_setup(self, vals_list):
        if self.env.su or self.env.user.has_group(
            "stock_request.group_stock_request_manager"
        ):
            return
        setup_fields = {
            "stock_request_responsible_user_ids",
            "default_stock_request_source_warehouse_id",
            "stock_request_receiving_transit_location_id",
        }
        if any(set(vals) & setup_fields for vals in vals_list):
            raise AccessError(
                _(
                    "Only a Stock Request Manager can manage Stock Request "
                    "warehouse setup."
                )
            )

    def action_create_stock_request_receiving_transit_location(self):
        self.ensure_one()
        self._check_manage_stock_request_setup(
            [{"stock_request_receiving_transit_location_id": False}]
        )
        if not self.stock_request_receiving_transit_location_id:
            transit_location = self.env["stock.location"].create(
                {
                    "name": "[%s] %s" % (self.code, _("Receiving Transit")),
                    "usage": "transit",
                    "company_id": self.company_id.id,
                }
            )
            self.stock_request_receiving_transit_location_id = transit_location
        return {"type": "ir.actions.client", "tag": "reload"}

    @api.model_create_multi
    def create(self, vals_list):
        if any(not vals.get("name") for vals in vals_list):
            raise ValidationError(
                _("Warehouse Name is required before creating a warehouse.")
            )
        self._check_manage_stock_request_setup(vals_list)
        warehouses = super().create(vals_list)
        if any("stock_request_responsible_user_ids" in vals for vals in vals_list):
            warehouses._sync_stock_request_warehouse_responsible_group()
        return warehouses

    def write(self, vals):
        self._check_manage_stock_request_setup([vals])
        result = super().write(vals)
        if "stock_request_responsible_user_ids" in vals:
            self._sync_stock_request_warehouse_responsible_group()
        return result

    def unlink(self):
        result = super().unlink()
        self._sync_stock_request_warehouse_responsible_group()
        return result

    @api.constrains("company_id")
    def _check_company_stock_request(self):
        if any(
            self.env["stock.request"].search(
                [
                    ("company_id", "!=", rec.company_id.id),
                    ("warehouse_id", "=", rec.id),
                ],
                limit=1,
            )
            for rec in self
        ):
            raise ValidationError(
                _(
                    "You cannot change the company of the warehouse, as it is "
                    "already assigned to stock requests that belong to "
                    "another company."
                )
            )
        if any(
            self.env["stock.request.order"].search(
                [
                    ("company_id", "!=", rec.company_id.id),
                    "|",
                    ("warehouse_id", "=", rec.id),
                    ("source_warehouse_id", "=", rec.id),
                ],
                limit=1,
            )
            for rec in self
        ):
            raise ValidationError(
                _(
                    "You cannot change the company of the warehouse, as it is "
                    "already assigned to stock requests that belong to "
                    "another company."
                )
            )
