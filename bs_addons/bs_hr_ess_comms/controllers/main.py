from werkzeug.exceptions import NotFound

from odoo import _, fields, http
from odoo.http import request

from odoo.addons.bs_hr_ess_base.controllers.main import EssPortal, EssPortalBase


class EssCommsPortal(EssPortalBase):

    def _ess_get_announcement(self, ann_id, employee):
        """Announcement visible for the employee, 404 otherwise (sudo record)."""
        announcement = request.env['bs.hr.announcement'].sudo().browse(int(ann_id)).exists()
        if not announcement or not announcement.is_current or not announcement._is_recipient(employee):
            raise NotFound()
        # display in the employee's language
        return announcement.with_context(lang=request.env.lang)

    @http.route(['/my/hr/news'], type='http', auth='user', website=True)
    def ess_news(self, filter='all', **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        Announcement = request.env['bs.hr.announcement'].with_context(lang=request.env.lang)
        announcements = Announcement._ess_visible_for(employee)
        acks = request.env['bs.hr.announcement.ack'].sudo().search(
            [('announcement_id', 'in', announcements.ids), ('employee_id', '=', employee.id)])
        read_ids = set(acks.filtered('read_date').announcement_id.ids)
        acked_ids = set(acks.filtered('ack_date').announcement_id.ids)
        if filter == 'unread':
            announcements = announcements.filtered(lambda a: a.id not in read_ids)
        elif filter == 'to_ack':
            announcements = announcements.filtered(lambda a: a.require_ack and a.id not in acked_ids)
        return self._ess_render('bs_hr_ess_comms.ess_news', employee, announcements=announcements,
                                read_ids=read_ids, acked_ids=acked_ids, filter=filter,
                                flash=self._ess_pop_flash(), page_name='ess_news')

    @http.route(['/my/hr/news/<int:ann_id>'], type='http', auth='user', website=True)
    def ess_news_detail(self, ann_id, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        announcement = self._ess_get_announcement(ann_id, employee)
        ack = announcement._ess_mark_read(employee)
        attachments = announcement.attachment_ids
        tokens = {att.id: att.sudo().generate_access_token()[0] for att in attachments}
        back = '/my/hr/policies' if announcement.announcement_type == 'policy' else '/my/hr/news'
        return self._ess_render('bs_hr_ess_comms.ess_news_detail', employee, announcement=announcement,
                                ack=ack, attachments=attachments, tokens=tokens, back=back,
                                flash=self._ess_pop_flash(), page_name='ess_news')

    @http.route(['/my/hr/news/<int:ann_id>/ack'], type='http', auth='user', website=True, methods=['POST'])
    def ess_news_ack(self, ann_id, **kw):
        employee = self._ess_employee()
        if not employee:
            raise NotFound()
        announcement = self._ess_get_announcement(ann_id, employee)
        if not announcement.require_ack:
            raise NotFound()
        announcement._ess_acknowledge(employee, ip=request.httprequest.remote_addr)
        return self._ess_ok_redirect('/my/hr/news/%s' % announcement.id,
                                     _('Thank you, your acknowledgement has been recorded.'))

    @http.route(['/my/hr/news/<int:ann_id>/image'], type='http', auth='user', sitemap=False)
    def ess_news_image(self, ann_id, **kw):
        employee = self._ess_employee()
        if not employee:
            raise NotFound()
        announcement = self._ess_get_announcement(ann_id, employee)
        if not announcement.image:
            raise NotFound()
        return request.env['ir.binary']._get_image_stream_from(announcement, 'image').get_response()

    @http.route(['/my/hr/policies'], type='http', auth='user', website=True)
    def ess_policies(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        policies = request.env['bs.hr.announcement'].with_context(lang=request.env.lang)._ess_visible_for(
            employee, [('announcement_type', '=', 'policy')])
        acks = request.env['bs.hr.announcement.ack'].sudo().search(
            [('announcement_id', 'in', policies.ids), ('employee_id', '=', employee.id)])
        acked_ids = set(acks.filtered('ack_date').announcement_id.ids)
        return self._ess_render('bs_hr_ess_comms.ess_policies', employee,
                                policies=policies.sorted(lambda p: p.name or ''), acked_ids=acked_ids,
                                page_name='ess_policies')

    @http.route(['/my/hr/calendar'], type='http', auth='user', website=True)
    def ess_calendar(self, **kw):
        employee = self._ess_employee()
        if not employee:
            return self._ess_no_employee()
        holidays = employee._ess_public_holidays()
        events = request.env['bs.hr.announcement'].with_context(lang=request.env.lang)._ess_visible_for(
            employee, [('announcement_type', '=', 'event')])
        today = fields.Date.context_today(employee)
        return self._ess_render('bs_hr_ess_comms.ess_calendar', employee, holidays=holidays,
                                events=events, celebrations=employee._ess_celebrations(today),
                                show_celebrations=employee.company_id.ess_show_birthdays,
                                to_date=lambda dt: fields.Datetime.context_timestamp(employee, dt).date(),
                                page_name='ess_calendar')


class EssPortalHomeComms(EssPortal):
    """Inject the latest pinned announcement on the portal home page."""

    @http.route()
    def ess_home(self, **kw):
        response = super().ess_home(**kw)
        employee = self._ess_employee()
        if employee and getattr(response, 'qcontext', None) is not None:
            pinned = request.env['bs.hr.announcement'].with_context(lang=request.env.lang)._ess_visible_for(
                employee, [('pinned', '=', True)])
            response.qcontext['ess_pinned_announcement'] = pinned[:1]
        return response
