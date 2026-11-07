import hashlib
import hmac
import html
import json
import logging
import re

from odoo import http
from odoo.http import request

from ..models.tryorder_intake import TryOrderRequestError
from ..services.catalog import TryOrderModifierCatalog
from odoo.addons.bs_pos_delivery_core.exceptions import DeliveryBusinessError
from odoo.addons.bs_pos_delivery_core.tools.http_audit import audit_failed_http_attempt

_logger = logging.getLogger(__name__)


def _audit_tryorder_connection():
    key = request.httprequest.headers.get('X-Api-Key')
    try:
        connections = request.env['bs.delivery.tryorder.intake'].sudo()._authorized_connections(key)
    except TryOrderRequestError:
        return False
    if len(connections) == 1:
        return connections
    body = request.httprequest.get_json(silent=True)
    order = body.get('order') if isinstance(body, dict) else None
    branch_id = order.get('branch_id') if isinstance(order, dict) else None
    if not str(branch_id or '').isdecimal():
        return False
    stores = request.env['bs.delivery.store.binding'].sudo().search([
        ('connection_id', 'in', connections.ids),
        ('pos_config_id', '=', int(branch_id)),
        ('active', '=', True),
    ])
    return stores.connection_id if len(stores) == 1 else False


class OrderConnectorController(http.Controller):
    """TryOrder HTTP endpoints backed by Delivery Core."""

    @staticmethod
    def _body():
        try:
            raw = request.httprequest.get_data(as_text=True) or '{}'
            return json.loads(raw)
        except (ValueError, TypeError):
            return {}

    @staticmethod
    def _as_text(value):
        """Coerce a value to a string. The platform may send localized names as
        dicts ({'ar': '...', 'en': '...'}); pick en, then ar, then any value."""
        if isinstance(value, dict):
            return str(value.get('en') or value.get('ar') or next(iter(value.values()), '') or '')
        return str(value) if value is not None else ''

    @staticmethod
    def _strip_html(value):
        if not value:
            return ''
        text = re.sub(r'<[^>]*>', '', value)
        text = html.unescape(text)
        return re.sub(r'\s+', ' ', text).strip()

    @staticmethod
    def _tr(record, field, lang):
        return record.with_context(lang=lang)[field]

    @staticmethod
    def _ar_code():
        """The Arabic language code this database actually has installed.

        Odoo ships Arabic as 'ar_001', but a database may carry a country
        variant instead ('ar_SA', 'ar_EG', ...) or no Arabic at all. Odoo
        raises "Invalid language code" for a language that is not installed,
        which fails the whole request, so never hardcode it. Returns None when
        the database has no Arabic.
        """
        try:
            installed = [code for code, _name in request.env['res.lang'].sudo().get_installed()]
        except Exception:  # noqa: BLE001 - a language lookup must never break a request
            _logger.exception("TryOrder: could not list installed languages")
            return None
        if 'ar_001' in installed:
            return 'ar_001'
        return next((code for code in installed if code == 'ar' or code.startswith('ar_')), None)

    @classmethod
    def _tr_ar(cls, record, field):
        """Arabic value of `field`, falling back to the English one when the
        database has no Arabic installed, so the platform still gets a name."""
        return cls._tr(record, field, cls._ar_code() or 'en_US')

    # ------------------------------------------------------------------ #
    # GET /order_connector/ping
    # ------------------------------------------------------------------ #
    @http.route('/order_connector/ping', type='http', auth='public', methods=['GET'], csrf=False)
    def ping(self, **kw):
        try:
            connections = request.env['bs.delivery.tryorder.intake'].sudo()._authorized_connections(
                request.httprequest.headers.get('X-Api-Key'),
            )
        except TryOrderRequestError as error:
            return request.make_json_response({'success': False, 'error': str(error)}, status=error.status)
        module = request.env['ir.module.module'].sudo().search(
            [('name', '=', 'bs_pos_delivery_tryorder')], limit=1)
        return request.make_json_response({
            'success': True,
            'tenant': connections.merchant_id if len(connections) == 1 else None,
            'company': connections.company_id.name if len(connections) == 1 else None,
            'version': '2.0',
            'module_version': module.installed_version if module else None,
        })

    # ------------------------------------------------------------------ #
    # GET /order_connector/branches
    # ------------------------------------------------------------------ #
    @http.route('/order_connector/branches', type='http', auth='public', methods=['GET'], csrf=False)
    def branches(self, **kw):
        try:
            connections = request.env['bs.delivery.tryorder.intake'].sudo()._authorized_connections(
                request.httprequest.headers.get('X-Api-Key'),
            )
        except TryOrderRequestError as error:
            return request.make_json_response({'success': False, 'error': str(error)}, status=error.status)
        stores = request.env['bs.delivery.store.binding'].sudo().search([
            ('connection_id', 'in', connections.ids), ('active', '=', True),
        ])
        data = [{
            'id': str(store.pos_config_id.id),
            'name': store.pos_config_id.name,
            'company': store.company_id.name,
        } for store in stores]
        return request.make_json_response({'success': True, 'branches': data})

    # ------------------------------------------------------------------ #
    # GET /order_connector/catalog
    # ------------------------------------------------------------------ #
    @http.route('/order_connector/catalog', type='http', auth='public', methods=['GET'], csrf=False)
    def catalog(self, **kw):
        try:
            store = request.env['bs.delivery.tryorder.intake'].sudo()._authorized_store(
                request.httprequest.headers.get('X-Api-Key'), kw.get('branch_id'),
            )
            items = self._catalog_items(store)
        except TryOrderRequestError as error:
            return request.make_json_response({'success': False, 'error': str(error)}, status=error.status)
        config = store.pos_config_id
        return request.make_json_response({
            'success': True,
            'categories': self._catalog_categories(config),
            'items': items,
            'sizes': [],
        })

    def _config_category_ids(self, config):
        """The pos.category ids available at this branch, expanded to include
        child categories. Returns None when the branch does not restrict
        categories (all categories available)."""
        if not config or not getattr(config, 'limit_categories', False):
            return None
        available = getattr(config, 'iface_available_categ_ids', False)
        if not available:
            return []
        return request.env['pos.category'].sudo().search(
            [('id', 'child_of', available.ids)]).ids

    def _product_domain(self, config):
        """Search domain for the products exported to a branch: available in
        POS, in the branch's company, and within its category limits."""
        domain = [('available_in_pos', '=', True), ('sale_ok', '=', True)]
        if config:
            company = config.company_id
            if company:
                domain += ['|', ('company_id', '=', False), ('company_id', '=', company.id)]
            categ_ids = self._config_category_ids(config)
            if categ_ids is not None:
                domain.append(('pos_categ_ids', 'in', categ_ids))
        return domain

    def _catalog_categories(self, config=None):
        domain = []
        categ_ids = self._config_category_ids(config)
        if categ_ids is not None:
            domain = [('id', 'in', categ_ids)]
        categories = request.env['pos.category'].sudo().search(domain)
        result = []
        for cat in categories:
            result.append({
                '_id': str(cat.id),
                'remoteId': str(cat.id),
                'name_en': self._tr(cat, 'name', 'en_US'),
                'name_ar': self._tr_ar(cat, 'name'),
                'description_en': '',
                'description_ar': '',
                'parent_id': str(cat.parent_id.id) if cat.parent_id else None,
                'sort': cat.sequence,
            })
        return result

    def _base_url(self):
        """Public base URL. Uses web.base.url (set to the https public host when
        Odoo runs behind a TLS-terminating proxy) instead of host_url, which is
        http on the internal proxy hop and 301-redirects (IPD-209)."""
        params = request.env['ir.config_parameter'].sudo()
        base = (params.get_param('web.base.url') or '').strip() or request.httprequest.host_url
        return base.rstrip('/')

    def _catalog_items(self, store):
        base = self._base_url()
        config = store.pos_config_id
        products = request.env['product.template'].sudo().search(self._product_domain(config))
        pricelist = store.connection_id._get_menu_pricelist()
        menu_prices = pricelist._get_products_price(products, 1.0) if pricelist else {}
        group_lines = products.filtered('allow_modifiers').modifier_group_line_ids
        option_products = (
            group_lines.modifier_group_id.option_ids.product_id
            | group_lines.option_line_ids.product_id
        )
        modifier_catalog = TryOrderModifierCatalog(store, pricelist, option_products)
        result = []
        for product in products:
            if (product.default_code or '').startswith((
                'TRYORDER-PTAV-', 'BS-DELIVERY-UNTAXED-DISCOUNT-',
            )):
                continue
            if len(product.product_variant_ids) != 1:
                continue
            pos_categs = product.pos_categ_ids
            category_id = str(pos_categs[0].id) if pos_categs else None
            has_image = bool(product.image_512)
            is_combo = product.type == 'combo'
            result.append({
                '_id': str(product.id),
                'remoteId': str(product.id),
                'sku': product.default_code or '',
                'type': product.type,
                'name_en': self._tr(product, 'name', 'en_US'),
                'name_ar': self._tr_ar(product, 'name'),
                'description_en': self._strip_html(self._tr(product, 'description_sale', 'en_US')),
                'description_ar': self._strip_html(self._tr_ar(product, 'description_sale')),
                'price': menu_prices[product.id] if pricelist else product.list_price,
                'category': category_id,
                'is_disabled': not product.active,
                'image_url': (
                    f"{base}/public/product/image/{product.id}?branch_id={config.id}&token="
                    + hmac.new(
                        store.connection_id.webhook_secret.encode(),
                        f"{store.id}:{product.id}".encode(), hashlib.sha256,
                    ).hexdigest()
                ) if has_image else None,
                'add_on': self._catalog_addons(product, store.company_id, modifier_catalog),
                'combos': self._catalog_combos(product, store.company_id) if is_combo else [],
            })
        return result

    def _catalog_combos(self, product, company):
        """IPD-210: export Odoo combo groups/choices so combo options sync.

        Odoo combo product -> combo_ids (product.combo, the choice groups) ->
        combo_item_ids (product.combo.item) -> product_id + extra_price.
        """
        combos = []
        for combo in getattr(product, 'combo_ids', []) or []:
            if combo.company_id and combo.company_id != company:
                continue
            items = []
            for ci in getattr(combo, 'combo_item_ids', []) or []:
                prod = getattr(ci, 'product_id', False)
                tmpl_id = prod.product_tmpl_id.id if prod else None
                items.append({
                    'id': f"comboitem-{ci.id}",
                    'product_id': str(tmpl_id) if tmpl_id else None,
                    'name_en': self._tr(prod, 'name', 'en_US') if prod else '',
                    'name_ar': self._tr_ar(prod, 'name') if prod else '',
                    'extra_price': getattr(ci, 'extra_price', 0.0) or 0.0,
                    'default_qty': getattr(ci, 'default_qty', 0),
                    'qty_max': getattr(ci, 'qty_max', 0),
                })
            combos.append({
                'id': f"combo-{combo.id}",
                'name_en': self._tr(combo, 'name', 'en_US'),
                'name_ar': self._tr_ar(combo, 'name'),
                'items': items,
                'qty_min': getattr(combo, 'qty_min', 0),
                'qty_max': getattr(combo, 'qty_max', 1),
            })
        return combos

    def _catalog_addons(self, product, company, modifier_catalog):
        """Expose stable POS modifier IDs that can be returned in an order."""
        add_on = []
        for line in product.attribute_line_ids:
            attribute = line.attribute_id
            category = {
                '_id': f"attr-{attribute.id}",
                'remoteId': f"attr-{attribute.id}",
                'name_en': self._tr(attribute, 'name', 'en_US'),
                'name_ar': self._tr_ar(attribute, 'name'),
                'description_en': '',
                'description_ar': '',
                'min_selection': 0,
                'max_selection': (1 if attribute.display_type in ('radio', 'select', 'color')
                                  else len(line.product_template_value_ids)),
                'status': True,
            }
            for ptav in line.product_template_value_ids:
                add_on.append({
                    'modifierCategory': category,
                    'modifier': {
                        '_id': f"ptav-{ptav.id}",
                        'remoteId': f"ptav-{ptav.id}",
                        'name_en': self._tr(ptav, 'name', 'en_US'),
                        'name_ar': self._tr_ar(ptav, 'name'),
                        'price': ptav.price_extra,
                        'category': f"attr-{attribute.id}",
                    },
                })
        if not product.allow_modifiers:
            return add_on
        for line in product.modifier_group_line_ids:
            group = line.modifier_group_id
            if not group.active or group.company_id != company:
                continue
            # IDs are per product (group line): a shared group or option ID
            # lets the platform merge one product's choices, availability and
            # prices into another's. Orders return ``posmod-<option>-<line>``.
            category_id = f"posmodgroup-{group.id}-{line.id}"
            category = {
                '_id': category_id,
                'remoteId': category_id,
                'name_en': self._tr(group, 'name', 'en_US'),
                'name_ar': self._tr_ar(group, 'name'),
                'description_en': '',
                'description_ar': '',
                'min_selection': group.min_qty,
                'max_selection': group.max_qty,
                'status': True,
            }
            group_addons = []
            for option in group.option_ids.filtered('active'):
                effective = line._effective_option(option)
                if 'available' in effective._fields and not effective.available:
                    continue
                if option.option_type == 'product' and not modifier_catalog.product_in_catalog(
                    effective.product_id,
                ):
                    continue
                modifier_id = f"posmod-{option.id}-{line.id}"
                group_addons.append({
                    'modifierCategory': category,
                    'modifier': {
                        '_id': modifier_id,
                        'remoteId': modifier_id,
                        'name_en': self._tr(effective, 'name', 'en_US'),
                        'name_ar': self._tr_ar(effective, 'name'),
                        'price': modifier_catalog.price(effective),
                        'status': modifier_catalog.available(effective),
                        'category': category_id,
                        'option_type': option.option_type,
                        'operation': option.operation,
                        'kitchen_name': effective.kitchen_name or '',
                        'default_qty': effective.default_qty,
                        'max_qty': effective.max_qty,
                    },
                })
            if any(row['modifier']['status'] for row in group_addons):
                add_on.extend(group_addons)
        return add_on

    # ------------------------------------------------------------------ #
    # POST /order_connector/orders
    # ------------------------------------------------------------------ #
    @http.route('/order_connector/orders', type='http', auth='public', methods=['POST'], csrf=False)
    @audit_failed_http_attempt('tryorder:create_order', _audit_tryorder_connection)
    def create_order(self, **kw):
        body = self._body()
        order = body.get('order') if isinstance(body, dict) else None
        if not isinstance(order, dict):
            return request.make_json_response(
                {'success': False, 'error': 'order must be an object'}, status=422,
            )
        try:
            delivery = request.env['bs.delivery.tryorder.intake'].sudo().receive_order(
                order, request.httprequest.headers.get('X-Api-Key'),
            )
        except TryOrderRequestError as error:
            return request.make_json_response(
                {'success': False, 'error': str(error)}, status=error.status,
            )
        except DeliveryBusinessError as error:
            status = 409 if 'conflict' in error.code else 422
            return request.make_json_response(
                {'success': False, 'error': str(error)}, status=status,
            )
        pos_order = delivery.pos_order_id
        if pos_order:
            return request.make_json_response({
                'success': True,
                'order_id': str(pos_order.id),
                'order_name': pos_order.name or pos_order.pos_reference,
                'state': pos_order.state,
                'kind': 'pos',
            })
        return request.make_json_response({
            'success': True,
            'order_id': str(delivery.id),
            'order_name': delivery.external_order_id,
            'state': delivery.state,
            'kind': 'core',
            'pos_processing_state': delivery.pos_processing_state,
            'pos_order_created': False,
        })

    @http.route('/order_connector/orders/<int:order_id>/status', type='http', auth='public',
                methods=['PUT', 'POST'], csrf=False)
    @audit_failed_http_attempt('tryorder:update_status', _audit_tryorder_connection)
    def update_order_status(self, order_id, **kw):
        body = self._body()
        try:
            delivery = request.env['bs.delivery.tryorder.intake'].sudo().update_status(
                order_id, body, request.httprequest.headers.get('X-Api-Key'),
            )
        except TryOrderRequestError as error:
            return request.make_json_response(
                {'success': False, 'error': str(error)}, status=error.status,
            )
        except DeliveryBusinessError as error:
            status = 409 if 'conflict' in error.code else 422
            return request.make_json_response(
                {'success': False, 'error': str(error)}, status=status,
            )
        pos_order = delivery.pos_order_id
        if pos_order:
            return request.make_json_response({
                'success': True, 'order_id': str(pos_order.id),
                'state': pos_order.state, 'status': delivery.external_status,
            })
        return request.make_json_response({
            'success': True, 'order_id': str(delivery.id),
            'state': delivery.state, 'status': delivery.external_status,
            'pos_processing_state': delivery.pos_processing_state,
            'pos_order_created': False,
        })
