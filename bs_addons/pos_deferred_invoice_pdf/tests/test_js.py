from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestDeferredInvoicePdfJs(HttpCase):
    def test_payment_download_decision_in_browser(self):
        self.browser_js(
            '/web/tests?headless&loglevel=2&preset=desktop&timeout=15000&filter=deferred%20invoice%20PDF',
            '',
            '',
            login='admin',
            timeout=600,
            success_signal='[HOOT] Test suite succeeded',
        )
