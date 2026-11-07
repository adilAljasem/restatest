from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PosOrder(models.Model):
    _inherit = "pos.order"

    def _exclusive_promotion_programs(self):
        self.ensure_one()
        return self.lines.reward_id.program_id.filtered(
            lambda program: program.exclusive_customer and program.program_type == "promotion"
        )

    def _exclusive_promotion_usage_date(self):
        self.ensure_one()
        return fields.Date.context_today(
            self.with_context(tz=self.company_id.partner_id.tz or "UTC"),
            timestamp=fields.Datetime.now(),
        )

    def _register_exclusive_promotion_usage(self, programs):
        self.ensure_one()
        usage_model = self.env["exclusive.promotion.usage"].sudo()
        usage_date = self._exclusive_promotion_usage_date()
        for program in programs:
            exclusive = program._exclusive_for_partner(self.partner_id, self.company_id)
            if not exclusive or usage_model.search_count([
                ("program_id", "=", program.id),
                ("partner_id", "=", exclusive.id),
                ("pos_order_id", "=", self.id),
            ]):
                continue
            discount = -sum(self.lines.filtered(lambda line: line.reward_id.program_id == program).mapped("price_subtotal_incl"))
            before = self.amount_total + discount
            usage_model.create({
                "amount_before": before,
                "amount_after": self.amount_total,
                "discount_amount": discount,
                "discount_percentage": discount / before * 100 if before else 0,
                "partner_id": self.partner_id.id,
                "program_id": program.id,
                "pos_order_id": self.id,
                "usage_date": usage_date,
                "company_id": self.company_id.id,
            })

    def action_pos_order_paid(self):
        for order in self:
            programs = order._exclusive_promotion_programs()._lock_exclusive_promotions()
            for program in programs:
                if (program.company_id and program.company_id != order.company_id
                        or not program.pos_ok
                        or program.pos_config_ids and order.config_id not in program.pos_config_ids):
                    raise ValidationError(_("This promotion is not available in this POS."))
                lines = order.lines.filtered(lambda line: line.reward_id.program_id == program)
                codes = set(lines.mapped("reward_identifier_code"))
                if len(codes) > 1:
                    raise ValidationError(_("The same exclusive promotion cannot be applied twice."))
                program._validate_exclusive_promotion(
                    order.partner_id, order._exclusive_promotion_usage_date(),
                    order.company_id, exclude_order=order,
                )
        result = super().action_pos_order_paid()
        for order in self.filtered(lambda order: order.state in ("paid", "done", "invoiced")):
            order._register_exclusive_promotion_usage(order._exclusive_promotion_programs())
        return result

    @api.model
    def get_exclusive_promotion_status(self, partner_id, program_ids, config_id):
        partner = self.env["res.partner"].browse(partner_id).exists()
        config = self.env["pos.config"].browse(config_id).exists()
        config.check_access("read")
        partner.check_access("read")
        if not config or config.company_id not in self.env.companies:
            raise ValidationError(_("Invalid POS configuration."))
        programs = self.env["loyalty.program"].browse(program_ids).exists().filtered(
            lambda program: program.exclusive_customer and program.program_type == "promotion"
        )
        usage_date = fields.Date.context_today(
            config.with_context(tz=config.company_id.partner_id.tz or "UTC")
        )
        usage = {}
        eligible = {str(program.id): False for program in programs}
        reached_program_ids = []
        exclusive = self.env["loyalty.program"]._exclusive_for_partner(
            partner, config.company_id
        )
        if exclusive:
            for program in programs:
                eligible[str(program.id)] = (
                    program._exclusive_matches_domain(exclusive)
                    and (not program.date_from or usage_date >= program.date_from)
                    and (not program.date_to or usage_date <= program.date_to)
                )
                count = program._exclusive_usage_count(exclusive, usage_date)
                usage[str(program.id)] = count
                if program.limit_for_customer and count >= program.exclusive_limit:
                    reached_program_ids.append(program.id)
        return {
            "is_exclusive": bool(exclusive),
            "usage": usage,
            "eligible": eligible,
            "reached_program_ids": reached_program_ids,
        }

    @api.model
    def validate_exclusive_promotion_programs(self, partner_id, program_ids, config_id):
        partner = self.env["res.partner"].browse(partner_id).exists()
        config = self.env["pos.config"].browse(config_id).exists()
        config.check_access("read")
        partner.check_access("read")
        if not config or config.company_id not in self.env.companies:
            raise ValidationError(_("Invalid POS configuration."))
        programs = self.env["loyalty.program"].browse(program_ids).exists().filtered(
            lambda program: program.exclusive_customer and program.program_type == "promotion"
        )
        try:
            exclusive = self.env["res.partner"]
            usage = {}
            usage_date = fields.Date.context_today(
                config.with_context(tz=config.company_id.partner_id.tz or "UTC")
            )
            for program in programs:
                current_exclusive = program._validate_exclusive_promotion(
                    partner, usage_date, config.company_id
                )
                exclusive |= current_exclusive
                usage[str(program.id)] = program._exclusive_usage_count(
                    current_exclusive, usage_date
                )
        except ValidationError as error:
            return {"successful": False, "message": error.args[0]}
        return {"successful": True, "usage": usage, "exclusive_ids": exclusive.ids}

    def action_pos_order_cancel(self):
        result = super().action_pos_order_cancel()
        self.env["exclusive.promotion.usage"].sudo().search([
            ("pos_order_id", "in", self.ids)
        ]).unlink()
        return result
