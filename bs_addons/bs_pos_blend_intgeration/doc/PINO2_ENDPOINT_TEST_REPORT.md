# تقرير اختبار Blend على قاعدة `pino2`

تاريخ آخر تشغيل كامل: 8 سبتمبر 2026 (بتوقيت القاهرة)

## النتيجة النهائية

- إصدار الموديول المثبت والمختبر: `19.0.1.10.0`.
- Odoo automated tests: نجحت `49/49` test methods (`57` checks في إحصاء Odoo)، بدون failures أو errors.
- Postman/Newman: نجحت `18/18` requests و`53/53` assertions، بدون failures.
- فحص Get Menu الكامل على بيانات `pino2`: `14` categories و`111` products، وكل IDs الناتجة رقمية (`0` invalid IDs).
- Python compilation وXML parsing وPostman JSON parsing: ناجحة.
- تسجيل الدخول بـ email/password أعاد `access_token`، والـCollection حفظته تلقائيًا ثم أرسلته في طلبات Odoo كـ
  `Authorization: Bearer {{odoo_inbound_token}}`.
- طلبات Odoo إلى Mock Blend حملت `Authorization: Bearer pino2-postman-outbound-token` ونجحت برمز `HTTP 200`.

## العقد النهائي المنفذ

- `item.updated.is_stock` يقبل JSON Boolean فقط: `true` أو `false`، ويرفض النصوص مثل `"false"`.
- `store.updated.status` يقبل JSON Boolean فقط: `true` أو `false`، ويرفض `0/1` والنصوص.
- IDs الخاصة بالـcategory/product/modifier group/modifier في Get Menu تخرج JSON numbers.
- `menu.updated` اتجاهه POS → Blend فقط. إذا وصل إلى Odoo كـ inbound event يتم رفضه بوضوح، ولا ينفذ Odoo أي menu pull عكسي.
- عند وصول `menu.updated` إلى Blend، Blend هو الذي يستدعي Get Menu في Odoo لجلب القائمة المحدثة.
- يتم اشتقاق الخصم غير المرسل من Blend بالمعادلة `subtotal + tax - total`، مع رفض الإجماليات التي تنتج خصمًا سالبًا أو أكبر من `subtotal`.
- `POST /api/orders` يطلب ويخزن `order_type` و`aggregator` و`aggregator_order_id`، مع بقاء `customer` اختياريًا.
- يغطي اختبار Create Order حالة خصم ضمني بالقيم `subtotal=100` و`tax=13.5` و`total=103.5`، ويحفظ Odoo خصمًا بقيمة `10` دون طرحه مرتين من إجماليات المنتجات النهائية.

## Endpoints والأحداث المختبرة

| Direction | Method | Endpoint / event | Result |
|---|---:|---|---:|
| Blend → Odoo | POST | `/api/login` | 200 + `access_token` |
| Blend → Odoo | POST | `/api/blend/webhook` - connection/auth check | 200 |
| Blend → Odoo | POST | `/api/blend/webhook` - `menu_sync` | 200 |
| Blend → Odoo | POST | `/api/blend/webhook` - `item_availability` | 200 |
| Blend → Odoo | POST | `/api/blend/webhook` - `modifier_availability` | 200 |
| Blend → Odoo | POST | `/api/blend/webhook` - `branch_availability` | 200 |
| Blend → Odoo | POST | `/api/blend/webhook` - `order_updated` | 200 |
| Blend → Odoo | GET | `/api/menu` | 200 + numeric menu IDs |
| Blend → Odoo | GET | `/api/branches` | 200 |
| Blend → Odoo | POST | `/api/orders` | 200 + POS order ID |
| Blend → Odoo | PUT | `/api/order/update` | 200 |
| Alternate Blend → Odoo | POST | `item.updated` | 200 |
| Alternate Blend → Odoo | POST | `store.updated` | 200 |
| Alternate Blend → Odoo | POST | `order.updated` | 200 |
| Odoo → Blend | POST | `order_created` | 200 |
| Odoo → Blend | POST | `order_updated` / `order.updated` | 200 |
| Odoo → Blend | POST | `menu.updated` | 200 |

وتغطي اختبارات Odoo كذلك: Bearer مفقود أو خاطئ، login خاطئ، JSON غير صالح، event time غير صالح،
Boolean types غير مطابقة، branch/product/modifier/order غير معروف، product غير متاح، schema/status غير صالح،
idempotency والطلبات المتزامنة، branch isolation، schedules، modifier limits، timeout وHTTP errors وretry limits،
HTTPS enforcement، وإعادة queue operation العالقة بعد توقف العملية.

## تشغيل Postman

1. استورد `doc/Blend_Sandbox.postman_collection.json`.
2. افتح الـCollection ثم **Variables**.
3. ضع دومين Odoo فقط في `odoo_domain`؛ لا تكتب method أو endpoint path معه.
4. ضع بيانات POS service account في `pos_login_email` و`pos_login_password`.
5. شغّل الـCollection كاملة، أو ابدأ بطلب
   `00 - Authorization (Required) / 00 - Login with Email & Password`.
6. طلب login يحفظ الـtoken تلقائيًا في `odoo_inbound_token`؛ لا تكتب email/password في Authorization tab.

عينة البيانات التالية هي التي استُخدمت في آخر اختبار `pino2` المحلي:

- Email: `pos_user@pino2.local`
- POS Config: ID `38`، `[TEST] Blend Postman pino2`
- Branch ID: `14589`
- Brand ID: `5000`
- POS Session: ID `207` وحالتها `opened`
- Sample IDs: category `10`، item `100`، modifier group `20`، modifier `200`

لا يحتوي ملف الـCollection الحالي على email أو password أو Bearer token محفوظ. يجب وضع حساب وsecrets البيئة المستهدفة في Current Values داخل Postman، وعدم حفظ أسرار Production في Git.

## اختيار قاعدة البيانات والدومين

- الـCollection الحالية Domain Only: تحتوي `odoo_domain` فقط، ولا تحتوي متغير database أو database header.
- كل روابط Odoo تُبنى تلقائيًا كـ `{{odoo_domain}}/api/...`.
- الـpre-request script ينظف المسافات وmethod زائدة، ويضيف `https://` إذا أُدخل اسم الدومين بدونه.
- Odoo.sh يحدد قاعدة البيانات من الدومين تلقائيًا. وفي الاستضافة الذاتية يجب ضبط `dbfilter` و`list_db = False` على السيرفر؛ Blend لا يرسل اسم قاعدة البيانات.

## ملاحظات السجل المحلي

- لا توجد أخطاء من `bs_pos_blend_intgeration` في التشغيل النهائي.
- Odoo كتب DEBUG عن غياب قاعدة GeoIP الاختيارية، وWARNING عن غياب `pdfminer.six` لفهرسة PDF. كلاهما خارج مسار Blend ولا يؤثر في endpoints أو نتائج الاختبار.
- الإعداد الأمني المقصود هو `blend.allow_insecure_urls = 0`، حتى يرفض Odoo outbound URLs غير HTTPS. HTTP localhost مستخدم في الـmock المحلي فقط.

## ما يتبقى لاختبار Blend Sandbox الخارجي

النتائج السابقة هي end-to-end محلي بين Odoo وقاعدة `pino2` وMock Blend مطابق للعقد. لا يمكن اعتبار سيرفر Blend الخارجي مختبرًا قبل استلام:

- Sandbox account credentials من Blend.
- روابط Blend webhook/API الحقيقية وBearer token للاتجاه الصادر.
- إضافة public outbound IP الخاص بسيرفر Odoo إلى Blend whitelist.
- Public HTTPS domain لـOdoo مع TLS وreverse proxy مضبوطين.

بعد استلام هذه القيم، تُستبدل قيمة `odoo_domain` والـtest secrets وروابط Blend فقط وتُعاد نفس الـCollection؛ لا يلزم تغيير endpoint أو payload في الكود.

## النسخ الاحتياطية السابقة

- `D:\odoo\odoo19\backups\pino2_before_blend_auth_20260907_221543.dump`
- `D:\odoo\odoo19\backups\pino2_before_blend_id_normalization_20260907_235658.dump`
