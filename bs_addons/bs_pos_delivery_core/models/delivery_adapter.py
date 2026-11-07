from odoo import _, models

from ..exceptions import DeliveryUnsupportedOperation


class DeliveryAdapter(models.AbstractModel):
    """Stateless contract implemented by every delivery-provider addon.

    Provider addons create an abstract model named
    ``bs.delivery.adapter.<provider_code>`` and inherit this model.  Connection
    records are passed explicitly so an adapter never stores tenant state.
    """

    _name = "bs.delivery.adapter"
    _description = "Delivery Provider Adapter"

    OPERATIONS = frozenset(
        {
            "test_connection",
            "fetch_oauth_token",
            "send_order",
            "cancel_order",
            "update_order",
            "get_order_status",
            "pull_menu",
            "push_menu",
            "sync_menu",
            "sync_products",
            "update_product_availability",
            "update_store_availability",
            "sync_categories",
            "sync_prices",
            "accept_order",
            "reject_order",
            "mark_order_preparing",
            "mark_order_ready",
            "verify_webhook_signature",
            "get_webhook_event_metadata",
            "process_webhook",
        }
    )

    def get_capabilities(self):
        """Return the operations implemented by this adapter."""
        return frozenset()

    def supports(self, operation):
        return operation in self.get_capabilities()

    def supports_order_state(self, state):
        """Optional state-level capability; old adapters retain their behavior."""
        return True

    def _unsupported(self, connection, operation):
        raise DeliveryUnsupportedOperation(
            _("%(provider)s does not support the %(operation)s operation.")
            % {
                "provider": connection.provider_id.display_name,
                "operation": operation,
            }
        )

    def test_connection(self, connection):
        return self._unsupported(connection, "test_connection")

    def send_order(self, connection, *args, **kwargs):
        return self._unsupported(connection, "send_order")

    def cancel_order(self, connection, *args, **kwargs):
        return self._unsupported(connection, "cancel_order")

    def update_order(self, connection, *args, **kwargs):
        return self._unsupported(connection, "update_order")

    def get_order_status(self, connection, *args, **kwargs):
        return self._unsupported(connection, "get_order_status")

    def sync_menu(self, connection, *args, **kwargs):
        """Legacy menu operation kept for connector compatibility.

        New integrations must expose the direction explicitly through
        :meth:`pull_menu` and/or :meth:`push_menu`.  Core never resolves a
        push job to this ambiguous operation.
        """
        return self._unsupported(connection, "sync_menu")

    def pull_menu(self, connection, *args, **kwargs):
        return self._unsupported(connection, "pull_menu")

    def push_menu(self, connection, *, store_binding=None, **kwargs):
        return self._unsupported(connection, "push_menu")

    def update_product_availability(self, connection, products, is_available, **kwargs):
        return self._unsupported(connection, "update_product_availability")

    def update_store_availability(self, connection, store_binding, is_available, **kwargs):
        return self._unsupported(connection, "update_store_availability")

    def sync_products(self, connection, products, **kwargs):
        return self._unsupported(connection, "sync_products")

    def sync_categories(self, connection, *args, **kwargs):
        return self._unsupported(connection, "sync_categories")

    def sync_prices(self, connection, *args, **kwargs):
        return self._unsupported(connection, "sync_prices")

    def accept_order(self, connection, *args, **kwargs):
        return self._unsupported(connection, "accept_order")

    def reject_order(self, connection, *args, **kwargs):
        return self._unsupported(connection, "reject_order")

    def mark_order_preparing(self, connection, *args, **kwargs):
        return self._unsupported(connection, "mark_order_preparing")

    def mark_order_ready(self, connection, *args, **kwargs):
        return self._unsupported(connection, "mark_order_ready")

    def verify_webhook_signature(self, connection, *args, **kwargs):
        """Validate a webhook using provider-specific credentials.

        Adapters must return ``True`` only after authenticating the original
        request body. An adapter for a provider without signed webhooks may
        implement its documented authentication mechanism here instead.
        """
        return self._unsupported(connection, "verify_webhook_signature")

    def get_webhook_event_metadata(self, connection, *args, **kwargs):
        """Return event_id, event_type, external_id, and optional idempotency_key."""
        return self._unsupported(connection, "get_webhook_event_metadata")

    def process_webhook(self, connection, *args, **kwargs):
        return self._unsupported(connection, "process_webhook")
