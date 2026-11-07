# منصة ريستا SaaS: التشغيل والنشر

## الفكرة

```
resta.sa ──────────────► قاعدة "resta" (المنصة)  ← resta_saas_master
   الموقع التسويقي · التسجيل · الدخول برقم الحساب · الإدارة · الاشتراكات · التحديثات
        │ تجهيز تلقائي (CREATE DATABASE … TEMPLATE)
        ▼
r100002.resta.sa ──────► قاعدة "r100002" (مطعم)  ← النسخة + resta_saas_tenant
r100003.resta.sa ──────► قاعدة "r100003" (مطعم)
        ▲  مزامنة كل 30 دقيقة، وعند فتح النظام إذا مضت 10 دقائق:
        └─ الحالة، مدير الحساب، التحديثات ⇄ الاستخدام، التقييمات، الاقتراحات
```

- **القوالب:** `resta_tpl_dinein` و `resta_tpl_qsr` قاعدتان جاهزتان، معلَّمتان `IS_TEMPLATE` في PostgreSQL، فلا تظهران للعملاء ولا تعمل عليهما مهام الـ cron.
- **التجهيز:** المطعم الجديد نسخة من قالب نسخته، وينتهي خلال 2-5 ثوانٍ تقريباً. بعدها يُضبط اسم الشركة ومستخدم المالك وكلمة مروره (تُنقل كبصمة hash فقط ولا تُحفظ كنص) ومفتاح الربط.
- **الدخول:** صفحة `/resta/login` تأخذ رقم الحساب، وتوقّع طلباً صالحاً 5 دقائق بمفتاح المطعم، ثم يُرسل البريد وكلمة المرور مباشرة إلى نظام المطعم دون أن يمرا بالمنصة.

## المتطلبات (مهم)

**Odoo.sh لا يسمح بإنشاء قواعد بيانات تلقائياً**، فالمنصة تحتاج خادماً خاصاً بكم (VPS أو سحابة):
- Ubuntu 24.04، و PostgreSQL 16، و Python 3.12+، و Odoo 19 من هذه الحزمة.
- مستخدم PostgreSQL للمنصة بصلاحية `CREATEDB`.
- DNS: سجل wildcard `*.resta.sa` يشير للخادم، مع `resta.sa`.
- شهادة SSL wildcard لـ `*.resta.sa` (Let's Encrypt بتحقق DNS أو Cloudflare).

## odoo.conf

```ini
[options]
addons_path = /opt/resta/odoo/addons,/opt/resta/enterprise,/opt/resta/resta_addons,/opt/resta/bs_addons
db_user = resta
dbfilter = ^%d$            ; أول جزء من النطاق = اسم القاعدة (resta.sa → resta، r100002.resta.sa → r100002)
list_db = False            ; إخفاء مدير قواعد البيانات
proxy_mode = True
workers = 9                ; تقريباً (2 × الأنوية) + 1
max_cron_threads = 2       ; المهام تعمل على كل قواعد المطاعم
limit_memory_soft = 2147483648
limit_memory_hard = 2684354560
db_maxconn = 64
```

## nginx

```nginx
server {
    listen 443 ssl http2;
    server_name resta.sa *.resta.sa;
    ssl_certificate     /etc/ssl/resta/fullchain.pem;
    ssl_certificate_key /etc/ssl/resta/privkey.pem;
    client_max_body_size 64m;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    location /websocket { proxy_pass http://127.0.0.1:8072; proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade"; }
    location / { proxy_pass http://127.0.0.1:8069; proxy_read_timeout 720s; }
}
server { listen 80; server_name resta.sa *.resta.sa; return 301 https://$host$request_uri; }
server { listen 443 ssl; server_name www.resta.sa; return 301 https://resta.sa$request_uri; }
```

## الإعداد لأول مرة

```bash
# 1) المنصة
odoo -c odoo.conf -d resta -i resta_saas_master,l10n_sa --without-demo=all --load-language=ar_001 --stop-after-init

# 2) القالبان (كل نسخة + موديول الربط)
odoo -c odoo.conf -d resta_tpl_dinein -i resta_edition_dinein,resta_saas_tenant --without-demo=all --load-language=ar_001 --stop-after-init
odoo -c odoo.conf -d resta_tpl_qsr    -i resta_edition_qsr,resta_saas_tenant    --without-demo=all --load-language=ar_001 --stop-after-init
psql -c "ALTER DATABASE resta_tpl_dinein WITH IS_TEMPLATE true"
psql -c "ALTER DATABASE resta_tpl_qsr    WITH IS_TEMPLATE true"
```

3) من **منصة ريستا ← الإعدادات ← إعدادات المنصة**:
- رابط المطعم: `https://{db}.resta.sa`
- رابط المنصة: `https://resta.sa`
- أرقام الدعم وواتساب.

4) أضف موظفي خدمة العملاء لمجموعة **منصة ريستا / مدير حساب**. يوزَّع العملاء الجدد بالتناوب على الأقل حملاً.

5) عدّل **الباقات** وأسعارها. الأسعار الحالية أمثلة فقط.

6) اضبط خادم البريد الصادر في المنصة ليصل بريد الترحيب ورقم الحساب.

## تحديث القوالب والمطاعم

- **تحديث القالب:** فعّل اتصال القالب، ثم حدّث الموديولات، ثم أعده قالباً:
  ```bash
  psql -c "ALTER DATABASE resta_tpl_qsr WITH IS_TEMPLATE false"
  odoo -c odoo.conf -d resta_tpl_qsr -u all --stop-after-init
  psql -c "ALTER DATABASE resta_tpl_qsr WITH IS_TEMPLATE true"
  ```
- **تحديث المطاعم القائمة:** شغّل `-u <modules>` على كل قاعدة `r*` (سكربت يمر على `SELECT db_name FROM resta_tenant WHERE state IN ('trial','active')`).
  وانشر بعدها «تحديث» من **التواصل ← التحديثات والرسائل** ليصل لكل المطاعم.

## التواصل مع المطاعم

| من المنصة | ماذا يحدث في نظام المطعم |
|---|---|
| نشر تحديث (ميزة، تحسين، إصلاح) | يظهر في لوحة «آخر التحديثات» مع رقم غير مقروء على الأيقونة |
| تحديث «هام» (صيانة مثلاً) | نافذة منبثقة عند الدخول، مرة واحدة لكل مستخدم |
| رسالة لمطاعم محددة أو حسب النسخة أو الحالة | تصل للمستهدفين فقط |
| تفعيل الاشتراك، تمديد التجربة، إيقاف | الشارة في الشريط العلوي، ونافذة الانتهاء أو الإيقاف، وإيقاف نقاط البيع |

| من المطعم | يصل إلى |
|---|---|
| تقييم تحديث بالوجوه + ملاحظة | **الاقتراحات والتقييمات**، ومتوسط التقييم على التحديث |
| «اقترح ميزة» | **الاقتراحات والتقييمات** مع نشاط لمدير الحساب |
| الاستخدام (المستخدمون، نقاط البيع، الطلبات والمبيعات 30 يوم، آخر دخول) | بطاقة المطعم ← الاستخدام |

## الأمان

- **التسجيل:** حقل فخ للبوتات (honeypot)، وحد للتسجيل لكل IP بالساعة، ورفض البريد المكرر، وكلمة مرور 8 أحرف على الأقل.
  - قبل الإطلاق: أضف Cloudflare Turnstile، وتحقق برسالة OTP على الجوال.
- **مفتاح الربط:** لكل مطعم مفتاحه الخاص، ويظهر لمدير المنصة فقط.
- **قواعد البيانات:** `list_db = False`. القوالب لا يصل إليها أحد.
- **النسخ الاحتياطي:** `pg_dump` يومي لكل قاعدة، مع نسخ مجلد filestore.

## الترخيص

Odoo Enterprise (OEEL-1) يتطلب اشتراكاً سارياً لكل قاعدة بيانات Enterprise، ولا يسمح بإعادة توزيع الكود المعدَّل.
نموذج «قاعدة لكل مطعم» يعني اشتراكاً لكل مطعم، أو اتفاق شراكة/SaaS مع أودو. يلزم حسم هذا قبل الإطلاق التجاري.
