# -*- coding: utf-8 -*-
"""Delivery app name and order number printed on the receipt (bs_pos_delivery_core / Connect).

bs_pos_delivery_core keeps the delivery app order in `bs.delivery.order`,
linked to the POS order through `pos_order_id`. These fields show its
details on the POS order so the receipt can print them. They are empty when
the connector is not installed or the order is not a delivery app order, and
the receipt module does not depend on the connector.
"""
from odoo import fields, models

DELIVERY_MODEL = 'bs.delivery.order'


class PosOrder(models.Model):
    _inherit = 'pos.order'

    receipt_delivery_platform = fields.Char(
        string='Delivery App', compute='_compute_receipt_delivery', compute_sudo=True)
    receipt_delivery_external_id = fields.Char(
        string='Delivery App Order ID', compute='_compute_receipt_delivery', compute_sudo=True)
    receipt_delivery_reference = fields.Char(
        string='Delivery App Order Number', compute='_compute_receipt_delivery', compute_sudo=True,
        help='Short order number shown by the delivery app, when it has one.')

    def _receipt_delivery_orders(self):
        """{pos.order id: bs.delivery.order} for the orders of the recordset."""
        if DELIVERY_MODEL not in self.env:
            return {}
        order_ids = [order_id for order_id in self.ids if isinstance(order_id, int)]
        if not order_ids:
            return {}
        Delivery = self.env[DELIVERY_MODEL].sudo()
        if 'pos_order_id' not in Delivery._fields:
            return {}
        result = {}
        for delivery in Delivery.search([('pos_order_id', 'in', order_ids)], order='id desc'):
            result.setdefault(delivery.pos_order_id.id, delivery)
        return result

    def _compute_receipt_delivery(self):
        deliveries = self._receipt_delivery_orders()
        for order in self:
            delivery = deliveries.get(order.id)
            values = order._receipt_delivery_values(delivery) if delivery else {}
            order.receipt_delivery_platform = values.get('platform', False)
            order.receipt_delivery_external_id = values.get('external_id', False)
            order.receipt_delivery_reference = values.get('reference', False)

    def _receipt_delivery_values(self, delivery):
        def get(name):
            return delivery[name] if name in delivery._fields else False

        def text(name):
            value = get(name)
            return str(value).strip() if value else False

        provider = get('provider_id') or get('connection_id')
        external_id = text('external_order_id')
        reference = text('external_reference')
        if reference == external_id:
            reference = False
        return {
            'platform': (provider.display_name if provider else False) or text('external_source'),
            'external_id': external_id,
            'reference': reference,
        }
