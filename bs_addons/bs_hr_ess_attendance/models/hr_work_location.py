import hashlib
import hmac
import math
import secrets
import time

from odoo import api, fields, models
from odoo.tools.sql import create_index

EARTH_RADIUS_M = 6371000.0


def haversine(lat1, lng1, lat2, lng2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


class BsHrGeofencePoint(models.Model):
    _name = 'bs.hr.geofence.point'
    _description = 'Extra geofence point of a branch'

    location_id = fields.Many2one('hr.work.location', required=True, ondelete='cascade')
    name = fields.Char(required=True, default='Entrance')
    latitude = fields.Float(digits=(10, 7), required=True)
    longitude = fields.Float(digits=(10, 7), required=True)
    radius = fields.Integer(string='Radius (m)', default=100, required=True)


class HrWorkLocation(models.Model):
    _inherit = 'hr.work.location'

    ess_latitude = fields.Float(string='Latitude', digits=(10, 7))
    ess_longitude = fields.Float(string='Longitude', digits=(10, 7))
    ess_radius = fields.Integer(string='Radius (m)', default=100)
    ess_point_ids = fields.One2many('bs.hr.geofence.point', 'location_id', string='Extra points')
    ess_max_accuracy = fields.Integer(
        string='Max GPS accuracy (m)', default=150,
        help='Readings less precise than this are refused (the phone must have a good GPS fix).')
    ess_use_geo = fields.Boolean(string='Location (geofence)', default=True)
    ess_use_qr = fields.Boolean(string='Rotating QR code')
    ess_use_face = fields.Boolean(string='Face recognition')
    ess_method_logic = fields.Selection([
        ('all', 'All selected methods are required'),
        ('any', 'Any selected method is enough'),
    ], string='Combination', default='all', required=True)
    ess_qr_period = fields.Integer(string='QR refresh (seconds)', default=10)
    ess_qr_secret = fields.Char(copy=False, groups='base.group_system',
                                default=lambda self: secrets.token_hex(32))
    ess_kiosk_token = fields.Char(string='Kiosk token', copy=False, index=True,
                                  default=lambda self: secrets.token_urlsafe(16))
    ess_kiosk_key = fields.Char(string='Kiosk activation key', copy=False,
                                groups='bs_hr_ess_base.group_ess_hr_officer',
                                default=lambda self: '%06d' % secrets.randbelow(1000000))
    ess_kiosk_url = fields.Char(compute='_compute_ess_kiosk_url', string='Kiosk URL')
    ess_today_present = fields.Integer(compute='_compute_ess_today', string='Present now')

    def init(self):
        # Existing branches got the same default when the columns were added:
        # give each one its own secrets, then enforce the token uniqueness.
        cr = self.env.cr
        cr.execute("""
            SELECT id FROM hr_work_location
             WHERE ess_kiosk_token IS NULL OR ess_qr_secret IS NULL
                OR ess_kiosk_token IN (SELECT ess_kiosk_token FROM hr_work_location
                                        GROUP BY ess_kiosk_token HAVING count(*) > 1)
                OR ess_qr_secret IN (SELECT ess_qr_secret FROM hr_work_location
                                      GROUP BY ess_qr_secret HAVING count(*) > 1)
        """)
        for (loc_id,) in cr.fetchall():
            cr.execute(
                'UPDATE hr_work_location SET ess_kiosk_token = %s, ess_qr_secret = %s, ess_kiosk_key = %s '
                'WHERE id = %s',
                [secrets.token_urlsafe(16), secrets.token_hex(32), '%06d' % secrets.randbelow(1000000), loc_id])
        create_index(cr, 'hr_work_location_ess_kiosk_token_uniq_idx', self._table,
                     ['ess_kiosk_token'], unique=True)

    def _compute_ess_kiosk_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for loc in self:
            loc.ess_kiosk_url = '%s/hr/kiosk/%s' % (base, loc.ess_kiosk_token) if loc.ess_kiosk_token else ''

    def _compute_ess_today(self):
        data = self.env['hr.attendance'].sudo()._read_group(
            [('ess_location_id', 'in', self.ids), ('check_out', '=', False)], ['ess_location_id'], ['__count'])
        counts = {loc.id: c for loc, c in data}
        for loc in self:
            loc.ess_today_present = counts.get(loc.id, 0)

    # ------------------------------------------------------------------
    # geofence
    # ------------------------------------------------------------------
    def _ess_geo_points(self):
        self.ensure_one()
        points = []
        if self.ess_latitude or self.ess_longitude:
            points.append((self.ess_latitude, self.ess_longitude, self.ess_radius or 100))
        points += [(p.latitude, p.longitude, p.radius) for p in self.ess_point_ids]
        return points

    def _ess_geo_check(self, lat, lng):
        """Return (inside, distance_to_nearest_point_in_m)."""
        self.ensure_one()
        best = None
        for p_lat, p_lng, radius in self._ess_geo_points():
            distance = haversine(lat, lng, p_lat, p_lng)
            if best is None or distance < best[1]:
                best = (distance <= radius, distance)
            if distance <= radius:
                return True, distance
        return best or (False, None)

    # ------------------------------------------------------------------
    # rotating QR (HMAC-based TOTP)
    # ------------------------------------------------------------------
    def _ess_qr_step(self, ts=None):
        self.ensure_one()
        period = max(self.ess_qr_period or 10, 5)
        return int((ts if ts is not None else time.time()) // period)

    def _ess_qr_signature(self, step):
        self.ensure_one()
        secret = self.sudo().ess_qr_secret or ''
        msg = ('%s:%s' % (self.id, step)).encode()
        return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:20]

    def _ess_qr_code(self, step=None):
        self.ensure_one()
        step = self._ess_qr_step() if step is None else step
        return '%s-%s-%s' % (self.id, step, self._ess_qr_signature(step))

    @api.model
    def _ess_qr_parse(self, code):
        """Return (location, step) when ``code`` is a valid, current code; else (empty, None)."""
        empty = self.browse()
        if not code:
            return empty, None
        if 'c=' in code:  # full URL scanned
            code = code.split('c=', 1)[1].split('&', 1)[0]
        parts = code.strip().split('-')
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            return empty, None
        location = self.sudo().browse(int(parts[0])).exists()
        if not location or not location.ess_use_qr:
            return empty, None
        step = int(parts[1])
        current = location._ess_qr_step()
        if abs(current - step) > 1:  # one period of tolerance for clock skew / scan time
            return empty, None
        if not hmac.compare_digest(location._ess_qr_signature(step), parts[2]):
            return empty, None
        return location, step

    def action_ess_regenerate_secrets(self):
        for loc in self.sudo():
            loc.write({'ess_qr_secret': secrets.token_hex(32),
                       'ess_kiosk_token': secrets.token_urlsafe(16),
                       'ess_kiosk_key': '%06d' % secrets.randbelow(1000000)})

    def action_ess_open_kiosk(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': '/hr/kiosk/%s' % self.ess_kiosk_token, 'target': 'new'}

    def _ess_kiosk_cookie_value(self):
        self.ensure_one()
        key = self.sudo().ess_kiosk_key or ''
        secret = self.sudo().ess_qr_secret or ''
        return hmac.new(secret.encode(), ('kiosk:%s:%s' % (self.id, key)).encode(), hashlib.sha256).hexdigest()
