from odoo import api, fields, models


class PosBusinessDayClosingWizard(models.TransientModel):
    """Branch manager report: every session of one business day."""

    _name = "pos.business.day.closing.wizard"
    _description = "POS Business Day Closing Report"

    config_id = fields.Many2one(
        "pos.config", "Point of Sale", required=True,
        default=lambda self: self.env["pos.config"].search([("company_id", "=", self.env.company.id)], limit=1),
    )
    business_date = fields.Date(
        "Business Date", required=True,
        default=lambda self: self.env["pos.closing.report.data"]._current_business_date(self.env.company),
        help="The business day starts at the 'Business Day Start Hour' of the company "
        "and ends at the same hour the next day.",
    )
    lang_mode = fields.Selection(
        [
            ("config", "As set on the Point of Sale"),
            ("ar", "Arabic"),
            ("en", "English"),
            ("both", "Arabic and English"),
        ],
        string="Report Language", default="config", required=True,
    )
    scope = fields.Selection(related="config_id.pcr_day_scope", readonly=False)

    @api.onchange("config_id")
    def _onchange_config_id(self):
        if self.config_id:
            self.business_date = self.env["pos.closing.report.data"]._current_business_date(
                self.config_id.company_id
            )

    def _get_report_data(self):
        self.ensure_one()
        return self.env["pos.closing.report.data"].business_day_data(
            self.config_id,
            self.business_date,
            lang_mode=None if self.lang_mode == "config" else self.lang_mode,
        )

    def action_print(self):
        self.ensure_one()
        return self.env.ref("pos_closing_report.action_report_pos_business_day").report_action(self)
