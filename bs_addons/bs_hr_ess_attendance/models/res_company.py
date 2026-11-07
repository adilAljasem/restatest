from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    ess_late_grace_minutes = fields.Integer(string='Lateness grace (minutes)', default=10)
    ess_device_binding = fields.Boolean(
        string='Bind employees to their phone', default=True,
        help='The first phone used becomes the employee device; a new phone needs HR approval.')
    ess_face_threshold = fields.Float(
        string='Face match threshold', default=0.5,
        help='Maximum distance between the live face and the enrolled face (lower = stricter). 0.5 is a good default.')
    ess_face_photo_fallback = fields.Boolean(
        string='Accept a selfie when the live camera is unavailable', default=True,
        help='Some phone apps (e.g. the Odoo iOS / Android app) may block live camera streams. The employee then '
             'takes a selfie: the face is still matched but liveness cannot be checked, so the attendance is '
             'flagged as suspicious for HR review.')
    ess_face_keep_photo = fields.Boolean(string='Keep a check-in photo as evidence', default=True)
    ess_face_photo_days = fields.Integer(string='Keep check-in photos (days)', default=30)
    ess_max_speed_kmh = fields.Integer(
        string='Impossible travel speed (km/h)', default=200,
        help='Two punches implying a faster move are flagged as suspicious (fake GPS).')
