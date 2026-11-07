from odoo.exceptions import UserError


class DeliveryIntegrationError(UserError):
    """Base error safe to display to an integration administrator."""

    code = "integration_error"
    retryable = False

    def __init__(self, message, *, code=None, retryable=None):
        super().__init__(message)
        self.code = code or self.code
        if retryable is not None:
            self.retryable = retryable


class DeliveryUnsupportedOperation(DeliveryIntegrationError):
    """Raised when an adapter is unavailable or does not implement an operation."""

    code = "unsupported_operation"


class DeliveryAuthenticationError(DeliveryIntegrationError):
    """Raised when provider credentials or a webhook signature are invalid."""

    code = "authentication_error"


class DeliveryWebhookValidationError(DeliveryIntegrationError):
    """Raised when a webhook cannot be parsed or lacks required metadata."""

    code = "validation_error"


class DeliveryWebhookConflictError(DeliveryIntegrationError):
    """Raised when an idempotency key is reused for different content."""

    code = "idempotency_conflict"


class DeliveryWebhookNotFoundError(DeliveryIntegrationError):
    """Raised when a webhook token does not resolve to an enabled connection."""

    code = "webhook_not_found"


class DeliveryNetworkError(DeliveryIntegrationError):
    """Raised for retryable network failures."""

    code = "network_error"
    retryable = True


class DeliveryTimeoutError(DeliveryIntegrationError):
    """Raised for retryable provider timeouts."""

    code = "timeout_error"
    retryable = True


class DeliveryProviderServerError(DeliveryIntegrationError):
    """Raised when a provider returns a retryable server-side failure."""

    code = "provider_server_error"
    retryable = True


class DeliveryBusinessError(DeliveryIntegrationError):
    """Raised for a valid request that cannot be applied to the business state."""

    code = "business_error"
