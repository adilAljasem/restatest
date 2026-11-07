from odoo import api, fields, models


class PosClosingReport(models.Model):
    _name = "pos.closing.report"
    _description = "POS Closing Report"
    _order = "stop_at desc, id desc"
    _rec_name = "name"

    name = fields.Char("Session", required=True, readonly=True)
    session_id = fields.Many2one(
        "pos.session", "POS Session", required=True, ondelete="cascade", index=True, readonly=True
    )
    config_id = fields.Many2one("pos.config", "Point of Sale Configuration", readonly=True, index=True)
    branch_name = fields.Char("Point of Sale", readonly=True)
    company_id = fields.Many2one("res.company", "Company", readonly=True)
    currency_id = fields.Many2one("res.currency", "Currency", readonly=True)
    user_id = fields.Many2one("res.users", "Session User", readonly=True, index=True)
    cashier_name = fields.Char("Employee", readonly=True, index=True)
    start_at = fields.Datetime("Opening Date", readonly=True)
    stop_at = fields.Datetime("Closing Date", readonly=True)
    source = fields.Selection(
        [("live", "Automatic (at closing)"), ("retro", "Retroactive")],
        "Source", readonly=True, default="live",
    )
    line_ids = fields.One2many("pos.closing.report.line", "report_id", "Details", readonly=True)

    cash_expected = fields.Monetary("Cash Expected", compute="_compute_totals", store=True)
    cash_counted = fields.Monetary("Cash Counted", compute="_compute_totals", store=True)
    cash_difference = fields.Monetary("Cash Difference", compute="_compute_totals", store=True)
    bank_expected = fields.Monetary("Bank Expected", compute="_compute_totals", store=True)
    bank_counted = fields.Monetary("Bank Counted", compute="_compute_totals", store=True)
    bank_difference = fields.Monetary("Bank Difference", compute="_compute_totals", store=True)
    expected_total = fields.Monetary("Total Expected", compute="_compute_totals", store=True)
    counted_total = fields.Monetary("Total Counted", compute="_compute_totals", store=True)
    difference_total = fields.Monetary("Total Difference", compute="_compute_totals", store=True)
    status = fields.Selection(
        [("balanced", "Balanced"), ("shortage", "Shortage"), ("surplus", "Surplus")],
        "Status", compute="_compute_totals", store=True,
    )

    _session_unique = models.Constraint(
        "unique(session_id)", "A closing report already exists for this session."
    )

    @api.depends("line_ids.expected", "line_ids.counted", "line_ids.method_type")
    def _compute_totals(self):
        for rec in self:
            cash = rec.line_ids.filtered(lambda l: l.method_type == "cash")
            bank = rec.line_ids.filtered(lambda l: l.method_type == "bank")
            rec.cash_expected = sum(cash.mapped("expected"))
            rec.cash_counted = sum(cash.mapped("counted"))
            rec.cash_difference = rec.cash_counted - rec.cash_expected
            rec.bank_expected = sum(bank.mapped("expected"))
            rec.bank_counted = sum(bank.mapped("counted"))
            rec.bank_difference = rec.bank_counted - rec.bank_expected
            rec.expected_total = rec.cash_expected + rec.bank_expected
            rec.counted_total = rec.cash_counted + rec.bank_counted
            rec.difference_total = rec.counted_total - rec.expected_total
            cur = rec.currency_id or rec.env.company.currency_id
            if cur.is_zero(rec.difference_total):
                rec.status = "balanced"
            elif rec.difference_total < 0:
                rec.status = "shortage"
            else:
                rec.status = "surplus"

    # ------------------------------------------------------------------
    # Build the report from session data
    # ------------------------------------------------------------------
    @api.model
    def _prepare_lines(self, session, bank_diffs=None):
        """Return report lines: Cash (expected/counted) + every bank payment method."""
        bank_diffs = {int(k): v for k, v in (bank_diffs or {}).items()}
        lines = []

        totals = {
            pm.id: amount
            for pm, amount in self.env["pos.payment"].sudo()._read_group(
                [("session_id", "=", session.id)], ["payment_method_id"], ["amount:sum"]
            )
        }

        cash_pm = session.payment_method_ids.filtered("is_cash_count")[:1]
        if session.config_id.cash_control and cash_pm:
            lines.append({
                "payment_method_id": cash_pm.id,
                "name": cash_pm.name,
                "method_type": "cash",
                "opening": session.cash_register_balance_start,
                "expected": session.cash_register_balance_end,
                "counted": session.cash_register_balance_end_real,
                "diff_known": True,
            })

        for pm in session.payment_method_ids.filtered(lambda p: p.type == "bank"):
            expected = totals.get(pm.id, 0.0)
            lines.append({
                "payment_method_id": pm.id,
                "name": pm.name,
                "method_type": "bank",
                "opening": 0.0,
                "expected": expected,
                "counted": expected + (bank_diffs.get(pm.id) or 0.0),
                "diff_known": pm.id in bank_diffs,
            })
        return lines

    @api.model
    def _create_for_session(self, session, bank_diffs=None, cashier_name=None, source="live"):
        session = session.sudo()
        existing = self.sudo().search([("session_id", "=", session.id)], limit=1)
        if existing:
            return existing
        line_vals = self._prepare_lines(session, bank_diffs)
        if source == "live":
            for vals in line_vals:
                vals["diff_known"] = True
        return self.sudo().create({
            "name": session.name,
            "session_id": session.id,
            "config_id": session.config_id.id,
            "branch_name": session.config_id.name,
            "company_id": session.company_id.id,
            "currency_id": session.currency_id.id,
            "user_id": session.user_id.id,
            "cashier_name": cashier_name or session.user_id.name,
            "start_at": session.start_at,
            "stop_at": session.stop_at or fields.Datetime.now(),
            "source": source,
            "line_ids": [(0, 0, v) for v in line_vals],
        })

    @api.model
    def _generate_for_sessions(self, sessions, source="retro"):
        done = self.sudo().search([("session_id", "in", sessions.ids)]).session_id
        created = self.browse()
        for session in (sessions - done).filtered(lambda s: s.state == "closed"):
            created |= self._create_for_session(
                session, cashier_name=session._closing_cashier_guess(), source=source
            )
        return created

    def _selection_label(self, field_name):
        """Translated label of a selection value, in the current user's language."""
        selection = dict(self._fields[field_name]._description_selection(self.env))
        return selection.get(self[field_name], "")

    def get_receipt_data(self, lang_mode=None):
        """Data of the cashier closing report (POS receipt and PDF)."""
        self.ensure_one()
        return self.env["pos.closing.report.data"].cashier_report_data(self, lang_mode=lang_mode)

    def action_print(self):
        return self.env.ref("pos_closing_report.action_report_pos_closing").report_action(self)


class PosClosingReportLine(models.Model):
    _name = "pos.closing.report.line"
    _description = "POS Closing Report Line"

    report_id = fields.Many2one("pos.closing.report", "Closing Report", required=True, ondelete="cascade", index=True)
    currency_id = fields.Many2one(related="report_id.currency_id")
    payment_method_id = fields.Many2one("pos.payment.method", "Payment Method")
    name = fields.Char("Method Name")
    method_type = fields.Selection([("cash", "Cash"), ("bank", "Bank")], "Type")
    opening = fields.Monetary("Opening Balance")
    expected = fields.Monetary("Expected")
    counted = fields.Monetary("Counted")
    difference = fields.Monetary("Difference", compute="_compute_difference", store=True)
    diff_known = fields.Boolean("Counted by Cashier", default=True)

    @api.depends("expected", "counted")
    def _compute_difference(self):
        for line in self:
            line.difference = line.counted - line.expected
