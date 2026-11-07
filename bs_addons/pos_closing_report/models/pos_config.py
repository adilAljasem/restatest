import json

from odoo import api, fields, models


class PosConfig(models.Model):
    """Closing report options (Point of Sale > Configuration > Closing Report)."""

    _inherit = "pos.config"

    # General
    pcr_language = fields.Selection(
        [
            ("user", "Language of the user printing"),
            ("ar", "Arabic"),
            ("en", "English"),
            ("both", "Arabic and English"),
        ],
        string="Report Language",
        default="user",
        required=True,
        help="Language of the closing reports when they are printed.",
    )
    pcr_use_kitchen_style = fields.Boolean(
        string="Use Kitchen Ticket Print Settings",
        default=True,
        help="Print the closing reports with the font size, printer width and the crisp "
        "black printing set in Point of Sale > Configuration > Kitchen Ticket.",
    )

    # Cashier report (one session)
    pcr_cashier_auto_print = fields.Boolean(
        string="Print When Closing the Register",
        default=True,
        help="Print the cashier closing report automatically after the register is closed.",
    )
    pcr_cashier_show_summary = fields.Boolean(string="Cashier Report: Sales Summary", default=True)
    pcr_cashier_show_items = fields.Boolean(string="Cashier Report: Sold Items", default=True)
    pcr_cashier_show_refunds = fields.Boolean(string="Cashier Report: Refunds", default=True)
    pcr_cashier_show_cancellations = fields.Boolean(string="Cashier Report: Cancellations", default=True)

    # Branch manager report (whole business day)
    pcr_day_scope = fields.Selection(
        [
            ("config", "This Point of Sale"),
            ("company", "All Points of Sale of the Branch (Company)"),
        ],
        string="Business Day Report Covers",
        default="config",
        required=True,
        help="Sessions included in the branch manager report of a business day.",
    )
    pcr_business_day_start_hour = fields.Float(
        related="company_id.business_day_start_hour", string="Business Day Start Hour",
    )
    pcr_day_in_pos = fields.Boolean(
        string="Print From the POS (Managers)",
        default=True,
        help="Show 'Business Day Report' in the POS menu for managers.",
    )
    pcr_day_user_ids = fields.Many2many(
        "res.users", "pos_config_pcr_day_user_rel", "config_id", "user_id",
        string="Users Allowed to Print the Business Day Report",
        help="Leave empty: POS managers. Otherwise only these users can print the business day "
        "report from the POS (with employee login, the user linked to the employee).",
    )
    pcr_day_access = fields.Char(
        string="Business Day Report Access",
        compute="_compute_pcr_day_access",
        help="Who can print the business day report, read by the POS (JSON).",
    )
    pcr_day_auto_print = fields.Boolean(
        string="Print When a Manager Closes the Register",
        default=False,
        help="When a manager closes the register, also print the business day report.",
    )
    pcr_day_show_summary = fields.Boolean(string="Business Day Report: Sales Summary", default=True)
    pcr_day_show_sessions = fields.Boolean(string="Business Day Report: Sessions Detail", default=True)
    pcr_day_show_items = fields.Boolean(string="Business Day Report: Sold Items", default=True)
    pcr_day_show_refunds = fields.Boolean(string="Business Day Report: Refunds", default=True)
    pcr_day_show_cancellations = fields.Boolean(string="Business Day Report: Cancellations", default=True)

    # ------------------------------------------------------------------
    # Who can print the business day report
    # ------------------------------------------------------------------
    def _pcr_day_allowed(self):
        """{'users': [ids], 'employees': [ids]} allowed; both empty = POS managers."""
        self.ensure_one()
        return {"users": self.sudo().pcr_day_user_ids.ids, "employees": []}

    @api.depends("pcr_day_user_ids")
    def _compute_pcr_day_access(self):
        for config in self:
            config.pcr_day_access = json.dumps(config._pcr_day_allowed()) if config.id else "{}"

    def _pcr_day_can_print(self, user, employee=None):
        """True / False when a list of allowed people is set, None when it is not (managers rule)."""
        self.ensure_one()
        allowed = self._pcr_day_allowed()
        if not (allowed["users"] or allowed["employees"]):
            return None
        if employee:
            # Employee login (pos_hr): the session user is shared, check the employee
            return employee.id in allowed["employees"] or employee.user_id.id in allowed["users"]
        return user.id in allowed["users"]
