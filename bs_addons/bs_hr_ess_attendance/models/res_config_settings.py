from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ess_late_grace_minutes = fields.Integer(related='company_id.ess_late_grace_minutes', readonly=False)
    ess_device_binding = fields.Boolean(related='company_id.ess_device_binding', readonly=False)
    ess_face_threshold = fields.Float(related='company_id.ess_face_threshold', readonly=False)
    ess_face_photo_fallback = fields.Boolean(related='company_id.ess_face_photo_fallback', readonly=False)
    ess_face_keep_photo = fields.Boolean(related='company_id.ess_face_keep_photo', readonly=False)
    ess_face_photo_days = fields.Integer(related='company_id.ess_face_photo_days', readonly=False)
    ess_max_speed_kmh = fields.Integer(related='company_id.ess_max_speed_kmh', readonly=False)
