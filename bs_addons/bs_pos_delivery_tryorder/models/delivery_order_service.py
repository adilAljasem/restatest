from odoo import api, models


class TryOrderDeliveryOrderService(models.AbstractModel):
    _inherit = "bs.delivery.order.service"

    @api.model
    def _connection_allows_provider_event(self, connection):
        if super()._connection_allows_provider_event(connection):
            return True
        return bool(
            self.env.context.get("bs_tryorder_draft_test_connection_id") == connection.id
            and connection.active
            and connection.provider_id.code == "tryorder"
            and connection.state == "draft"
            and connection.environment == "test"
            and connection.allow_draft_api_testing
        )
