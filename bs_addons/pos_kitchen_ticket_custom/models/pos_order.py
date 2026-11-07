from odoo import fields, models

# Delivery orders of the Connect module (bs_pos_delivery_core). The module is
# optional: without it the fields stay empty and nothing else changes.
DELIVERY_MODELS = ('bs.delivery.order', 'bs.delivery.order.binding')


class PosOrder(models.Model):
    _inherit = 'pos.order'

    kt_delivery_app = fields.Char(
        string='Delivery App (Kitchen)', compute='_compute_kt_delivery',
        help='Delivery application of the order (Connect), printed on the kitchen ticket.')
    kt_delivery_ref = fields.Char(
        string='App Order Number (Kitchen)', compute='_compute_kt_delivery',
        help='Order number in the delivery application, printed on the kitchen ticket.')

    def _compute_kt_delivery(self):
        found = self._kt_delivery_values()
        for order in self:
            app, reference = found.get(order.id, (False, False))
            order.kt_delivery_app = app
            order.kt_delivery_ref = reference

    def _kt_delivery_values(self):
        """{pos order id: (app name, app order number)} from Connect."""
        order_ids = [order_id for order_id in self.ids if isinstance(order_id, int)]
        found = {}
        if not order_ids:
            return found
        for model_name in DELIVERY_MODELS:
            if model_name not in self.env:
                continue
            model = self.env[model_name]
            if 'pos_order_id' not in model._fields:
                continue
            missing = [order_id for order_id in order_ids if order_id not in found]
            if not missing:
                break
            # Cashiers have no access to the delivery records: read them as sudo,
            # only the app name and the order number are used.
            records = model.sudo().with_context(active_test=False).search(
                [('pos_order_id', 'in', missing)], order='id desc')
            for record in records:
                order_id = record.pos_order_id.id
                if order_id in found:
                    continue
                provider = record.provider_id if 'provider_id' in record._fields else False
                connection = record.connection_id if 'connection_id' in record._fields else False
                app = (
                    (provider and provider.display_name)
                    or (connection and connection.display_name)
                    or (record.external_source if 'external_source' in record._fields else False)
                    or False
                )
                reference = (
                    (record.external_reference if 'external_reference' in record._fields else False)
                    or (record.external_order_id if 'external_order_id' in record._fields else False)
                    or False
                )
                if app or reference:
                    found[order_id] = (app, reference)
        return found

    def _load_pos_data_fields(self, config):
        fields_list = list(super()._load_pos_data_fields(config))
        # An empty list means every field is loaded.
        for name in ('kt_delivery_app', 'kt_delivery_ref'):
            if fields_list and name not in fields_list:
                fields_list.append(name)
        return fields_list
