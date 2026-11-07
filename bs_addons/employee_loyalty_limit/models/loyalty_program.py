from ast import literal_eval
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class LoyaltyProgram(models.Model):
    _inherit = "loyalty.program"

    for_employees = fields.Boolean(
        string="For Employees",
        help="Make this promotion available only to POS customers linked to an employee.",
    )
    employee_limit = fields.Integer(string="Limit", default=1)
    employee_domain = fields.Char(
        string="Employee Domain", default="[]",
        help="Eligible employees, for example by department or employee tags. An empty domain includes all employees.",
    )
    employee_eligible_by_partner = fields.Json(
        compute="_compute_employee_usage_by_partner",
        help="Eligibility per customer for this promotion, without exposing employee details to POS.",
    )

    def _employee_domain_value(self):
        self.ensure_one()
        domain = literal_eval(self.employee_domain or "[]")
        if not isinstance(domain, list):
            raise ValueError("Employee domain must be a list")
        return domain

    @api.constrains("employee_domain")
    def _check_employee_domain(self):
        for program in self:
            try:
                self.env["hr.employee"].sudo().search(program._employee_domain_value(), limit=1)
            except (ValueError, TypeError, SyntaxError) as error:
                raise ValidationError(_("Invalid employee domain: %s", str(error))) from error

    def _employee_matches_domain(self, employee):
        self.ensure_one()
        return bool(employee and self.env["hr.employee"].sudo().search_count(
            [("id", "=", employee.id)] + self._employee_domain_value(), limit=1
        ))
    employee_limit_period = fields.Selection(
        [("day", "Day"), ("week", "Week"), ("month", "Month")],
        string="Per",
        default="day",
    )
    employee_usage_by_partner = fields.Json(
        compute="_compute_employee_usage_by_partner",
        help="Current-period usage counts loaded by the POS client.",
    )

    @api.constrains(
        "for_employees", "program_type", "employee_limit", "employee_limit_period",
        "limit_usage",
    )
    def _check_employee_limit(self):
        for program in self:
            if not program.for_employees or program.program_type != "promotion":
                continue
            if program.employee_limit <= 0:
                raise ValidationError(_("The employee promotion limit must be greater than zero."))
            if not program.employee_limit_period:
                raise ValidationError(_("An employee promotion period is required."))
            if program.limit_usage:
                raise ValidationError(_(
                    "Standard Limit Usage cannot be combined with an employee promotion limit."
                ))

    @api.onchange("for_employees", "program_type")
    def _onchange_employee_promotion(self):
        for program in self:
            if program.for_employees and program.program_type == "promotion":
                program.limit_usage = False

    def _employee_period_bounds(self, usage_date=None):
        self.ensure_one()
        usage_date = fields.Date.to_date(usage_date or fields.Date.context_today(self))
        if self.employee_limit_period == "week":
            start = usage_date - timedelta(days=usage_date.weekday())
            end = start + timedelta(days=7)
        elif self.employee_limit_period == "month":
            start = usage_date.replace(day=1)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            start = usage_date
            end = start + timedelta(days=1)
        return start, end

    @api.model
    def _employee_for_partner(self, partner, company=None):
        if not partner:
            return self.env["hr.employee"]
        domain = [("work_contact_id", "=", partner.id)]
        if company:
            employee = self.env["hr.employee"].sudo().search(
                domain + [("company_id", "=", company.id)], limit=1
            )
            if employee:
                return employee
        return self.env["hr.employee"].sudo().search(domain, limit=1)

    def _employee_usage_count(self, employee, usage_date=None, exclude_order=None):
        self.ensure_one()
        if not employee:
            return 0
        start, end = self._employee_period_bounds(usage_date)
        domain = [
            ("employee_id", "=", employee.id),
            ("program_id", "=", self.id),
            ("usage_date", ">=", start),
            ("usage_date", "<", end),
        ]
        if exclude_order:
            domain.append(("pos_order_id", "!=", exclude_order.id))
        return self.env["employee.promotion.usage"].sudo().search_count(domain)

    def _validate_employee_promotion(self, partner, usage_date=None, company=None, exclude_order=None):
        self.ensure_one()
        if not self.for_employees or self.program_type != "promotion":
            return self.env["hr.employee"]
        employee = self._employee_for_partner(partner, company)
        if not employee:
            raise ValidationError(_("This promotion is available only to employees."))
        if not self._employee_matches_domain(employee):
            raise ValidationError(_("This employee does not match the promotion's employee conditions."))
        if self._employee_usage_count(employee, usage_date, exclude_order) >= self.employee_limit:
            raise ValidationError(_("Employee discount limit has been reached for this period."))
        return employee

    def _lock_employee_promotions(self):
        programs = self.filtered(lambda p: p.for_employees and p.program_type == "promotion")
        if programs:
            self.env.cr.execute(
                "SELECT id FROM loyalty_program WHERE id IN %s ORDER BY id FOR UPDATE",
                [tuple(programs.ids)],
            )
        return programs

    @api.depends("for_employees", "employee_limit", "employee_limit_period", "employee_domain")
    @api.depends_context("employee_usage_partner_ids", "employee_usage_company_id")
    def _compute_employee_usage_by_partner(self):
        partner_ids = self.env.context.get("employee_usage_partner_ids", [])
        company = self.env["res.company"].browse(
            self.env.context.get("employee_usage_company_id")
        )
        partners = self.env["res.partner"].sudo().browse(partner_ids).exists()
        employees = self.env["hr.employee"].sudo().search([
            ("work_contact_id", "in", partners.ids),
        ])
        employee_by_partner = {}
        for employee in employees:
            partner_id = employee.work_contact_id.id
            current = employee_by_partner.get(partner_id)
            if not current or (company and employee.company_id == company):
                employee_by_partner[partner_id] = employee
        usage_date = fields.Date.context_today(
            self.with_context(tz=company.partner_id.tz or "UTC")
        ) if company else fields.Date.context_today(self)
        for program in self:
            usage = {}
            eligible = {}
            if program.for_employees and program.program_type == "promotion":
                matching_ids = set(self.env["hr.employee"].sudo().search(
                    [("id", "in", [e.id for e in employee_by_partner.values()])]
                    + program._employee_domain_value()
                ).ids)
                eligible = {
                    str(partner_id): employee.id in matching_ids
                    for partner_id, employee in employee_by_partner.items()
                }
                start, end = program._employee_period_bounds(usage_date)
                counts = self.env["employee.promotion.usage"].sudo()._read_group(
                    [
                        ("program_id", "=", program.id),
                        ("employee_id", "in", [e.id for e in employee_by_partner.values()]),
                        ("usage_date", ">=", start),
                        ("usage_date", "<", end),
                    ],
                    ["employee_id"],
                    ["__count"],
                )
                count_by_employee = {employee.id: count for employee, count in counts}
                usage = {
                    str(partner_id): count_by_employee.get(employee.id, 0)
                    for partner_id, employee in employee_by_partner.items()
                }
            program.employee_usage_by_partner = usage
            program.employee_eligible_by_partner = eligible

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + [
            "for_employees",
            "employee_limit",
            "employee_limit_period",
            "employee_usage_by_partner",
            "employee_eligible_by_partner",
        ]

    @api.model
    def _load_pos_data_search_read(self, data, config):
        partner_ids = [partner["id"] for partner in data.get("res.partner", [])]
        programs = self.with_context(
            employee_usage_partner_ids=tuple(partner_ids),
            employee_usage_company_id=config.company_id.id,
        )
        return super(LoyaltyProgram, programs)._load_pos_data_search_read(data, config)
