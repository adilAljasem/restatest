import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class PosSession(models.Model):
    _inherit = "pos.session"

    def _validate_session(self, *args, **kwargs):
        """Catch closings done from the POS UI as well as from the backend."""
        bank_diffs = kwargs.get("bank_payment_method_diffs")
        if bank_diffs is None and len(args) >= 3:
            bank_diffs = args[2]
        res = super()._validate_session(*args, **kwargs)
        for session in self.filtered(lambda s: s.state == "closed"):
            try:
                with self.env.cr.savepoint():
                    self.env["pos.closing.report"]._create_for_session(
                        session,
                        bank_diffs=bank_diffs or {},
                        cashier_name=self.env.context.get("pos_closing_cashier") or session._closing_cashier_guess(),
                        source="live",
                    )
            except Exception:
                # Never block the session closing because of the report
                _logger.exception("Could not create closing report for session %s", session.name)
        return res

    def _closing_cashier_guess(self):
        """Employee name: last employee/user who recorded an order, else the session user."""
        self.ensure_one()
        last_order = self.order_ids.sorted("id")[-1:]
        if last_order:
            employee = getattr(last_order, "employee_id", False)  # when pos_hr is installed
            if employee:
                return employee.name
            if last_order.user_id:
                return last_order.user_id.name
        return self.user_id.name

    @api.model
    def get_closing_receipt_data(self, session_id):
        """Called by the POS after closing to print the cashier report."""
        report = self.env["pos.closing.report"].sudo().search([("session_id", "=", session_id)], limit=1)
        return report.get_receipt_data() if report else False

    @api.model
    def get_business_day_receipt_data(self, config_id, business_date=False, employee_id=False):
        """Called by the POS (managers) to print the branch manager report of a business day.

        :param business_date: 'YYYY-MM-DD'; empty = the business day running now
            (after midnight and before the business day start hour, the previous day).
        """
        config = self.env["pos.config"].browse(int(config_id)).exists()
        if not config:
            return False
        Data = self.env["pos.closing.report.data"]
        Data._check_day_report_access(config, employee_id)
        return Data.business_day_data(config, business_date or None)

    @api.model
    def get_business_day_choices(self, config_id):
        """Business days offered in the POS: the current one and the previous ones."""
        config = self.env["pos.config"].sudo().browse(int(config_id)).exists()
        if not config:
            return []
        today = self.env["pos.closing.report.data"]._current_business_date(config.company_id)
        return [fields.Date.to_string(today - timedelta(days=i)) for i in range(7)]
