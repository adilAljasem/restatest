"""Bilingual labels of the closing reports.

The report language is chosen per Point of Sale at print time
(Closing Report settings), so the labels do not depend on the language of the
user who prints and work even when Arabic is not installed in Odoo.
"""

LABELS = {
    # Titles
    "cashier_title": ("Cashier Closing Report", "تقرير إغلاق الكاشير"),
    "day_title": ("Business Day Report", "تقرير اليوم التشغيلي"),
    # Header
    "company": ("Company", "الشركة"),
    "branch": ("Point of Sale", "نقطة البيع"),
    "branches": ("Points of Sale", "نقاط البيع"),
    "employee": ("Employee", "الموظف"),
    "session": ("Session", "الجلسة"),
    "opening_date": ("Opening Date", "تاريخ الفتح"),
    "closing_date": ("Closing Date", "تاريخ الإغلاق"),
    "business_date": ("Business Date", "اليوم التشغيلي"),
    "period": ("Working Hours", "وقت العمل"),
    "from": ("From", "من"),
    "to": ("To", "إلى"),
    "printed_at": ("Printed At", "وقت الطباعة"),
    "printed_by": ("Printed By", "طُبع بواسطة"),
    # Sales summary
    "sales_summary": ("Sales Summary", "ملخص المبيعات"),
    "orders_count": ("Orders", "عدد الطلبات"),
    "gross_sales": ("Sales", "المبيعات"),
    "refunds_total": ("Refunds", "المرتجعات"),
    "net_sales": ("Net Sales", "صافي المبيعات"),
    "taxes": ("Taxes", "الضرائب"),
    "discounts": ("Discounts", "الخصومات"),
    "average_ticket": ("Average Ticket", "متوسط الفاتورة"),
    "guests": ("Guests", "عدد الضيوف"),
    # Payments / count
    "payments": ("Payments", "طرق الدفع"),
    "cash_count": ("Closing Count", "جرد الإغلاق"),
    "payment_method": ("Payment Method", "طريقة الدفع"),
    "type": ("Type", "النوع"),
    "cash": ("Cash", "نقدي"),
    "bank": ("Bank", "بنك"),
    "opening": ("Opening Balance", "رصيد الافتتاح"),
    "expected": ("Expected", "المتوقع"),
    "counted": ("Counted", "الفعلي"),
    "difference": ("Difference", "الفرق"),
    "total_expected": ("Total Expected", "إجمالي المتوقع"),
    "total_counted": ("Total Counted", "إجمالي الفعلي"),
    "total_difference": ("Total Difference", "إجمالي الفرق"),
    "total": ("Total", "الإجمالي"),
    "amount": ("Amount", "المبلغ"),
    "count": ("Count", "العدد"),
    "status": ("Status", "الحالة"),
    "balanced": ("Balanced", "متوازن"),
    "shortage": ("Shortage", "عجز"),
    "surplus": ("Surplus", "زيادة"),
    "open": ("Open", "مفتوحة"),
    "closing": ("Closing", "قيد الإغلاق"),
    "closed": ("Closed", "مغلقة"),
    "retro_note": (
        "* Counted amount not stored in Odoo: assumed equal to expected.",
        "* المبلغ الفعلي غير محفوظ في النظام: اعتُبر مساوياً للمتوقع.",
    ),
    "open_note": (
        "Session still open: counted amounts are not available yet.",
        "الجلسة ما زالت مفتوحة: المبالغ الفعلية غير متوفرة بعد.",
    ),
    # Items
    "sold_items": ("Sold Items", "الأصناف المباعة"),
    "item": ("Item", "الصنف"),
    "qty": ("Qty", "الكمية"),
    "items_total": ("Items Total", "إجمالي الأصناف"),
    # Refunds / cancellations
    "refunds": ("Refunds", "المرتجعات"),
    "refunded_items": ("Refunded Items", "الأصناف المرتجعة"),
    "refund_orders": ("Refund Orders", "طلبات الاسترداد"),
    "cancellations": ("Cancellations", "الإلغاءات"),
    "cancelled_orders": ("Cancelled Orders", "الطلبات الملغاة"),
    "order": ("Order", "الطلب"),
    "none": ("None", "لا يوجد"),
    "more": ("more", "أخرى"),
    # Sessions (day report)
    "sessions": ("Sessions", "الجلسات"),
    "sessions_count": ("Sessions", "عدد الجلسات"),
    "cash_difference": ("Cash Difference", "فرق النقدي"),
    "bank_difference": ("Bank Difference", "فرق البنك"),
    # Signatures
    "employee_signature": ("Employee Signature", "توقيع الموظف"),
    "manager_signature": ("Manager Signature", "توقيع المدير"),
}


def resolve_lang_mode(mode, env_lang):
    """'user' follows the language of the user printing the report."""
    if mode in ("ar", "en", "both"):
        return mode
    return "ar" if (env_lang or "").startswith("ar") else "en"


def get_labels(mode):
    """({key: label}, {key: second label}) in the report language.

    'both' prints the Arabic label with the English one under it (second dict);
    the second dict is empty for a single language.
    """
    if mode in ("ar", "both"):
        primary = {key: ar for key, (en, ar) in LABELS.items()}
    else:
        primary = {key: en for key, (en, ar) in LABELS.items()}
    secondary = {key: en for key, (en, ar) in LABELS.items()} if mode == "both" else {}
    return primary, secondary


def lang_code(mode):
    """Odoo language used to read product / method names for this report language."""
    return "ar_001" if mode in ("ar", "both") else "en_US"
