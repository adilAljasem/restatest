# -*- coding: utf-8 -*-
{
    'name': "Pino POS Receipt & Kitchen Ticket Design",
    'summary': "تصميم مخصص لفاتورة نقطة البيع وإيصال المطبخ (KOT) قابل للتحكم من إعدادات نقطة البيع",
    'description': """
تصميم فاتورة ومطبخ نقطة البيع - بينو
=====================================
- تصميم مخصص لفاتورة المبيعات في نقطة البيع (أودو 19).
- تصميم مخصص لإيصال المطبخ (KOT) المرسل إلى طابعات الطلبات.
- كل تصميم يمكن تشغيله أو إيقافه بشكل مستقل من إعدادات نقطة البيع،
  وعند الإيقاف يعود أودو تلقائياً إلى التصميم الافتراضي.
- دعم الأسماء ثنائية اللغة (عربي / لاتيني)، رمز QR الخاص بهيئة الزكاة
  والضريبة والجمارك، وباركود Code128 يُولَّد محلياً بدون إنترنت.

بنية مسطحة: كل ملفات الموديول في جذره عدا مجلد static، حتى لا يضيع أي
مجلد فرعي أثناء النسخ أو الرفع إلى المستودع.
""",
    'author': "Resta",
    "website": "https://resta.sa",
    'category': 'Sales/Point of Sale',
    'version': '19.0.2.0.1',
    'license': 'LGPL-3',
    'depends': ['point_of_sale', 'pos_restaurant'],
    'data': [
        'pino_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pino_pos_receipt/static/src/pino_receipt.css',
            'pino_pos_receipt/static/src/pino_receipt.js',
            'pino_pos_receipt/static/src/kitchen_data.js',
            'pino_pos_receipt/static/src/pino_order_receipt.xml',
            'pino_pos_receipt/static/src/customer_phone.xml',
            'pino_pos_receipt/static/src/pino_kitchen_receipt.xml',
        ],
        'web.assets_unit_tests': [
            'pino_pos_receipt/static/tests/unit/**/*.test.js',
        ],
    },
    'installable': True,
    'application': False,
}
