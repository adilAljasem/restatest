from odoo import fields, models


RECURRING_PURCHASE_ORDER_PARAMETER = (
    "bs_recurring_purchase_agreement.recurring_purchase_order"
)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bs_recurring_purchase_order_enabled = fields.Boolean(
        string="Recurring Purchase Order",
        config_parameter=RECURRING_PURCHASE_ORDER_PARAMETER,
    )

    def set_values(self):
        super().set_values()
        self.ensure_one()
        cron = self.env.ref(
            "bs_recurring_purchase_agreement.ir_cron_recurring_purchase_orders"
        ).sudo()
        enabled = self.bs_recurring_purchase_order_enabled
        values = {"active": enabled}
        if enabled and not cron.active:
            values["nextcall"] = fields.Datetime.now()
        cron.write(values)
