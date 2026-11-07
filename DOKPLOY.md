# تثبيت ريستا على Dokploy

الملف موجّه لمهندس السيرفرات. صاحب القرار يتحقق فقط من قسم «التحقق» في آخره.

## ما الذي في المستودع

| الملف | دوره |
|---|---|
| `Dockerfile` | صورة واحدة: Ubuntu 24.04 + Odoo 19 المخفف + Enterprise + موديولات ريستا وبيسك سلوشن + خطوط عربية للتقارير + rtlcss + wkhtmltopdf + rclone |
| `docker-compose.yml` | ثلاث خدمات: `postgres` و `odoo` (المنصة وكل المطاعم) و `backup` |
| `docker/entrypoint.sh` | ينشئ `odoo.conf` من المتغيرات، ينتظر قاعدة البيانات، ويشغّل التهيئة الأولى |
| `docker/resta-init.sh` | أول تشغيل: قاعدة المنصة + قالبا النسختين (`IS_TEMPLATE`) + إعدادات الروابط. آمن للتكرار |
| `docker/resta-backup.sh` | نسخة يومية لكل قاعدة + ملفاتها، احتفاظ يومي/أسبوعي/شهري، نسخة خارجية بـ rclone، وتنبيه |
| `docker/resta-restore.sh` | استرجاع قاعدة واحدة من نسخة، مع التحقق من سلامة الملفات |
| `docker/resta-upgrade.sh` | تحديث موديولات على المنصة والقوالب وكل المطاعم |
| `.env.example` | كل المتغيرات مع قيم مقترحة لسيرفر Contabo Cloud VPS 30 |

## الفرق عن ملف الـ compose المرفق

| المرفق (ALKPI) | ريستا | السبب |
|---|---|---|
| `ports: 5432:5432` | لا منفذ لقاعدة البيانات | كان المنفذ مفتوحاً للإنترنت. الآن قاعدة البيانات داخل الشبكة فقط |
| `prodrigestivill/postgres-backup-local` لقاعدة واحدة | خدمة نسخ تكتشف كل قواعد المطاعم تلقائياً + ملفات المرفقات | كل مطعم قاعدة مستقلة، ومرفقات أودو خارج قاعدة البيانات |
| النسخ داخل نفس السيرفر فقط | + نسخة خارجية (Backblaze B2) + تنبيه Uptime Kuma | يحمي من فقد السيرفر نفسه |
| الدومين من تبويب Domains | labels لـ Traefik بنطاق شامل `*.restaerp.com` | تبويب الدومين لا يغطي نطاقاً فرعياً لكل مطعم |
| ‎`ports: "80"`‎ | `expose` | Dokploy يوصي بـ expose حتى لا تُفتح منافذ على السيرفر |

## الخطوات

### 1. السيرفر و Dokploy
1. سيرفر Ubuntu 24.04. المقترح: Contabo Cloud VPS 30، أي 8 أنوية و 24GB رام.
2. ثبّت Dokploy كما في [توثيقه الرسمي](https://docs.dokploy.com)، ثم افتح لوحته وأنشئ حساب المدير.
3. فعّل التحقق بخطوتين في Dokploy. اربطه بـ GitHub (حساب الشركة).

### 2. المستودع
1. مستودع **خاص** على GitHub باسم الشركة، فيه محتوى هذا المجلد كما هو: `odoo/` و `enterprise/` و `resta_addons/` و `bs_addons/` وملفات Docker.
2. كود Enterprise لا يُرفع لمستودع عام أبداً.

### 3. DNS و SSL (Cloudflare)
1. أضف `restaerp.com` إلى Cloudflare، وغيّر nameservers عند مسجّل النطاق إلى القيم التي يحددها Cloudflare، وانتظر أن تصبح حالة النطاق **Active**.
2. من **DNS → Records** أضف السجلين التاليين، مع استبدال `SERVER_IP` بعنوان IPv4 العام لسيرفر Dokploy:

   | Type | Name | IPv4 address | Proxy status | TTL |
   |---|---|---|---|---|
   | A | `@` | `SERVER_IP` | Proxied (برتقالي) | Auto |
   | A | `*` | `SERVER_IP` | Proxied (برتقالي) | Auto |

   سجل `*` يغطي `test1.restaerp.com` وبقية العملاء؛ لا تحتاج سجل DNS لكل عميل. السجلات المحددة مثل `test1` تتقدم على wildcard، فراجع أي سجلات قديمة متعارضة.
3. اختر طريقة واحدة للشهادة:
   - **(أ) الأبسط: شهادة Cloudflare Origin.**
     1. في Cloudflare: SSL/TLS ← Origin Server ← Create Certificate لـ `restaerp.com` و `*.restaerp.com`.
     2. في Dokploy: Certificates ← أضف الشهادة بصيغة PEM والمفتاح على السيرفر الذي يعمل عليه Traefik. تأكد أن Traefik حمّل الشهادة؛ رفع الملفات وحده قد لا يفعّلها. إذا لم يُنشأ تعريفها تلقائياً، أضف ملفاً في مجلد إعداد Traefik الديناميكي الذي يراقبه file provider، بالقيم الفعلية لمسارات الشهادة داخل حاوية Traefik:

        ```yaml
        tls:
          certificates:
            - certFile: /actual/path/to/chain.crt
              keyFile: /actual/path/to/privkey.key
        ```

        راجع [شهادات Dokploy](https://docs.dokploy.com/docs/core/certificates) و[تعريف الشهادات في Traefik](https://doc.traefik.io/traefik/reference/routing-configuration/http/tls/tls-certificates/). لا تضع المفتاح الخاص في Git.
     3. في Cloudflare: اجعل وضع SSL/TLS على **Full (strict)**، والسجلات مُفعّلاً عليها الوكيل (السحابة البرتقالية).
   - **(ب) Let's Encrypt بتحدي DNS.**
     1. أضف في إعداد Traefik داخل Dokploy مُصدِّر شهادات `letsencrypt-dns` يستخدم Cloudflare، مع متغير `CF_DNS_API_TOKEN`.
     2. ألغِ التعليق عن الأسطر الثلاثة `tls.certresolver` و `tls.domains` في `docker-compose.yml`.
4. في **SSL/TLS → Edge Certificates** انتظر تفعيل Universal SSL، وفعّل **Always Use HTTPS**. شهادة Universal SSL تغطي النطاق الرئيسي والمستوى الأول مثل `test1.restaerp.com` في إعداد nameservers الكامل. شهادة Origin تعمل بين Cloudflare والسيرفر، لذلك أبقِ الوكيل مفعّلاً.
5. **لا تضف** دومين المنصة من تبويب Domains في Dokploy: التوجيه معرَّف في الـ labels، وإضافته من التبويب تكرره. تأكد أن Cloudflare يستطيع الوصول إلى السيرفر على المنفذ `443`.
6. في Environment ضع `RESTA_DOMAIN=restaerp.com` ثم أعد النشر. إذا كان النطاق غير `restaerp.com`، استبدله أيضاً في الـ labels الثلاثة وأسطر الشهادة في compose.

المراجع: [Wildcard DNS](https://developers.cloudflare.com/dns/manage-dns-records/reference/wildcard-dns-records/)، [Origin CA](https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/)، [Full (strict)](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/)، [تغطية Universal SSL](https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/limitations/).

### نقل تثبيت قائم وفيه بيانات إلى النطاق الجديد

1. خذ نسخة فورية: `docker exec resta_backup resta-backup now`.
2. أبقِ نفس خدمة Compose ونفس volumes. **لا تغيّر اسم قاعدة المنصة** عند تغيير الدومين. في Environment:

   ```dotenv
   RESTA_DOMAIN=restaerp.com
   RESTA_MASTER_DB=deemo
   RESTA_AUTO_INIT=true
   ```

   `deemo` هو اسم قاعدة المنصة المتوقع من compose السابق (`deemo.resta.sa`). إذا كان اسم قاعدتك الحالية `resta` أو غيره، ضع الاسم الفعلي. يعرض الأمر التالي القواعد الموجودة:

   ```bash
   docker exec resta_postgres psql -U odoo -d postgres -Atc "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY datname"
   ```

3. انشر الكود المحدّث. `RESTA_MASTER_DB` يربط `restaerp.com` بقاعدة المنصة الحالية. عند تشغيل `resta-init` وتغيّر رابط المنصة، يُحدّث رابط المنصة ورابط كل قاعدة عميل مسجلة فيها، مع الإبقاء على بيانات المطاعم وأسماء قواعدها. أبقِ `RESTA_MASTER_DB` مضبوطاً في عمليات النشر التالية أيضاً.
4. حدّث واجهات المنصة المخزنة في قاعدة البيانات: `docker exec resta_odoo resta-upgrade resta_saas_master deemo` (استبدل `deemo` باسم قاعدة المنصة الفعلي). اختبر الدخول إلى المنصة، وعميل قائم مثل `r100002.restaerp.com`، ومزامنة الاشتراك وPOS. سيحتاج المستخدمون إلى تسجيل الدخول على النطاق الجديد، وأعد إصدار روابط QR والإشارات المحفوظة التي تشير للنطاق القديم.

النظام ينشئ أسماء العملاء حالياً برقم الحساب مثل `r100002.restaerp.com`. مثال `test1.restaerp.com` يفتح قاعدة باسم `test1` إذا كانت مجهزة؛ wildcard لا ينشئ قاعدة بيانات ولا يضيف اختيار اسم مخصص في التسجيل.

### 4. إنشاء الخدمة في Dokploy
1. أنشئ Project ← Create Service ← **Compose**.
   - المصدر: GitHub ← المستودع.
   - الفرع: `main`.
   - مسار الملف: `docker-compose.yml`.
2. في **Environment**: انسخ محتوى `.env.example`، وغيّر كل `change-me` إلى كلمات مرور عشوائية طويلة. احفظها في مدير كلمات المرور.
3. فعّل **Autodeploy** إذا تريد أن يُنشر كل دفع (push) على `main` تلقائياً.
4. اضغط **Deploy**.
   - بناء الصورة: 10 إلى 15 دقيقة.
   - أول تشغيل ينشئ المنصة والقالبين: حوالي 5 دقائق.
   - تابع السجل (Logs) لخدمة `odoo` حتى يظهر `[resta-init] ready`.

### 5. بعد أول تشغيل
1. افتح `https://restaerp.com/web/login` وادخل:
   - المستخدم: `admin`
   - كلمة المرور: `admin`
2. **غيّر كلمة المرور فوراً.**
3. من منصة ريستا ← الإعدادات: راجع أرقام الدعم والباقات. رابط المطعم والمنصة ضُبطا تلقائياً من `RESTA_DOMAIN`.
4. أضف مدراء الحسابات، واضبط خادم البريد الصادر (Brevo أو SES).

### 6. النسخ الاحتياطي والتنبيهات
1. **Backblaze B2:**
   1. أنشئ Bucket خاصاً باسم `resta-backups`.
   2. أنشئ Application Key محصوراً على هذا الـ bucket.
   3. ضع المفتاحين في `B2_KEY_ID` و `B2_APPLICATION_KEY`.
2. **Uptime Kuma** (يمكن تثبيته من قوالب Dokploy):
   - مراقِب HTTP على `https://restaerp.com/web/health`، ومراقِب على مطعم تجريبي.
   - مراقِب **Push**: ضع رابطه في `BACKUP_NOTIFY_URL`، وفعّل أن يتأخر 26 ساعة. إذا ما وصلته نبضة النسخة الليلية، يصلك تنبيه.
   - اربط التنبيهات بتيليجرام أو واتساب.
3. النسخ تُحفظ على السيرفر في `/root/resta-backups` بثلاثة مستويات، ونسخة منها في B2:
   - يومي، يُحتفظ به 7 أيام.
   - أسبوعي، يُحفظ يوم الجمعة ويُحتفظ به 4 أسابيع.
   - شهري، يُحفظ يوم 1 ويُحتفظ به 6 أشهر.
4. نسخة فورية بأي وقت:
   ```bash
   docker exec resta_backup resta-backup now
   ```

### 7. الاسترجاع (جرّبه شهرياً)
```bash
# قائمة النسخ
ls /root/resta-backups/daily/
# استرجاع مطعم كنسخة جانبية للتحقق (r100002_restored)
docker exec -it resta_odoo resta-restore /backups/daily/2026-11-07_0230 r100002
# استبدال قاعدة المطعم الحية (بعد التأكد)
docker exec -it resta_odoo resta-restore /backups/daily/2026-11-07_0230 r100002 r100002 --force
```
سلامة الملفات تُفحص قبل أي استرجاع، ولا تُستبدل قاعدة موجودة بدون `--force`.

### 8. التحديثات
1. ادفع التعديل إلى GitHub. ينشر Dokploy الصورة الجديدة، والكود الجديد يصبح فعالاً.
2. إذا تغيّرت حقول أو واجهات في موديول، حدّثه على كل القواعد:
   ```bash
   docker exec resta_odoo resta-upgrade the_dash,resta_saas_tenant
   ```
3. يتوقف السكربت عند أول خطأ. القواعد المتبقية لا تُمس.
4. خذ نسخة فورية (`resta-backup now`) قبل أي تحديث.

## التحقق (لصاحب القرار)
- [ ] `https://restaerp.com` يفتح بالقفل الأخضر
- [ ] تسجيل مطعم تجريبي يعطي رقم حساب خلال دقيقة، ويفتح `https://r1000xx.restaerp.com`
- [ ] Dokploy ← خدمة `backup` حالتها **healthy** بعد أول ليلة
- [ ] مجلد النسخة الليلية ظاهر في Backblaze
- [ ] تجربة استرجاع ناجحة أمامك (القسم 7)
- [ ] وصلك تنبيه Uptime Kuma عندما أُوقفت خدمة `odoo` عمداً

## السعة التقريبية
- سيرفر 24GB بإعدادات `.env.example`: يكفي للتجربة وعشرات المطاعم الصغيرة.
- كل قاعدة مطعم فارغة حوالي 75MB.
- عند النمو: ارفع `ODOO_WORKERS` و `PG_SHARED_BUFFERS`، أو انقل PostgreSQL لسيرفر مستقل.
