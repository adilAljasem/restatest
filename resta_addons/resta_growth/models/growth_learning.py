"""Lessons and guided tours. Content is seeded from a data file; in phase 3 the hub
pushes new versions of it to every restaurant."""
from odoo import api, fields, models


class RestaGrowthLesson(models.Model):
    _name = "resta.growth.lesson"
    _description = "درس رحلة النمو"
    _order = "level_sequence, sequence, id"

    key = fields.Char(required=True, index=True)
    name = fields.Char("العنوان", required=True, translate=True)
    level_id = fields.Many2one("resta.growth.level", "المستوى", ondelete="set null")
    level_sequence = fields.Integer(related="level_id.sequence", store=True)
    sequence = fields.Integer(default=10)
    kind = fields.Selection([("video", "فيديو"), ("article", "مقال"), ("checklist", "قائمة تحقق"),
                             ("tour", "جولة إرشادية")], "النوع", default="article", required=True)
    minutes = fields.Integer("المدة (دقائق)", default=3)
    video_url = fields.Char("رابط الفيديو")
    content = fields.Html("المحتوى", translate=True, sanitize=True)
    tour_name = fields.Char("الجولة المرتبطة")
    version = fields.Integer(default=1)
    active = fields.Boolean(default=True)

    _key_uniq = models.Constraint("UNIQUE(key)", "مفتاح الدرس مستخدم.")

    def _ui(self, done_ids=()):
        return [{
            "id": l.id, "key": l.key, "name": l.name, "kind": l.kind, "minutes": l.minutes,
            "video_url": l.video_url or "", "content": l.content or "", "tour": l.tour_name or "",
            "level": l.level_id.sequence or 0, "done": l.id in done_ids,
        } for l in self]

    @api.model
    def mark_done(self, lesson_key):
        lesson = self.sudo().search([("key", "=", lesson_key)], limit=1)
        if lesson:
            self.env["resta.growth.lesson.progress"]._set_done(lesson)
        return True

    @api.model
    def _mark_tour_done(self, tour_name):
        for lesson in self.sudo().search([("tour_name", "=", tour_name)]):
            self.env["resta.growth.lesson.progress"]._set_done(lesson)


class RestaGrowthLessonProgress(models.Model):
    _name = "resta.growth.lesson.progress"
    _description = "تقدّم المستخدم في الدروس"

    user_id = fields.Many2one("res.users", required=True, default=lambda s: s.env.user, ondelete="cascade", index=True)
    lesson_id = fields.Many2one("resta.growth.lesson", required=True, ondelete="cascade")
    state = fields.Selection([("started", "بدأ"), ("done", "أنجز")], default="started", required=True)
    done_date = fields.Datetime()
    tour_offered = fields.Boolean("عُرضت الجولة")

    _uniq = models.Constraint("UNIQUE(user_id, lesson_id)", "تقدّم مكرر.")

    @api.model
    def _get(self, lesson, user=None):
        user = user or self.env.user
        rec = self.sudo().search([("user_id", "=", user.id), ("lesson_id", "=", lesson.id)], limit=1)
        return rec or self.sudo().create({"user_id": user.id, "lesson_id": lesson.id})

    @api.model
    def _set_done(self, lesson):
        rec = self._get(lesson)
        if rec.state != "done":
            rec.write({"state": "done", "done_date": fields.Datetime.now()})
        return rec

    @api.model
    def offer_tour_once(self, lesson_key):
        """True the first time only: the «جولة دقيقتين؟» prompt is offered once per user."""
        lesson = self.env["resta.growth.lesson"].sudo().search([("key", "=", lesson_key)], limit=1)
        if not lesson:
            return False
        rec = self._get(lesson)
        if rec.tour_offered or rec.state == "done":
            return False
        rec.tour_offered = True
        return True

    @api.model
    def _done_lesson_ids(self, user=None):
        user = user or self.env.user
        return set(self.sudo().search([("user_id", "=", user.id), ("state", "=", "done")]).lesson_id.ids)


class WebTourTour(models.Model):
    _inherit = "web_tour.tour"

    @api.model
    def consume(self, tourName):
        if tourName and tourName.startswith("resta_growth_"):
            self.env["resta.growth.lesson"]._mark_tour_done(tourName)
            return False  # never chain Odoo's own onboarding tours after ours
        return super().consume(tourName)

    @api.model
    def get_current_tour(self):
        # Restaurants see only Resta's Arabic tours, not Odoo's generic onboarding ones.
        return False
