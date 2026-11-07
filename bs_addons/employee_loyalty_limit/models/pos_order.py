from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PosOrder(models.Model):
    _inherit = "pos.order"

    def _employee_promotion_programs(self):
        self.ensure_one()
        return self.lines.reward_id.program_id.filtered(
            lambda program: program.for_employees and program.program_type == "promotion"
        )

    def _employee_promotion_usage_date(self):
        self.ensure_one()
        return fields.Date.context_today(
            self.with_context(tz=self.company_id.partner_id.tz or "UTC"),
            timestamp=self.date_order,
        )

    def _register_employee_promotion_usage(self, programs):
        self.ensure_one()
        usage_model = self.env["employee.promotion.usage"].sudo()
        usage_date = self._employee_promotion_usage_date()
        for program in programs:
            employee = program._employee_for_partner(self.partner_id, self.company_id)
            if not employee or usage_model.search_count([
                ("program_id", "=", program.id),
                ("employee_id", "=", employee.id),
                ("pos_order_id", "=", self.id),
            ]):
                continue
            usage_model.create({
                "employee_id": employee.id,
                "employee_name": employee.name,
                "partner_id": self.partner_id.id,
                "program_id": program.id,
                "pos_order_id": self.id,
                "usage_date": usage_date,
                "company_id": self.company_id.id,
            })

    @api.model
    def _process_order(self, order, existing_order):
        order_id = super()._process_order(order, existing_order)
        pos_order = self.browse(order_id)
        if pos_order.state not in ("paid", "done", "invoiced"):
            return order_id
        programs = pos_order._employee_promotion_programs()._lock_employee_promotions()
        usage_date = pos_order._employee_promotion_usage_date()
        for program in programs:
            program._validate_employee_promotion(
                pos_order.partner_id,
                usage_date,
                pos_order.company_id,
                exclude_order=pos_order,
            )
        pos_order._register_employee_promotion_usage(programs)
        return order_id

    @api.model
    def get_employee_promotion_status(self, partner_id, program_ids, config_id):
        partner = self.env["res.partner"].sudo().browse(partner_id).exists()
        config = self.env["pos.config"].sudo().browse(config_id).exists()
        programs = self.env["loyalty.program"].sudo().browse(program_ids).exists().filtered(
            lambda program: program.for_employees and program.program_type == "promotion"
        )
        usage_date = fields.Date.context_today(
            config.with_context(tz=config.company_id.partner_id.tz or "UTC")
        )
        usage = {}
        eligible = {str(program.id): False for program in programs}
        reached_program_ids = []
        employee = self.env["loyalty.program"]._employee_for_partner(
            partner, config.company_id
        )
        if employee:
            for program in programs:
                eligible[str(program.id)] = program._employee_matches_domain(employee)
                count = program._employee_usage_count(employee, usage_date)
                usage[str(program.id)] = count
                if count >= program.employee_limit:
                    reached_program_ids.append(program.id)
        return {
            "is_employee": bool(employee),
            "usage": usage,
            "eligible": eligible,
            "reached_program_ids": reached_program_ids,
        }

    @api.model
    def validate_employee_promotion_programs(self, partner_id, program_ids, config_id):
        partner = self.env["res.partner"].sudo().browse(partner_id).exists()
        config = self.env["pos.config"].sudo().browse(config_id).exists()
        programs = self.env["loyalty.program"].sudo().browse(program_ids).exists().filtered(
            lambda program: program.for_employees and program.program_type == "promotion"
        )
        try:
            employee = self.env["hr.employee"]
            usage = {}
            usage_date = fields.Date.context_today(
                config.with_context(tz=config.company_id.partner_id.tz or "UTC")
            )
            for program in programs:
                current_employee = program._validate_employee_promotion(
                    partner, usage_date, config.company_id
                )
                employee |= current_employee
                usage[str(program.id)] = program._employee_usage_count(
                    current_employee, usage_date
                )
        except ValidationError as error:
            return {"successful": False, "message": error.args[0]}
        return {"successful": True, "usage": usage, "employee_ids": employee.ids}

    def action_pos_order_cancel(self):
        result = super().action_pos_order_cancel()
        self.env["employee.promotion.usage"].sudo().search([
            ("pos_order_id", "in", self.ids)
        ]).unlink()
        return result
