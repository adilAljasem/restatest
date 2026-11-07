"""Load the retained addons without opening a database or contacting providers."""

import ast
import importlib
import sys
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

import requests


WORKSPACE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(WORKSPACE / "odoo"))

import odoo  # noqa: E402
from odoo.modules.module import initialize_sys_path  # noqa: E402

odoo.tools.config["addons_path"] = ",".join(
    str(WORKSPACE / folder) for folder in ("aggregator_connect", "odoo/addons")
)
initialize_sys_path()


class TestConnectorLoading(unittest.TestCase):
    def test_custom_dependencies_exist(self):
        root = WORKSPACE / "aggregator_connect"
        for manifest in root.glob("*/__manifest__.py"):
            metadata = ast.literal_eval(manifest.read_text(encoding="utf-8-sig"))
            for dependency in metadata.get("depends", []):
                if dependency.startswith("bs_pos_delivery_"):
                    with self.subTest(addon=manifest.parent.name, dependency=dependency):
                        self.assertTrue((root / dependency / "__manifest__.py").is_file())

    def test_feedus_loads(self):
        self._load("bs_pos_delivery_feedus")

    def test_blend_loads(self):
        self._load("bs_pos_delivery_blend")

    def test_deliverect_loads(self):
        self._load("bs_pos_delivery_deliverect")

    def test_retained_test_suites_load(self):
        for name in ("core", "feedus", "blend", "deliverect"):
            with self.subTest(addon=name):
                self._load("bs_pos_delivery_" + name + ".tests")

    def _load(self, name):
        try:
            importlib.import_module("odoo.addons." + name)
        except ImportError as error:
            self.fail(f"Retained addon {name} cannot load: {error}")


class TestDirectProviderTransport(unittest.TestCase):
    """Exercise real adapter/HTTP-client methods with only the network and ORM mocked."""

    @classmethod
    def setUpClass(cls):
        def model(addon, module, name):
            return getattr(importlib.import_module(
                f"odoo.addons.bs_pos_delivery_{addon}.models.{module}"
            ), name)

        cls.http_class = model("core", "delivery_http_client", "DeliveryHttpClient")
        cls.blend_class = model("blend", "delivery_adapter", "BlendDeliveryAdapter")
        cls.feedus_class = model("feedus", "delivery_adapter", "FeedusDeliveryAdapter")
        cls.deliverect_class = model("deliverect", "delivery_adapter", "DeliverectAdapter")
        cls.deliverect_webhook = model("deliverect", "webhook_service", "DeliverectWebhook")
        errors = importlib.import_module("odoo.addons.bs_pos_delivery_core.exceptions")
        cls.authentication_error = errors.DeliveryAuthenticationError
        cls.timeout_error = errors.DeliveryTimeoutError

    def setUp(self):
        class Environment(dict):
            context = {}
            lang = "en_US"

        self.log = Mock()
        self.logs = Mock()
        self.logs.start_attempt.return_value = self.log
        self.env = Environment({"bs.delivery.sync.log": self.logs})
        client = SimpleNamespace(
            env=self.env,
            response_payload=self.http_class.response_payload,
            duration_ms=self.http_class.duration_ms,
        )
        client.execute = MethodType(self.http_class.execute, client)
        self.env["bs.delivery.http.client"] = client
        self.connection = SimpleNamespace(ensure_one=Mock())
        # No test may accidentally reach a real provider.
        network = patch.object(requests.Session, "request", side_effect=AssertionError("Unexpected network request"))
        network.start()
        self.addCleanup(network.stop)

    @staticmethod
    def response(data=None):
        response = Mock(status_code=200)
        response.json.return_value = data or {"status": "success"}
        return response

    def test_blend_uses_core_transport_and_provider_bearer(self):
        connection = self.connection
        connection.access_token = "test-outbound-token"
        connection.blend_timeout_seconds = 15
        connection._blend_validate_url = Mock(side_effect=lambda url, **kw: url)
        adapter = SimpleNamespace(env=self.env, _headers=self.blend_class._headers)
        payload = {"event": "order_updated", "branch_id": "test-store"}
        with patch("requests.post", return_value=self.response()) as outgoing:
            result = self.blend_class._post(
                adapter, connection, "https://blend.invalid/status", payload,
                operation="blend_order_status", binding=False, external_id="test-order",
            )
        self.assertEqual(result, {"status": "success"})
        self.assertEqual(outgoing.call_args.kwargs["headers"]["Authorization"], "Bearer test-outbound-token")
        self.assertEqual(self.logs.start_attempt.call_args.kwargs["request_payload"], payload)
        connection._blend_validate_url.assert_called_once()
        self.log.mark_success.assert_called_once()

    def test_feedus_uses_core_transport_and_provider_key(self):
        connection = self.connection
        connection._feedus_resolved_status_url = lambda: "https://feedus.invalid/status"
        connection._feedus_secret = Mock(return_value="test-api-key")
        connection.feedus_timeout_seconds = 15
        binding = Mock(pos_order_id=True)
        binding.sudo.return_value = binding
        order = SimpleNamespace(_name="bs.delivery.order", binding_id=binding, external_order_id="test-order")
        with patch("requests.post", return_value=self.response()) as outgoing:
            result = self.feedus_class._send_status_update(
                SimpleNamespace(env=self.env), connection, order, "accepted",
            )
        self.assertTrue(result["success"])
        self.assertEqual(outgoing.call_args.kwargs["headers"]["api-key"], "test-api-key")
        self.assertEqual(self.logs.start_attempt.call_args.kwargs["request_payload"], {
            "order_id": "test-order", "status": "accepted",
        })
        binding.mark_sync_success.assert_called_once_with(external_status="accepted", odoo_status="accepted")
        self.log.mark_success.assert_called_once()

    def test_deliverect_uses_core_transport_and_oauth(self):
        connection = self.connection
        connection.provider_id = SimpleNamespace(code="deliverect")
        connection.base_url = "https://deliverect.invalid"
        connection._check_deliverect_url = Mock()
        adapter = SimpleNamespace(env=self.env, _token=Mock(return_value="test-oauth-token"))
        with patch("requests.request", return_value=self.response()) as outgoing:
            result = self.deliverect_class._request(
                adapter, connection, "deliverect_test_connection", "GET", "/accounts",
            )
        self.assertEqual(result, {"status": "success"})
        self.assertEqual(outgoing.call_args.args, ("GET", "https://deliverect.invalid/accounts"))
        self.assertEqual(outgoing.call_args.kwargs["headers"]["Authorization"], "Bearer test-oauth-token")
        connection._check_deliverect_url.assert_called_once()
        self.log.mark_success.assert_called_once()

    def test_direct_timeout_is_logged_and_mapped(self):
        def timeout():
            raise requests.Timeout()

        with self.assertRaises(self.timeout_error):
            self.env["bs.delivery.http.client"].execute(
                self.connection, request_callable=timeout, operation="test",
                request_url="https://provider.invalid", request_method="POST", provider_label="Test",
            )
        self.log.mark_failed.assert_called_once()

    def test_blend_inbound_token_is_still_required(self):
        adapter = SimpleNamespace(
            env=self.env, _ensure_blend_connection=Mock(), _bearer_token=self.blend_class._bearer_token,
            _single_store_for_payload=Mock(),
        )
        connection = SimpleNamespace(webhook_secret="test-inbound-token")
        with self.assertRaises(self.authentication_error):
            self.blend_class.verify_webhook_signature(adapter, connection, raw_body=b"{}", headers={})
        adapter._single_store_for_payload.assert_not_called()
        self.assertTrue(self.blend_class.verify_webhook_signature(
            adapter, connection, raw_body=b"{}", headers={"Authorization": "Bearer test-inbound-token"},
        ))
        adapter._single_store_for_payload.assert_called_once()

    def test_feedus_inbound_key_is_still_required(self):
        adapter = SimpleNamespace(
            env=self.env, _ensure_feedus_connection=Mock(), _header_value=self.feedus_class._header_value,
        )
        connection = SimpleNamespace(
            _feedus_secret=lambda name: "test-inbound-key",
            feedus_webhook_auth_method="api_key", feedus_api_key_header="api-key",
        )
        with self.assertRaises(self.authentication_error):
            self.feedus_class.verify_webhook_signature(adapter, connection, raw_body=b"{}", headers={})
        self.assertTrue(self.feedus_class.verify_webhook_signature(
            adapter, connection, raw_body=b"{}", headers={"api-key": "test-inbound-key"},
        ))

    def test_deliverect_inbound_token_is_still_required(self):
        service = SimpleNamespace(env=self.env)
        connection = SimpleNamespace(deliverect_webhook_auth="token", webhook_secret="test-inbound-token")
        with self.assertRaises(self.authentication_error):
            self.deliverect_webhook._authenticate(service, connection, b"{}", {})
        self.assertTrue(self.deliverect_webhook._authenticate(
            service, connection, b"{}", {"X-Deliverect-Auth-Token": "test-inbound-token"},
        ))


if __name__ == "__main__":
    unittest.main(verbosity=2)
