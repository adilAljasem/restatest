from ast import literal_eval
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class LoyaltyProgram(models.Model):
    _inherit = "loyalty.program"

    exclusive_customer = fields.Boolean(
        string="Exclusive Customer",
        help="Make this promotion available only to POS customers matching the customer domain.",
    )
    limit_for_customer = fields.Boolean(string="Limit for Customer")
    exclusive_limit = fields.Integer(string="Maximum Uses", default=1)
    exclusive_domain = fields.Char(
        string="Customer Domain", default="[]",
        help="Eligible customers. An empty domain includes all customers.",
    )
    exclusive_eligible_by_partner = fields.Json(
        compute="_compute_exclusive_usage_by_partner",
        help="Eligibility per customer for this promotion, without exposing exclusive details to POS.",
    )

    def _exclusive_domain_value(self):
        self.ensure_one()
        domain = literal_eval(self.exclusive_domain or "[]")
        if not isinstance(domain, list):
            raise ValueError("Exclusive Customer domain must be a list")
        return domain

    @api.constrains("exclusive_domain")
    def _check_exclusive_domain(self):
        for program in self:
            try:
                self.env["res.partner"].sudo().search(program._exclusive_domain_value(), limit=1)
            except (ValueError, TypeError, SyntaxError) as error:
                raise ValidationError(_("Invalid exclusive domain: %s", str(error))) from error

    def _exclusive_matches_domain(self, exclusive):
        self.ensure_one()
        return bool(exclusive and self.env["res.partner"].sudo().search_count(
            [("id", "=", exclusive.id)] + self._exclusive_domain_value(), limit=1
        ))
    exclusive_limit_period = fields.Selection(
        [("day", "Day"), ("week", "Week"), ("month", "Month"), ("year", "Year")],
        string="Per",
        default="day",
    )
    exclusive_usage_by_partner = fields.Json(
        compute="_compute_exclusive_usage_by_partner",
        help="Current-period usage counts loaded by the POS client.",
    )

    @api.constrains(
        "exclusive_customer", "program_type", "exclusive_limit", "exclusive_limit_period",
        "limit_usage", "limit_for_customer",
    )
    def _check_exclusive_limit(self):
        for program in self:
            if not program.exclusive_customer or program.program_type != "promotion":
                continue
            if program.limit_for_customer and program.exclusive_limit <= 0:
                raise ValidationError(_("The exclusive promotion limit must be greater than zero."))
            if not program.exclusive_limit_period:
                raise ValidationError(_("An exclusive promotion period is required."))
            if program.limit_usage:
                raise ValidationError(_(
                    "Standard Limit Usage cannot be combined with an exclusive promotion limit."
                ))

    @api.onchange("exclusive_customer", "program_type")
    def _onchange_exclusive_promotion(self):
        for program in self:
            if program.exclusive_customer and program.program_type == "promotion":
                program.limit_usage = False

    def _exclusive_period_bounds(self, usage_date=None):
        self.ensure_one()
        usage_date = fields.Date.to_date(usage_date or fields.Date.context_today(self))
        if self.exclusive_limit_period == "week":
            anchor = self.date_from or (usage_date - timedelta(days=(usage_date.weekday() - 5) % 7))
            start = anchor + timedelta(days=((usage_date - anchor).days // 7) * 7)
            end = start + timedelta(days=7)
        elif self.exclusive_limit_period == "year":
            start = usage_date.replace(month=1, day=1)
            end = start.replace(year=start.year + 1)
        elif self.exclusive_limit_period == "month":
            start = usage_date.replace(day=1)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            start = usage_date
            end = start + timedelta(days=1)
        return start, end

    @api.model
    def _exclusive_for_partner(self, partner, company=None):
        return partner

    def _exclusive_usage_count(self, exclusive, usage_date=None, exclude_order=None):
        self.ensure_one()
        if not exclusive:
            return 0
        start, end = self._exclusive_period_bounds(usage_date)
        domain = [
            ("partner_id", "=", exclusive.id),
            ("program_id", "=", self.id),
            ("usage_date", ">=", start),
            ("usage_date", "<", end),
        ]
        if exclude_order:
            domain.append(("pos_order_id", "!=", exclude_order.id))
        return self.env["exclusive.promotion.usage"].sudo().search_count(domain)

    def _validate_exclusive_promotion(self, partner, usage_date=None, company=None, exclude_order=None):
        self.ensure_one()
        if not self.exclusive_customer or self.program_type != "promotion":
            return self.env["res.partner"]
        if self.date_from and usage_date < self.date_from or self.date_to and usage_date > self.date_to:
            raise ValidationError(_("Promotion is outside its validity dates."))
        exclusive = self._exclusive_for_partner(partner, company)
        if not exclusive:
            raise ValidationError(_("Select a customer to use this exclusive promotion."))
        if not self._exclusive_matches_domain(exclusive):
            raise ValidationError(_("This customer does not match the promotion conditions."))
        if self.limit_for_customer and self._exclusive_usage_count(exclusive, usage_date, exclude_order) >= self.exclusive_limit:
            raise ValidationError(_("Exclusive Customer discount limit has been reached for this period."))
        return exclusive

    def _lock_exclusive_promotions(self):
        programs = self.filtered(lambda p: p.exclusive_customer and p.program_type == "promotion")
        if programs:
            self.env.cr.execute(
                "SELECT id FROM loyalty_program WHERE id IN %s ORDER BY id FOR UPDATE",
                [tuple(programs.ids)],
            )
            self.env.cr.execute("UPDATE loyalty_program SET write_date = clock_timestamp() WHERE id IN %s", [tuple(programs.ids)])
        return programs

    def _compute_exclusive_usage_by_partner(self):
        for program in self:
            program.exclusive_usage_by_partner = {}
            program.exclusive_eligible_by_partner = {}

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + [
            "exclusive_customer",
            "exclusive_limit",
            "limit_for_customer",
            "exclusive_limit_period",
            "exclusive_usage_by_partner",
            "exclusive_eligible_by_partner",
        ]

    @api.model
    def _load_pos_data_search_read(self, data, config):
        partner_ids = [partner["id"] for partner in data.get("res.partner", [])]
        programs = self.with_context(
            exclusive_usage_partner_ids=tuple(partner_ids),
            exclusive_usage_company_id=config.company_id.id,
        )
        return super(LoyaltyProgram, programs)._load_pos_data_search_read(data, config)
