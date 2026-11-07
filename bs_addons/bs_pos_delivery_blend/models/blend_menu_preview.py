"""Show, per branch, exactly what ``/api/menu`` publishes to Blend for one product."""

from markupsafe import Markup, escape

from odoo import _, fields, models

from ..services.exceptions import BlendAPIError
from ..services.pos_api import BlendPOSAPIService


class BlendMenuPreview(models.TransientModel):
    _name = "bs.blend.menu.preview"
    _description = "Blend Menu Preview"

    product_tmpl_id = fields.Many2one("product.template", required=True, readonly=True)
    result_html = fields.Html(readonly=True, sanitize=False)

    def _build(self):
        self.ensure_one()
        product = self.product_tmpl_id.sudo()
        connections = self.env["bs.delivery.connection"].sudo().search([
            ("provider_id.code", "=", "blend"),
            ("active", "=", True),
            ("company_id", "in", (product.company_id or self.env.company).ids),
        ])
        sections = []
        for connection in connections:
            for config in connection.pos_config_ids.filtered(lambda branch: branch.active and branch.blend_enabled):
                sections.append(self._branch_section(connection, config, product))
        if not sections:
            sections.append(Markup("<p>%s</p>") % _("No active Blend branch is linked to this company."))
        self.result_html = Markup("").join(sections)

    def _branch_section(self, connection, config, product):
        title = Markup("<h3>%s — %s</h3>") % (connection.display_name, config.display_name)
        service = BlendPOSAPIService(self.env, config, connection=connection)
        service.trace = []
        try:
            menu = service.get_menu(filters={
                "product_id": BlendPOSAPIService._record_external_id(product),
            }, per=100)
        except BlendAPIError as error:
            return title + Markup('<p class="text-danger">%s</p>') % error.message
        rows = [row for category in menu["categories"] for row in category["products"]]
        if not rows:
            return title + Markup('<p class="text-danger">%s</p>') % _(
                "The product itself is not in the Blend menu of this branch "
                "(check Point of Sale, Delivery Platforms, Can be Sold and the Blend fields)."
            )
        groups_sent = len(rows[0]["modifier_groups"])
        head = Markup(
            "<p>%s</p><table class='table table-sm table-bordered'><thead><tr>"
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
            "</tr></thead><tbody>"
        ) % (
            _("Modifier groups sent: %s") % groups_sent,
            _("Group"), _("Option"), _("Product"), _("Sent"), _("Available"), _("Price"), _("Reason"),
        )
        body = Markup("")
        for row in service.trace:
            sent = row.get("sent")
            status = row.get("status")
            css = "" if sent and status else "table-warning" if sent else "table-danger"
            body += Markup(
                "<tr class='%s'><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            ) % (
                css, row.get("group", ""), row.get("option", ""), row.get("product", ""),
                _("Yes") if sent else _("No"),
                "" if not sent else (_("Yes") if status else _("No")),
                "" if not sent else row.get("price", 0.0),
                row.get("reason", ""),
            )
        return title + head + body + Markup("</tbody></table>")


class ProductTemplate(models.Model):
    _inherit = "product.template"

    def action_blend_menu_preview(self):
        self.ensure_one()
        preview = self.env["bs.blend.menu.preview"].create({"product_tmpl_id": self.id})
        preview._build()
        return {
            "type": "ir.actions.act_window",
            "name": _("Blend Menu Preview"),
            "res_model": preview._name,
            "res_id": preview.id,
            "view_mode": "form",
            "target": "new",
        }
