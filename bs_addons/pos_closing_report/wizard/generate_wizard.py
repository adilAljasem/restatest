from odoo import fields, models


class PosClosingReportGenerate(models.TransientModel):
    _name = "pos.closing.report.generate"
    _description = "Generate POS Closing Reports Retroactively"

    date_from = fields.Date("From Date")
    date_to = fields.Date("To Date")
    config_ids = fields.Many2many(
        "pos.config", string="Points of Sale", help="Leave empty for all points of sale"
    )
    user_ids = fields.Many2many("res.users", string="Session Users", help="Leave empty for all users")
    regenerate = fields.Boolean("Regenerate existing retroactive reports")

    def action_generate(self):
        self.ensure_one()
        domain = [("state", "=", "closed")]
        if self.date_from:
            domain.append(("stop_at", ">=", fields.Datetime.to_datetime(self.date_from)))
        if self.date_to:
            domain.append(("stop_at", "<", fields.Datetime.add(fields.Datetime.to_datetime(self.date_to), days=1)))
        if self.config_ids:
            domain.append(("config_id", "in", self.config_ids.ids))
        if self.user_ids:
            domain.append(("user_id", "in", self.user_ids.ids))
        sessions = self.env["pos.session"].search(domain)
        Report = self.env["pos.closing.report"]
        if self.regenerate:
            # Never delete live reports: they hold what the cashier actually entered at closing.
            Report.search([("session_id", "in", sessions.ids), ("source", "=", "retro")]).unlink()
        Report._generate_for_sessions(sessions, source="retro")
        action = self.env["ir.actions.act_window"]._for_xml_id("pos_closing_report.action_pos_closing_report")
        action["domain"] = [("session_id", "in", sessions.ids)]
        action["context"] = {"search_default_group_cashier": 1}
        if not sessions:
            action["help"] = "<p>%s</p>" % self.env._("No closed sessions match the selected filters.")
        return action
