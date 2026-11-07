from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bs_analytic_rate_frequency = fields.Selection(
        selection=[
            ("manual", "Manual"),
            ("daily", "Daily"),
            ("weekly", "Weekly"),
            ("monthly", "Monthly"),
            ("yearly", "Yearly"),
        ],
        string="Refresh Frequency",
        required=True,
        default="manual",
        config_parameter="bs_analytic_distribution_rate.refresh_frequency",
    )
    bs_analytic_rate_next_run = fields.Datetime(string="Next Run")

    _BS_CRON_INTERVALS = {
        "daily": (1, "days", relativedelta(days=1)),
        "weekly": (1, "weeks", relativedelta(weeks=1)),
        "monthly": (1, "months", relativedelta(months=1)),
        "yearly": (12, "months", relativedelta(years=1)),
    }

    @api.model
    def get_values(self):
        values = super().get_values()
        cron = self.env.ref(
            "bs_analytic_distribution_rate.ir_cron_refresh_analytic_rates",
            raise_if_not_found=False,
        )
        values["bs_analytic_rate_next_run"] = (
            cron.sudo().nextcall if cron and cron.sudo().active else False
        )
        return values

    def set_values(self):
        super().set_values()
        self.ensure_one()
        cron = self.env.ref(
            "bs_analytic_distribution_rate.ir_cron_refresh_analytic_rates"
        ).sudo()
        frequency = self.bs_analytic_rate_frequency
        if frequency == "manual":
            cron.active = False
            return

        interval_number, interval_type, interval_delta = self._BS_CRON_INTERVALS[
            frequency
        ]
        next_run = self.bs_analytic_rate_next_run
        if not next_run:
            next_run = fields.Datetime.now() + interval_delta
        cron.write({
            "active": True,
            "interval_number": interval_number,
            "interval_type": interval_type,
            "nextcall": next_run,
        })
