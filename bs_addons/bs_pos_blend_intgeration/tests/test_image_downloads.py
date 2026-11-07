"""Inbound image tests: DNS and HTTP are simulated, never external traffic."""

import base64
import io
import socket
from copy import deepcopy
from unittest.mock import patch

import requests
from PIL import Image

from odoo.tests.common import _super_send, tagged

from ..services import menu
from ..services.webhook import BlendWebhookService
from .common import BlendTransactionCase, sample_menu


def image_bytes(image_format="PNG", color="blue"):
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(stream, format=image_format)
    return stream.getvalue()


class ImageStream(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.read_count = 0

    def read(self, *args, **kwargs):
        self.read_count += 1
        return super().read(*args, **kwargs)

    def release_conn(self):
        # Mimic urllib3's release hook, also called after complete consumption.
        self.close()


def image_response(data=b"", status=200, content_type="image/png", headers=None):
    response = requests.Response()
    response.status_code = status
    response.headers.update({"Content-Type": content_type, **(headers or {})})
    response.raw = ImageStream(data)
    response.url = "https://images.example.test/image.png"
    return response


def dns_results(*addresses):
    return [
        (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
        for address in addresses
    ]


@tagged("post_install", "-at_install")
class TestBlendImageDownloads(BlendTransactionCase):
    url = "https://images.example.test/product.png?signature=PRIVATE-SIGNATURE"

    def setUp(self):
        super().setUp()
        self.service = menu.BlendMenuService(self.env, self.config)

    def _download(self, response, *, url=None):
        with patch.object(menu.socket, "getaddrinfo", return_value=dns_results("93.184.216.34")), \
                patch.object(menu.requests, "get", return_value=response) as get:
            result = self.service._download_image(self.url if url is None else url)
        return result, get

    def _product(self, config=None):
        return self.env["product.template"].search([
            ("blend_config_id", "=", (config or self.config).id), ("blend_external_id", "=", "100"),
        ])

    def test_valid_images_are_downloaded_and_connections_closed(self):
        formats = ["JPEG", "PNG", "GIF", "BMP"]
        if menu.WebPImagePlugin.SUPPORTED:
            formats.append("WEBP")
        for image_format in formats:
            with self.subTest(image_format=image_format):
                data = image_bytes(image_format)
                response = image_response(data, content_type="image/%s" % image_format.lower())
                result, get = self._download(response)
                if image_format != "BMP":
                    self.assertEqual(base64.b64decode(result), data)
                else:
                    # Odoo's normal image processor converts BMP to PNG.
                    with Image.open(io.BytesIO(base64.b64decode(result))) as decoded:
                        self.assertEqual(decoded.format, "PNG")
                        self.assertEqual(decoded.size, (8, 8))
                        self.assertEqual(decoded.convert("RGB").getpixel((0, 0)), (0, 0, 255))
                self.assertTrue(response.raw.closed)
                self.assertIs(get.call_args.kwargs.get("allow_redirects"), False)
                self.assertTrue(get.call_args.kwargs["stream"])
                self.assertNotIn("Authorization", get.call_args.kwargs.get("headers", {}))

    def test_requests_does_not_follow_redirect_to_private_or_public_destination(self):
        for destination in ("https://127.0.0.1/internal", "https://cdn.example.test/image.png"):
            for status in (301, 302, 303, 307, 308):
                with self.subTest(destination=destination, status=status):
                    responses = []

                    def send(request, **kwargs):
                        response = image_response(
                            image_bytes(), status=status if request.url == self.url else 200,
                            headers={"Location": destination} if request.url == self.url else {},
                        )
                        response.request = request
                        response.url = request.url
                        responses.append(response)
                        return response

                    # Use Requests' real redirect machinery; replace only the
                    # HTTP adapter so there is no network, even on regressions.
                    # Restore the native Session.send only while ALL transport
                    # calls are mocked, bypassing Odoo's external-network guard.
                    with patch.object(menu.socket, "getaddrinfo", return_value=dns_results("93.184.216.34")), \
                            patch.object(requests.adapters.HTTPAdapter, "send", side_effect=send) as http, \
                            patch.object(requests.sessions.Session, "send", _super_send):
                        self.assertFalse(self.service._download_image(self.url))
                    self.assertEqual(http.call_count, 1)
                    self.assertTrue(all(response.raw.closed for response in responses))
                    self.assertTrue(all(response.raw.read_count == 0 for response in responses))

    def test_all_non_200_responses_are_rejected_and_closed(self):
        for status in (201, 204, 206, 300, 304, 305, 399, 400, 401, 404, 500):
            with self.subTest(status=status):
                response = image_response(image_bytes(), status=status)
                result, _get = self._download(response)
                self.assertFalse(result)
                self.assertTrue(response.raw.closed)

    def test_malformed_or_credentialed_urls_do_not_reach_dns_or_http(self):
        for url in (None, 7, {"url": self.url}, "", "file:///etc/passwd", "http://example.test/x",
                    "https:///missing-host", "https://[invalid", "https://example.test:invalid/x",
                    "https://user:secret@example.test/image", "https://example.test/a\n.png"):
            with self.subTest(url=url), patch.object(menu.socket, "getaddrinfo") as dns, \
                    patch.object(menu.requests, "get") as get:
                self.assertFalse(self.service._download_image(url))
                dns.assert_not_called()
                get.assert_not_called()

    def test_non_public_ipv4_ipv6_and_mixed_dns_answers_are_blocked(self):
        for address in ("127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254",
                        "0.0.0.0", "224.0.0.1", "100.64.0.1", "::1", "::", "fe80::1", "fd00::1",
                        "ff02::1", "::ffff:127.0.0.1"):
            for mixed in (False, True):
                with self.subTest(address=address, mixed=mixed):
                    addresses = ("93.184.216.34", address) if mixed else (address,)
                    with patch.object(menu.socket, "getaddrinfo", return_value=dns_results(*addresses)), \
                            patch.object(menu.requests, "get") as get:
                        self.assertFalse(self.service._download_image(self.url))
                        get.assert_not_called()

    def test_public_ipv6_is_allowed(self):
        data = image_bytes()
        response = image_response(data)
        with patch.object(menu.socket, "getaddrinfo", return_value=dns_results("2606:4700:4700::1111")), \
                patch.object(menu.requests, "get", return_value=response):
            self.assertEqual(base64.b64decode(self.service._download_image(self.url)), data)

    def test_dns_failure_or_empty_answer_does_not_break_the_sync(self):
        for result in ([], dns_results("invalid-ip"), socket.gaierror("no DNS"), OSError("DNS unavailable")):
            with self.subTest(result=result), patch.object(menu.requests, "get") as get:
                options = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
                with patch.object(menu.socket, "getaddrinfo", **options):
                    self.assertFalse(self.service._download_image(self.url))
                get.assert_not_called()

    def test_wrong_mime_and_disguised_non_images_are_rejected(self):
        cases = [
            (image_bytes(), "text/html"), (image_bytes(), "application/octet-stream"),
            (b"<html>not an image</html>", "image/jpeg"), (b"not an image", "image/png"),
            (b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>', "image/svg+xml"),
            (b"", "image/png"), (image_bytes()[:25], "image/png"),
        ]
        for data, content_type in cases:
            with self.subTest(content_type=content_type, data_length=len(data)):
                response = image_response(data, content_type=content_type)
                result, _get = self._download(response)
                self.assertFalse(result)
                self.assertTrue(response.raw.closed)

    def test_content_type_parameters_do_not_reject_valid_images(self):
        result, _get = self._download(image_response(image_bytes(), content_type="IMAGE/PNG; charset=binary"))
        self.assertTrue(result)

    def test_size_limit_is_enforced_on_actual_stream_not_just_header(self):
        data = image_bytes()
        with patch.object(self.service, "MAX_IMAGE_BYTES", len(data)):
            response = image_response(data)
            self.assertTrue(self._download(response)[0])
            for headers in ({}, {"Content-Length": "1"}):
                response = image_response(data + b"x", headers=headers)
                self.assertFalse(self._download(response)[0])
                self.assertTrue(response.raw.closed)

    def test_oversized_content_length_is_rejected_before_reading(self):
        response = image_response(image_bytes(), headers={"Content-Length": str(self.service.MAX_IMAGE_BYTES + 1)})
        with patch.object(response, "iter_content", wraps=response.iter_content) as read:
            self.assertFalse(self._download(response)[0])
            read.assert_not_called()
        self.assertTrue(response.raw.closed)

    def test_bad_content_length_is_rejected_without_an_exception(self):
        for length in ("-1", "invalid"):
            with self.subTest(length=length):
                response = image_response(image_bytes(), headers={"Content-Length": length})
                self.assertFalse(self._download(response)[0])
                self.assertTrue(response.raw.closed)

    def test_network_errors_do_not_log_signed_urls_or_tokens(self):
        for error in (requests.Timeout, requests.ConnectionError, requests.exceptions.SSLError):
            with self.subTest(error=error.__name__), \
                    patch.object(menu.socket, "getaddrinfo", return_value=dns_results("93.184.216.34")), \
                    patch.object(menu.requests, "get", side_effect=error(self.url)):
                with self.assertLogs(menu._logger, level="WARNING") as captured:
                    self.assertFalse(self.service._download_image(self.url))
                output = "\n".join(captured.output)
                self.assertNotIn("PRIVATE-SIGNATURE", output)
                self.assertNotIn(self.url, output)
                self.assertIn(str(self.config.id), output)

    def test_stream_error_closes_response(self):
        response = image_response(image_bytes())
        with patch.object(response, "iter_content", side_effect=requests.exceptions.ChunkedEncodingError(self.url)):
            self.assertFalse(self._download(response)[0])
        self.assertTrue(response.raw.closed)

    def test_timeout_remains_bounded(self):
        self.config.blend_timeout = 120
        _result, get = self._download(image_response(image_bytes()))
        self.assertEqual(get.call_args.kwargs["timeout"], 20)

    def test_download_switch_off_never_calls_http(self):
        event = sample_menu(self.branch_id)
        event["payload"]["items"][0]["images"] = [self.url]
        with patch.object(menu.requests, "get") as get:
            self.webhook(event)
            get.assert_not_called()
        self.assertFalse(self._product().image_1920)

    def test_bad_image_preserves_previous_image_and_other_products_still_sync(self):
        self.sync_sample_menu()
        product = self._product()
        original = base64.b64encode(image_bytes(color="red"))
        product.with_context(blend_inbound_sync=True).image_1920 = original
        self.config.blend_sync_images = True
        event = sample_menu(self.branch_id, event_time="2030-01-01T00:00:00Z")
        first = event["payload"]["items"][0]
        first.update({"name": {"en": "Updated despite invalid image"}, "price": 42, "images": [self.url]})
        second = deepcopy(first)
        second.update({"id": "101", "name": {"en": "Second product"}, "price": 53})
        event["payload"]["items"].append(second)
        event["payload"]["categories"][0]["items"].append("101")
        with patch.object(menu.socket, "getaddrinfo", return_value=dns_results("93.184.216.34")), \
                patch.object(menu.requests, "get", side_effect=[
                    image_response(b"corrupt image"), image_response(image_bytes()),
                ]):
            result = self.webhook(event)
        self.assertEqual(result["result"]["items"], 2)
        self.assertEqual(product.image_1920, original)
        self.assertEqual(product.with_context(lang="en_US").name, "Updated despite invalid image")
        self.assertEqual(product.list_price, 42)
        second_product = self.env["product.template"].search([
            ("blend_config_id", "=", self.config.id), ("blend_external_id", "=", "101"),
        ])
        self.assertEqual(second_product.list_price, 53)
        self.assertTrue(second_product.image_1920)

    def test_malformed_image_entries_are_non_fatal(self):
        self.config.blend_sync_images = True
        for number, images in enumerate(({"url": self.url}, [None], [{"url": self.url}], self.url), 1):
            with self.subTest(images=images), patch.object(menu.requests, "get") as get:
                event = sample_menu(self.branch_id, event_time="2030-01-%02dT00:00:00Z" % number)
                event["payload"]["items"][0].update({"images": images, "price": number})
                self.webhook(event)
                self.assertEqual(self._product().list_price, number)
                get.assert_not_called()

    def test_excessive_pixel_count_is_rejected_before_decode(self):
        response = image_response(image_bytes())
        with patch.object(menu, "IMAGE_MAX_RESOLUTION", 63):
            self.assertFalse(self._download(response)[0])
        self.assertTrue(response.raw.closed)

    def test_unreadable_truncated_image_headers_are_non_fatal(self):
        formats = ["JPEG", "PNG", "GIF", "BMP"]
        if menu.WebPImagePlugin.SUPPORTED:
            formats.append("WEBP")
        for image_format in formats:
            data = image_bytes(image_format)
            with self.subTest(image_format=image_format):
                response = image_response(data[:12])
                self.assertFalse(self._download(response)[0])
                self.assertTrue(response.raw.closed)

    def test_missing_image_list_keeps_the_existing_image(self):
        self.sync_sample_menu()
        product = self._product()
        original = base64.b64encode(image_bytes())
        product.with_context(blend_inbound_sync=True).image_1920 = original
        self.config.blend_sync_images = True
        with patch.object(menu.requests, "get") as get:
            self.webhook(sample_menu(self.branch_id, event_time="2030-02-01T00:00:00Z"))
            get.assert_not_called()
        self.assertEqual(product.image_1920, original)

    def test_successful_image_sync_keeps_branch_and_company_scope(self):
        self.config.blend_sync_images = True
        self.sync_sample_menu()
        product = self._product()
        original = base64.b64encode(image_bytes(color="red"))
        product.with_context(blend_inbound_sync=True).image_1920 = original
        company = self.env["res.company"].create({"name": "Blend image isolation company"})
        for index, target_company in enumerate(self.env.company | company):
            other_config = self.env["pos.config"].with_company(target_company).create({
                "name": "Other image branch %s" % index, "company_id": target_company.id,
                "blend_enabled": True, "blend_branch_id": "%s-%s" % (self.branch_id, index),
                "blend_brand_id": self.brand_id, "blend_inbound_token": "other-image-token-%s" % index,
                "blend_sync_images": True, "payment_method_ids": [(6, 0, [])],
            })
            event = sample_menu(other_config.blend_branch_id)
            event["payload"]["items"][0]["images"] = [self.url]
            response = image_response(image_bytes())
            with patch.object(menu.socket, "getaddrinfo", return_value=dns_results("93.184.216.34")), \
                    patch.object(menu.requests, "get", return_value=response):
                BlendWebhookService(self.env).handle(event, "Bearer other-image-token-%s" % index)
            other_product = self._product(other_config)
            self.assertNotEqual(other_product, product)
            self.assertEqual(other_product.company_id, target_company)
            self.assertEqual(other_product.image_1920, base64.b64encode(image_bytes()))
            self.assertEqual(product.image_1920, original)
