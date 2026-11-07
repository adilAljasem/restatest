from odoo import api, fields, models

# Keep in sync with static/src/icons/icons.js (RS_ICONS)
RS_ICON_KEYS = [
    "vision", "smart", "erp", "kpi", "pay", "pos", "invoice", "ledger", "box", "cart",
    "bag", "target", "factory", "chat", "contacts", "apps", "settings", "calendar",
    "kanban", "people", "clock", "sun", "recruit", "receipt", "hourglass", "globe",
    "mail", "ticket", "survey", "car", "wrench", "book", "folder", "sign", "lifebuoy",
    "planning", "todo", "dining", "chef", "shield", "gift", "headset", "learn",
    "megaphone", "barcode", "approve", "sheet", "repeat",
]


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    rs_section = fields.Char(
        string="Sidebar Section", translate=True,
        help="Top-level apps sharing the same section are grouped together in "
             "the sidebar. Leave empty to use the automatic grouping.",
    )
    rs_icon = fields.Selection(
        [(key, key.capitalize()) for key in RS_ICON_KEYS],
        string="Sidebar Icon",
        help="Resta line icon used for this app. Leave empty for automatic.",
    )
    rs_section_sequence = fields.Integer(
        string="Section Order", default=50,
        help="Sections are sorted by the smallest order of their apps.",
    )

    @api.model
    def load_web_menus(self, debug):
        menus = super().load_web_menus(debug)
        root = menus.get("root") or {}
        app_ids = [mid for mid in root.get("children", []) if isinstance(mid, int)]
        if app_ids:
            apps = self.sudo().browse(app_ids).with_prefetch(app_ids)
            for app in apps:
                entry = menus.get(app.id)
                if entry is not None:
                    entry["rsSection"] = app.rs_section or False
                    entry["rsSectionSequence"] = app.rs_section_sequence
                    entry["rsIcon"] = app.rs_icon or False
        return menus
