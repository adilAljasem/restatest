import uuid

from odoo.tests.common import TransactionCase

from ..services.webhook import BlendWebhookService


def sample_menu(branch_id, *, event_time="2026-09-01T10:00:00Z"):
    return {
        "event": "menu_sync",
        "event_time": event_time,
        "branch_id": branch_id,
        "payload": {
            "categories": [
                {
                    "id": "10",
                    "name": {"ar": "بيتزا", "en": "Pizza"},
                    "description": {"ar": "قسم البيتزا", "en": "Pizza category"},
                    "schedule_time": {
                        "monday": [
                            {"start": "08:00", "end": "23:00", "is_visible": True}
                        ]
                    },
                    "items": ["100"],
                }
            ],
            "items": [
                {
                    "id": "100",
                    "name": {"ar": "بيتزا مارجريتا", "en": "Margherita Pizza"},
                    "description": {"ar": "وصف عربي", "en": "English description"},
                    "price": 150.0,
                    "is_available": True,
                    "categories": ["10"],
                    "images": [],
                    "schedule_time": {},
                }
            ],
            "toppings": [
                {
                    "id": "20",
                    "name": {"ar": "الإضافات", "en": "Add-ons"},
                    "is_required": True,
                    "min": 1,
                    "max": 2,
                    "max_per_quantity": 2,
                    "options": [
                        {
                            "id": "200",
                            "name": {"ar": "جبنة إضافية", "en": "Extra Cheese"},
                            "price": 10.0,
                        }
                    ],
                    "items": ["100"],
                }
            ],
            "branch_schedule_time": {},
        },
    }


def configure_blend_preset(config):
    preset = config.env["pos.preset"].create({"name": "Blend Delivery Test"})
    config.write({
        "use_presets": True,
        "available_preset_ids": [(4, preset.id)],
        "blend_default_preset_id": preset.id,
    })
    return preset


class BlendTransactionCase(TransactionCase):
    def setUp(self):
        super().setUp()
        # Fixtures use bilingual menu names and UTC schedule timestamps; do not
        # depend on languages/timezones installed in an existing client database.
        self.env["res.lang"]._activate_lang("ar_001")
        self.env.company.partner_id.tz = "UTC"
        suffix = uuid.uuid4().hex[:10]
        self.branch_id = "branch-%s" % suffix
        self.brand_id = "brand-%s" % suffix
        self.token = "inbound-%s" % suffix
        self.outbound_token = "outbound-%s" % suffix
        self.config = self.env["pos.config"].create(
            {
                "name": "Blend Test POS %s" % suffix,
                "blend_enabled": True,
                "blend_branch_id": self.branch_id,
                "blend_brand_id": self.brand_id,
                "blend_inbound_token": self.token,
                "blend_outbound_token": self.outbound_token,
                "blend_order_url": "https://blend.example.test/orders",
                "blend_status_url": "https://blend.example.test/status",
                "blend_event_url": "https://blend.example.test/events",
                "blend_sync_images": False,
                "blend_timeout": 5,
                "blend_retry_max": 3,
                "payment_method_ids": [(6, 0, [])],
            }
        )
        self.env["resta.kds.display"].create(
            {"name": "Blend kitchen (all)", "pos_config_ids": [(6, 0, self.config.ids)]})

        configure_blend_preset(self.config)

    def webhook(self, payload):
        return BlendWebhookService(self.env).handle(
            payload,
            "Bearer %s" % self.token,
            request_headers={"X-Request-ID": "blend-test"},
        )

    def sync_sample_menu(self, *, event_time="2026-09-01T10:00:00Z"):
        payload = sample_menu(self.branch_id, event_time=event_time)
        result = self.webhook(payload)
        # The fixture's imported ID 100 must not collide with native demo ID 100.
        categories = self.env["pos.category"].search([("blend_config_id", "=", self.config.id)])
        self.config.write({"limit_categories": True, "iface_available_categ_ids": [(6, 0, categories.ids)]})
        return result

    def create_order(self):
        self.sync_sample_menu()
        product = self.env["product.template"].search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", "100"),
                ("use_as_modifier", "=", False),
            ]
        )
        option = self.env["product.template"].search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", "200"),
                ("use_as_modifier", "=", True),
            ]
        )
        session = self.env["pos.session"].create({"config_id": self.config.id})
        partner = self.env["res.partner"].create(
            {
                "name": "Blend Customer",
                "phone": "+966500000000",
                "street": "Test Street",
                "city": "Riyadh",
            }
        )
        order_uuid = str(uuid.uuid4())
        order = self.env["pos.order"].create(
            {
                "session_id": session.id,
                "config_id": self.config.id,
                "uuid": order_uuid,
                "pos_reference": "BLEND-TEST-%s" % order_uuid[:8],
                "partner_id": partner.id,
                "general_customer_note": "No cutlery",
                "amount_tax": 10.0,
                "amount_total": 160.0,
                "amount_paid": 160.0,
                "amount_return": 0.0,
            }
        )
        parent_line = self.env["pos.order.line"].create(
            {
                "order_id": order.id,
                "product_id": product.product_variant_id.id,
                "qty": 1,
                "price_unit": 150.0,
                "price_subtotal": 150.0,
                "price_subtotal_incl": 150.0,
                "customer_note": "Well done",
            }
        )
        self.env["pos.order.line"].create(
            {
                "order_id": order.id,
                "product_id": option.product_variant_id.id,
                "qty": 1,
                "price_unit": 10.0,
                "price_subtotal": 10.0,
                "price_subtotal_incl": 10.0,
                "parent_line_id": str(parent_line.id),
                "modifier_selection_qty": 1,
            }
        )
        return order
