# -*- coding: utf-8 -*-
{
    'name': 'POS Control Buttons - Resize Height',
    'version': '19.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'تصغير ارتفاع أزرار اللوحة الجانبية / شريط الأزرار في نقطة البيع (Odoo 19)',
    'description': """
تصغير ارتفاع أزرار التحكم (control buttons) في نقطة البيع في أودو 19،
سواء الأزرار الظاهرة في شاشة المنتجات أو الأزرار داخل نافذة "المزيد" (More)،
بدون تعديل أي كود بايثون أو قوالب JS — فقط CSS مخصص يُحقن ضمن حزمة أصول
نقطة البيع (point_of_sale._assets_pos).

ملاحظة عن أودو 19: لم تعد أزرار التحكم تستخدم الكلاس ".control-button"،
بل أصبحت أزرار Bootstrap عادية (btn btn-secondary btn-lg) داخل الحاوية
".control-buttons"، لذلك تم تحديث المُحدّدات (selectors) في هذا الإصدار.
""",
    'author': "Resta",
    "website": "https://resta.sa",
    'depends': ['point_of_sale'],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_control_button_resize/static/src/scss/control_buttons.scss',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
