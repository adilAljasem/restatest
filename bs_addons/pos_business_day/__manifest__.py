# -*- coding: utf-8 -*-
{
    'name': 'Business Day for POS Sales & Inventory Reports',
    'version': '19.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'يربط تقارير مبيعات نقاط البيع وحركات المخزون بإعدادات وقت الدوام (اليوم التشغيلي/المحاسبي)',
    'description': """
Business Day (Operational Day) for POS & Inventory
==================================================

يعالج هذا الموديول حالة الأنشطة التي يمتد دوامها لما بعد منتصف الليل
(مثال: دوام من 6 صباحاً حتى 2 صباحاً اليوم التالي)، حيث تُنسب مبيعات
نقاط البيع وحركات المخزون التي تتم بين منتصف الليل وساعة بداية الدوام
إلى "يوم العمل" السابق بدلاً من التاريخ التقويمي الفعلي.

المزايا
-------

* إعداد "ساعة بداية اليوم التشغيلي" على مستوى الشركة.
* حقل محسوب ومخزّن "Business Date" على طلبات نقطة البيع (pos.order).
* حقل محسوب ومخزّن "Business Date" على حركات المخزون (stock.move).
* تقارير Pivot/Graph جاهزة لمبيعات نقاط البيع وحركات المخزون مجمّعة حسب اليوم التشغيلي.
* إمكانية إعادة احتساب اليوم التشغيلي للسجلات القديمة بعد تغيير الإعداد (مهمة خلفية على دفعات).

متوافق مع Odoo 19.0.
""",
    'author': "Resta",
    "website": "https://resta.sa",
    'license': 'LGPL-3',
    'depends': ['point_of_sale', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/business_day_settings_views.xml',
        'views/business_day_recompute_job_views.xml',
        'views/pos_order_views.xml',
        'views/pos_business_day_report_views.xml',
        'views/stock_business_day_report_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
