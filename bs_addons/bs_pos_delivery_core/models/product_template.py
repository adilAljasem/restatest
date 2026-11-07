from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    delivery_available = fields.Boolean(
        string="Available in Delivery",
        default=True,
        index=True,
        help="Master availability toggle for delivery aggregator platforms (e.g. Blend, Deliverect).",
    )

    def _get_active_delivery_connections(self):
        """Return active, connected delivery connections for this product's company."""
        domain = [
            ("state", "=", "connected"),
            ("active", "=", True),
            ("provider_id.active", "=", True),
        ]
        companies = self.mapped("company_id")
        if companies:
            domain.append(("company_id", "in", companies.ids))
        elif self.env.companies:
            domain.append(("company_id", "in", self.env.companies.ids))
        return self.env["bs.delivery.connection"].sudo().search(domain)

    def action_delivery_toggle_availability(self):
        self.check_access("write")
        for template in self:
            new_val = not template.delivery_available
            template.delivery_available = new_val
            connections = template._get_active_delivery_connections()
            for connection in connections:
                adapter = connection._get_adapter()
                if adapter.supports("update_product_availability"):
                    connection._dispatch(
                        "update_product_availability",
                        template,
                        new_val,
                    )
        state_label = _("available") if all(self.mapped("delivery_available")) else _("closed")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Delivery Availability"),
                "message": _("Product availability set to %(state)s for delivery platforms.")
                % {"state": state_label},
                "type": "success",
                "sticky": False,
            },
        }

    def action_delivery_sync_product(self):
        self.check_access("write")
        if not (
            self.env.user.has_group("bs_pos_delivery_core.group_delivery_manager")
            or self.env.is_admin()
        ):
            raise AccessError(_("Delivery manager access is required to sync products to delivery platforms."))
        for template in self:
            connections = template._get_active_delivery_connections()
            for connection in connections:
                adapter = connection._get_adapter()
                if adapter.supports("sync_products"):
                    connection._dispatch("sync_products", template)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Delivery Synchronization"),
                "message": _("Product synchronization dispatched to connected delivery platforms."),
                "type": "success",
                "sticky": False,
            },
        }

