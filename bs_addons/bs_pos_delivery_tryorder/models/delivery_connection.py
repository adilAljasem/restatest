from odoo import api, fields, models


class DeliveryConnection(models.Model):
    _inherit = "bs.delivery.connection"

    is_tryorder_provider = fields.Boolean(compute="_compute_is_tryorder_provider")
    allow_draft_api_testing = fields.Boolean(
        string="Allow TryOrder API Testing While Draft",
        groups="bs_pos_delivery_core.group_delivery_integration_manager",
        help=(
            "Accept authenticated TryOrder API requests while this connection is "
            "Draft and its environment is Test. Order requests can create real "
            "Odoo and POS orders in this database."
        ),
    )

    @api.depends("provider_id.code")
    def _compute_is_tryorder_provider(self):
        for connection in self:
            connection.is_tryorder_provider = connection.provider_id.code == "tryorder"

    def _is_pos_board_eligible(self):
        self.ensure_one()
        return super()._is_pos_board_eligible() or bool(
            self.provider_id.code == "tryorder"
            and self.active
            and self.state == "draft"
            and self.environment == "test"
            and self.allow_draft_api_testing
        )
