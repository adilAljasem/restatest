# Resta SaaS — نسخة أودو 19 مخففة للمطاعم

## الحجم

| | قبل | بعد |
|---|---|---|
| Community | 642 موديول · 1.4 GB | 126 موديول · 157 MB |
| Enterprise | 788 موديول · 758 MB | 68 موديول · 24 MB |
| قاعدة بيانات بعد التثبيت | — | داين إن 217 موديول (~100 MB) · وجبات سريعة 250 موديول |

الموديولات الأساسية: المحاسبة (accountant + تقارير السعودية + ZATCA)، المخزون (+ الباركود والصلاحية)،
المشتريات، المبيعات، التصنيع (الوصفات/BoM لمطابخ الإنتاج)، نقطة البيع للمطاعم (KDS، الطلب الذاتي QR، الولاء،
الموظفين، تسوية الآجل، تطبيق الجوال)، والموارد البشرية مع بوابة الموظف، والموقع.

تم حذف ما يلي: الاختبارات، وكل الترجمات عدا العربي والإنجليزي، وجميع التوطينات غير السعودية، والـ CRM، والمشاريع، والـ IoT، و Studio، و Urban Piper، وأجهزة الدفع غير المستخدمة في السعودية
(Adyen, Stripe, Viva, Razorpay, Tyro…).

بوابات الدفع الإلكتروني المتبقية (اختيارية): APS (Amazon Payment Services — تدعم مدى) و Paymob و Custom.

## الهيكل

```
odoo/            ← سيرفر Odoo 19 Community المخفف
enterprise/      ← Enterprise المخفف
resta_addons/
  resta_theme_suite      ← الثيم على POS/المطبخ/الطلب الذاتي/الموقع/البوابة/التقارير
  resta_saas_base        ← القاعدة: مطعم دائماً + إخفاء خيارات التجزئة + إعدادات السعودية
  resta_edition_dinein   ← نسخة الداين إن (نموذج بينو)
  resta_edition_qsr      ← نسخة الوجبات السريعة والمقاهي (نموذج بن فاستك / بلص)
bs_addons/       ← موديولات بيسك سلوشن (79) + الثيم + bs_hr_ess
tools/
  depgraph.py      ← يحسب الموديولات المطلوبة مع الاعتماديات وموديولات auto_install
  build_slim.sh    ← يبني النسخة المخففة من سورس أودو الكامل (أعد تشغيله مع كل تحديث لأودو)
```

## التشغيل

```bash
# نسخة الداين إن
python3 odoo/odoo-bin --addons-path=odoo/addons,enterprise,resta_addons,bs_addons \
  -d client_db -i resta_edition_dinein --without-demo=all --load-language=ar_001

# نسخة الوجبات السريعة والمقاهي
... -i resta_edition_qsr
```

عند التثبيت يتم تلقائياً ما يلي: تحميل شجرة الحسابات السعودية وضريبة 15% والريال، وتفعيل اللغة العربية،
وإنشاء نقطة البيع بإعداداتها، وتثبيت موديولات بيسك سلوشن إذا كانت موجودة في الـ addons path.

**خطوة يدوية لكل عميل:** ربط دفتر الفواتير بزاتكا (OTP من بوابة فاتورة). نقطة البيع لن تفتح قبل هذه الخطوة،
وهذا سلوك أودو الطبيعي.

## ما الذي يتغير في نقطة البيع (resta_saas_base)

- كل نقطة بيع هي نقطة بيع مطعم، وتم إخفاء خيار "Is a Bar/Restaurant".
- اختفت كروت "ملابس / أثاث / مخبز / تجزئة" من الشاشة الأولى، وبقي كرتان فقط: مطعم، ووجبات سريعة ومقاهي.
- من الإعدادات أُخفيت: Ship Later، والإيصال بدون أسعار (هدايا)، وماسح الباركود، و Pricer، وحجز المواعيد،
  و Avatax، و SMS، و IoT Box، وأجهزة الدفع، و Urban Piper.
- أنواع طلب إضافية: تطبيقات التوصيل، وطلبات السيارة (Drive-thru).

## النسختان

| | داين إن | وجبات سريعة ومقاهي |
|---|---|---|
| الشاشة الافتراضية | الطاولات | الكاشير مباشرة |
| الصالات | قسم العوائل (8 طاولات) + قسم الأفراد (8) | بدون |
| الطلب الذاتي | QR على الطاولة: طلب ودفع بعد الوجبة | قائمة QR للعرض فقط |
| شاشات التحضير | المطبخ + بار المشروبات | المطبخ + بار القهوة (مسح تلقائي بعد 5 دقائق) |
| شاشة أرقام الطلبات للعملاء | — | ✓ (pos_order_tracking_display) |
| قائمة حسب الوقت | — | فئة الفطور تظهر من 6 إلى 12 ظهراً |
| طباعة الفاتورة قبل الدفع / تقسيم الفاتورة | ✓ | — |

### موديولات بيسك سلوشن (`bs_addons/`)

جُمعت من مستودعات بينو وبن باستك وبلص في مجلد واحد، بنسخة واحدة لكل موديول:
- القاعدة: أعلى رقم إصدار.
- إذا تطابق رقم الإصدار واختلف الكود، فالاختيار يدوي:
  - `inventory_stock_adjustments` و `stock_request`: نسخة بينو (إصلاح الترحيل بتاريخ سابق، واختبارات أكثر) مع مجلد migrations من بن باستك.
  - `pos_config_controls`: نسخة بينو (فيها قاعدة أمان إضافية على التحويلات). شاشة no_order_screen الموجودة في نسخة بن باستك غير مدموجة.
  - `pos_analytic_account`: نسخة بلص (القيد بتاريخ يوم العمل للجلسة).
  - **فرق يحتاج قرارك:** نسخة بن باستك من `inventory_stock_adjustments` فيها قيد يمنع أن تكون الكمية المعدودة أقل من الكمية المحجوزة، وهذا القيد غير موجود في نسخة بينو.

تُثبَّت الموديولات تلقائياً مع النسخة إذا كانت موجودة في الـ addons path، ويمكن تعديل القوائم من System Parameters.

**مشتركة (`resta.common_modules`)**: الثيم، وبوابة HR، وتقارير الإغلاق، ويوم العمل، وتذكرة المطبخ، والإيصالات، وصلاحيات الموظفين، و ZATCA async، و the_dash، وطلبات المخزون، والجرد…

| موديول | المصدر |
|---|---|
| `resta_sidebar_theme` | upload resta_sidebar_theme_odoo19.zip |
| `resta_sidebar_theme_enterprise` | upload resta_sidebar_theme_odoo19.zip |
| `pos_business_day` | pino 19.0.1.0.0 |
| `pos_closing_report` | pino 19.0.2.1.0 |
| `pos_closing_report_hr` | pino 19.0.1.0.0 |
| `pos_kitchen_ticket_custom` | pino 19.0.1.4.0 |
| `pino_pos_receipt` | pino 19.0.2.0.1 |
| `odoo_pos_custom_branding` | pino 19.0.1.9.1 |
| `bi_pos_multi_layout` | pino 19.0.0.0.0 |
| `pos_control_button_resize` | pino 19.0.1.0.0 |
| `pos_ticket_pager_ipad` | pino 19.0.1.0.0 |
| `pos_set_default_customer` | pino 19.0.1.0.0 |
| `pos_config_controls` | pino 19.0.1.0.0 |
| `dk_pos_employee_access_rights` | pino 19.0.1.4.0 |
| `employee_loyalty_limit` | pino 19.0.1.2.0 |
| `kds_order_timer` | pino 19.0.3.0.0 |
| `pos_kds_sound` | pino 19.0.1.0.0 |
| `l10n_sa_edi_pos_async` | pino 19.0.1.0.2 |
| `pos_analytic_account` | puls 19.0.1.1.0 |
| `hide_menu_user` | puls 19.0.2.0.1 |
| `inventory_stock_adjustments` | pino 19.0.1.7 |
| `stock_request` | pino 19.0.1.50.11 |
| `the_dash` | pino 19.0.3.4.0 |

**داين إن (`resta.dinein_modules`)، من بينو:**

| موديول | المصدر |
|---|---|
| `product_modifiers` | pino 19.0.1.0.5 |
| `bs_pos_blend_intgeration` | pino 19.0.1.37.0 |
| `bs_pos_blend_intgeration_zatca` | pino 19.0.1.0.0 |
| `bs_pos_session_handover` | pino 19.0.1.3.0 |
| `pos_shared_floor_sync` | pino 19.0.1.0.0 |
| `pos_table_opened_by` | pino 19.0.1.0.1 |
| `bs_pos_kitchen_flow` | pino 19.0.1.0.1 |
| `bs_pos_kitchen_bilingual` | pino 19.0.1.0.0 |
| `bs_pos_products_by_category_only` | pino 19.0.1.0.3 |
| `bs_pos_customer_search_autofill` | pino 19.0.1.0.0 |
| `bs_pos_refund_guard` | pino 19.0.1.0.0 |
| `pos_ticket_partner_phone` | pino 19.0.1.0.0 |
| `pos_hide_products` | pino 19.0.1.0.0 |
| `exclusive_customer_discount` | pino 19.0.1.2.0 |
| `loyalty_reward_description_kanban` | pino 19.0.1.0.0 |
| `pos_analytic_tag_custom` | pino 19.0.1.0.1 |
| `stock_quant_qty_guard` | pino 19.0.1.0.0 |
| `pos_bonat_loyalty_online` | pino 19.0.1.1.0 |

**وجبات سريعة ومقاهي (`resta.qsr_modules`)، من بن باستك وبلص:**

| موديول | المصدر |
|---|---|
| `pos_restaurant_combo_modifiers` | bunbastic 19.0.2.1.1 |
| `bs_pos_delivery_core` | bunbastic 19.0.2.30.0 |
| `bs_pos_delivery_blend` | bunbastic 19.0.1.18.0 |
| `bs_pos_delivery_tryorder` | bunbastic 19.0.1.3.0 |
| `bs_pos_kitchen_resend` | bunbastic 19.0.1.1.0 |
| `pos_kitchen_send_before_payment` | bunbastic 19.0.1.2.0 |
| `pos_call_center` | bunbastic 19.0.2.0.2 |
| `pos_disable_decimal` | bunbastic 19.0.1.0.0 |
| `pos_order_tracking_nps` | puls 19.0.1.4.0 |
| `pos_order_tracking_nps_hr` | puls 19.0.1.0.0 |
| `pos_bonat_loyalty_online` | pino 19.0.1.1.0 |
| `pos_cash_management` | puls 19.0.2.0.0 |
| `pos_blind_closing` | puls 19.0.1.1.0 |
| `pos_cashier_shortage_report` | puls 19.0.1.0.0 |
| `bs_pos_payment_clearing` | puls 19.0.1.4.0 |
| `pos_deferred_invoice_pdf` | puls 19.0.1.0.0 |
| `batch_scrap_orders` | bunbastic 19.0.1.2.0 |
| `mrp_enhancement` | puls 19.0.1.0.0 |
| `mrp_bom_consumption_adjustment` | puls 19.0.1.2.2 |
| `custom_stock_ageing_report` | puls 19.0.1.1.1 |
| `product_category_inventory_location` | puls 19.0.1.0.0 |
| `bs_recurring_purchase_agreement` | puls 19.0.1.0.0 |
| `bs_analytic_center_profit_loss` | puls 19.0.1.0.0 |
| `bs_analytic_distribution_rate` | puls 19.0.1.0.0 |
| `bs_company_vendor_bill_validation` | puls 19.0.1.0.3 |
| `petty_cash` | puls 19.0.1.0.0 |
| `employee_loan_management` | puls 19.0.1.0.3 |
| `uom_aretx` | bunbastic 19.0.1.0.0 |

**مستبعدة من الـ SaaS:**

| موديول | السبب |
|---|---|
| `pino_panel` | Beno-only panel (recruiting, invitations); needs CRM/helpdesk/sign/slides |
| `pino_theme` | Beno brand identity, replaced by resta_sidebar_theme |
| `pino_backend_custom` | Beno production customisations; needs sign |
| `pino_pos_custom` | Beno production POS customisations |
| `pino_dash_guard` | Guard for the Beno board snapshot |
| `pino_calories` | Calories computed for the Beno menu (candidate to generalise for SFDA) |
| `bi_hide_show_menu_app` | Duplicate of hide_menu_user (23 MB) |
| `codusic_pos_order_types` | Odoo 19 presets replace order types |
| `order_connector` | Old TryOrder connector, replaced by bs_pos_delivery_tryorder (Connect) |

> تنبيه: موديولات بن باستك وبلص لم تعمل مع بعض في قاعدة بيانات واحدة قبل هذه النسخة. التثبيت ناجح وبدون أخطاء،
> لكن يلزم اختبار وظيفي للمتداخل منها (pos_cash_management و pos_blind_closing و pos_closing_report في شاشة الإغلاق).

### الموارد البشرية (bs_hr_ess) والبوابة والموقع

- `bs_hr_ess` بكل أجزائه: الحضور بالوجه والكشك، والطلبات، والوثائق، والتعاميم، والشكاوى، والتقييم، والتقارير، والربط مع POS، على بوابة `/my/hr`.
- أُضيف للبناء: portal و website و hr_attendance و hr_holidays و hr_expense و hr_hourly_cost و contacts،
  والرواتب السعودية (hr_payroll + l10n_sa_hr_payroll، للتأمينات) كخيار. `bs_hr_ess_payroll` و `employee_loan_management` يحتاجان الرواتب.

### الثيم في كل الواجهات

| الواجهة | كيف |
|---|---|
| الواجهة الخلفية وتسجيل الدخول | `resta_sidebar_theme` (+ enterprise) |
| نقطة البيع، شاشة المطبخ، شاشة العميل، شاشة تتبع الطلبات | `resta_theme_suite` ← خط IBM Plex Sans Arabic + tokens في `point_of_sale.base_app` (الألوان تصل من متغيرات الثيم) |
| الطلب الذاتي (QR / كشك) | `resta_theme_suite` ← متغيرات تسبق بنفسجي أودو في `pos_self_order.assets` |
| الموقع والبوابة (بما فيها HR) | `resta_theme_suite` ← لوحة ألوان الموقع الافتراضية = Resta، مع الخط وتنسيق الأزرار والبطاقات |
| تقارير PDF | `resta_theme_suite` ← الخط العربي في `web.report_assets_common` |
| موديولاتكم | `tools/rebrand.py` ← استبدال بنفسجي وتركوازي أودو (والتركوازي الخاص ببوابة HR وألوان The Dash) بألوان Resta. ألوان الحالات (نجاح/تحذير/خطأ) وألوان الشركاء (Blend، TryOrder) لم تتغير |

**إصلاح في الثيم (19.0.1.5.1):** `resta_sidebar_theme_enterprise` كان يستخدم `remove` ثم `prepend`. عند تثبيت
website يُضمَّن ملف المتغيرات مرتين في الحزمة، فينتقل ملف الثيم إلى ما بعد متغيرات أودو، وترجع الأزرار بنفسجية.
استُبدلا بـ `before`. **انقل هذا التعديل لمستودعكم.**

### لوحة البيانات: The Dash 19.0.4.0

صارت The Dash لوحة البيانات الرئيسية للنظام، وتربط كل التطبيقات بثيم ريستا:
- **المدخل:** أول عنصر في القائمة الجانبية (تحت «نظرة عامة»)، وتفتح داخل النظام مع الشريط الجانبي. يوجد أيضاً عنصر «ملء الشاشة» لشاشات الحائط.
  لوحات أودو (Spreadsheet) نُقلت تحتها باسم «لوحات الجداول».
- **قسم العمليات الجديد (`/thedash/ops`):** له زر تحديث مستقل كبقية الأقسام الثقيلة، وتبويب لكل تطبيق مثبت:
  المخزون، المشتريات، المحاسبة، الإنتاج والوصفات، الموظفون (مع طلبات bs_hr_ess وتكلفة العمالة كنسبة من المبيعات)، رضا الضيوف (NPS من pos_order_tracking_nps).
  التطبيق غير المثبت يُخفى قسمه، والقسم الذي يفشل يعرض خطأه دون أن يؤثر على البقية.
- **من كل تطبيق:** عنصر «لوحة البيانات» في تقارير المخزون والمشتريات والمحاسبة والتصنيع والموظفين والحضور والمبيعات ونقاط البيع، ويفتح على التبويب المناسب.
  التطبيقات التي تُثبَّت لاحقاً يُضاف لها العنصر تلقائياً.
- **الثيم:** الوضع الفاتح افتراضي بألوان ريستا، والداكن مطابق لوضع ريستا الداكن، وخط IBM Plex Sans Arabic، وشعار R.
- **مكتبة الرسوم:** Chart.js أصبح يُحمَّل من نسخة أودو المحلية بدل CDN خارجي.

> ⚠️ نسختا داين إن والوجبات السريعة لا تُثبَّتان على نفس قاعدة البيانات: Blend القديم (بينو) و Connect (بن باستك)
> ينشئان مستخدم Blend بنفس اسم الدخول. كل عميل يأخذ نسخة واحدة.


## منصة الـ SaaS (التسجيل الذاتي وقاعدة لكل مطعم)

- `resta_saas_master`: الموقع التسويقي، والتسجيل التجريبي الذي ينشئ قاعدة لكل مطعم، والدخول برقم الحساب، والمطاعم والاشتراكات والفواتير، ومدراء الحسابات، وطلبات العرض، والتحديثات والرسائل، والاقتراحات والتقييمات.
- `resta_saas_tenant`: داخل كل مطعم. المزامنة، ولوحة «آخر التحديثات» مع «اقترح ميزة» وتقييم بالوجوه، وشارة التجربة، وإيقاف نقاط البيع عند الانتهاء، والدخول من resta.sa.

التشغيل والنشر في **DEPLOY_SAAS.md**.

## مقارنة مع Toast

| خاصية Toast | الوضع في Resta |
|---|---|
| POS يعمل بدون إنترنت | ✓ أودو POS (offline) |
| KDS | ✓ شاشات تحضير لكل قسم |
| Mobile Order & Pay | ✓ QR على الطاولة (داين إن)؛ للكاونتر: غيّر الوضع إلى "mobile" مع APS |
| Self-ordering kiosk | ✓ متاح في pos_self_order (وضع Kiosk)، يحتاج جهاز دفع مدمج |
| Handheld (Toast Go) | ✓ pos_mobile / pos_hr_mobile |
| Order-ready board | ✓ نسخة الوجبات السريعة |
| Menu dayparts | ✓ ساعات الفئة (hour_after / hour_until) |
| Loyalty / Gift cards | ✓ pos_loyalty + Bonat |
| Catering & events | ✓ عروض أسعار المبيعات تُسحب من POS (pos_sale) |
| Third-party delivery | ✓ Connect (Blend / FeedUs / TryOrder) |
| Inventory + xtraCHEF cost | ✓ مخزون + وصفات BoM + المشتريات + سيخ (تكلفة الجرام الفعلية) |
| Multi-location / Toast Now | ✓ شركات وفروع + لوحة البيانات (The Dash) مربوطة بكل التطبيقات |
| **ناقص: Reservations (Toast Tables)** | يحتاج موديول حجوزات خفيف (appointment محذوف لثقله) |
| Scheduling / Payroll | ✓ bs_hr_ess (حضور بالوجه، إجازات، سلف) + رواتب سعودية اختيارية |
| **ناقص: Guest CRM + SMS/Email campaigns** | ملف عميل موحد من bs_pos_customer_lookup + Bonat |
| **ناقص: Forecasting / Benchmarking / AI (Toast IQ)** | امتداد لقسم العمليات في The Dash |
| **ناقص: Online ordering site + branded app** | website موجود الآن؛ متجر الطلب أونلاين (website_sale) غير مضاف |

## ملاحظة الترخيص

Enterprise مرخص بـ OEEL-1: التعديل مسموح، لكن كل قاعدة بيانات Enterprise تحتاج اشتراك أودو ساري،
ولا يجوز إعادة توزيع الكود المعدَّل. يُنصح بمراجعة نموذج الـ SaaS مع شريك أودو أو مستشار قانوني.
