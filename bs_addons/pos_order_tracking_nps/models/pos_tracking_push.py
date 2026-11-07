import logging
import textwrap
from urllib.parse import urlsplit

from requests import Session

from odoo import api, fields, models
from odoo.addons.mail.tools import jwt
from odoo.addons.mail.tools.web_push import _encrypt_payload, DeviceUnreachableError

_logger = logging.getLogger(__name__)


def send_web_push(session, base_url, endpoint, keys, payload, vapid_private_key, vapid_public_key,
                  ttl=900, urgency='high'):
    """RFC 8291/8030 web push, like mail's push_to_end_point but with ``Urgency: high``
    (delivered immediately, also to phones in battery-saving mode) and a longer TTL
    (a phone that is briefly offline still receives the alert)."""
    url = urlsplit(endpoint)
    if url.netloc.endswith(".invalid"):
        raise DeviceUnreachableError("Device Unreachable")
    token = jwt.sign(
        {'aud': f'{url.scheme}://{url.netloc}', 'sub': base_url},
        vapid_private_key, ttl=12 * 60 * 60, algorithm=jwt.Algorithm.ES256,
    )
    body = _encrypt_payload(payload.encode(), {'keys': keys})
    headers = {
        'Authorization': f'vapid t={token}, k={vapid_public_key}',
        'Content-Encoding': 'aes128gcm',
        'TTL': str(int(ttl)),
        'Urgency': urgency,
    }
    response = session.post(endpoint, headers=headers, data=body, timeout=5)
    if response.status_code in (200, 201, 202):
        return True
    _logger.warning("Order tracking push failed %s %d - %s", endpoint[:60], response.status_code,
                    textwrap.shorten(response.text or '', 100))
    if response.status_code in (404, 410):
        raise DeviceUnreachableError("Device Unreachable")
    return False


class PosTrackingSubscription(models.Model):
    _name = 'pos.tracking.subscription'
    _description = 'POS Order Tracking Web Push Subscription'

    order_id = fields.Many2one('pos.order', required=True, ondelete='cascade', index=True)
    endpoint = fields.Char(required=True)
    keys = fields.Char(required=True, help="JSON with the browser p256dh and auth keys")
    lang = fields.Char()

    _order_endpoint_unique = models.Constraint(
        'unique(order_id, endpoint)', 'This browser is already subscribed to the order.',
    )

    @api.model
    def _vapid_public_key(self):
        # Reuse the VAPID key pair managed by the mail module (generated on first use).
        return self.env['mail.push.device'].sudo().get_web_push_vapid_public_key()


class PosTrackingPush(models.Model):
    _name = 'pos.tracking.push'
    _description = 'POS Order Tracking Push Queue'

    subscription_id = fields.Many2one('pos.tracking.subscription', required=True, ondelete='cascade')
    payload = fields.Text()
    ttl = fields.Integer(default=900)
    urgency = fields.Selection([('high', 'High'), ('normal', 'Normal')], default='high')

    @api.model
    def _process_queue(self, batch_size=100):
        pushes = self.sudo().search([], limit=batch_size)
        if not pushes:
            return
        params = self.env['ir.config_parameter'].sudo()
        private_key = params.get_param('mail.web_push_vapid_private_key')
        public_key = params.get_param('mail.web_push_vapid_public_key')
        if not private_key or not public_key:
            pushes.unlink()
            return
        session = Session()
        dead = self.env['pos.tracking.subscription']
        base_url = self.get_base_url()
        for push in pushes:
            sub = push.subscription_id
            if sub in dead:
                continue
            try:
                send_web_push(
                    session, base_url, sub.endpoint, sub.keys, push.payload,
                    private_key, public_key, ttl=push.ttl or 900, urgency=push.urgency or 'high',
                )
            except DeviceUnreachableError:
                dead |= sub
            except Exception as e:  # noqa: BLE001 - never block the queue for one device
                _logger.warning("Order tracking push failed: %s", e)
        pushes.unlink()
        dead.unlink()
        if self.search_count([], limit=1):
            self.env.ref('pos_order_tracking_nps.ir_cron_tracking_push').sudo()._trigger()
