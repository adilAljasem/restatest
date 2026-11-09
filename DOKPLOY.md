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
| `docker/traefik-letsencrypt-dns.example.yml` | مقطع يُدمج في إعداد Traefik الثابت لإصدار شهادة wildcard من Let's Encrypt عبر Cloudflare DNS |

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
   | A | `@` | `SERVER_IP` | DNS only (رمادي) | Auto |
   | A | `*` | `SERVER_IP` | DNS only (رمادي) | Auto |

   سجل `*` يغطي `test1.restaerp.com` وبقية العملاء؛ لا تحتاج سجل DNS لكل عميل. السجلات المحددة مثل `test1` تتقدم على wildcard، فراجع أي سجلات قديمة متعارضة.
3. **الطريقة المستخدمة في compose: Let's Encrypt بتحدي DNS-01.** تعمل مع السحابة الرمادية، وتصدر شهادة موثوقة للمتصفح تشمل `restaerp.com` و `*.restaerp.com`. نفّذ إعداد Traefik التالي **قبل نشر compose المحدّث**:
   1. في Cloudflare ← My Profile ← API Tokens أنشئ Custom Token بصلاحيتَي **Zone / DNS / Edit** و **Zone / Zone / Read**، والنطاق المسموح **Include / Specific zone / restaerp.com**.
   2. في Dokploy افتح إعداد **Traefik للسيرفر الذي يستضيف التطبيق**، ثم قسم Environment الخاص بـ Traefik، وأضف `CF_DNS_API_TOKEN=YOUR_TOKEN` مع الإبقاء على المتغيرات الموجودة. لا تضعه في Environment لخدمة Odoo؛ Traefik هو الذي يتصل بـ Cloudflare. لا تحفظ التوكن في Git أو ترسله في المحادثة.
   3. افتح إعداد Traefik الثابت `traefik.yml` (عادةً `/etc/dokploy/traefik/traefik.yml` على السيرفر). ادمج محتوى [مثال DNS-01](docker/traefik-letsencrypt-dns.example.yml) تحت `certificatesResolvers` الموجود، واستبدل البريد ببريد مسؤول الشهادات:

      ```yaml
      certificatesResolvers:
        # أبقِ letsencrypt وأي resolvers أخرى موجودة كما هي.
        letsencrypt-dns:
          acme:
            email: YOUR_EMAIL@example.com
            storage: /etc/dokploy/traefik/dynamic/acme-resta-dns.json
            dnsChallenge:
              provider: cloudflare
              resolvers:
                - "1.1.1.1:53"
                - "8.8.8.8:53"
      ```

      لا تكرّر مفتاح `certificatesResolvers` ولا تستبدل الملف كاملاً؛ أبقِ إعدادات `entryPoints` و `providers` ومُصدِّر `letsencrypt` القديم. هذا إعداد **ثابت**؛ وضعه في ملف داخل مجلد `dynamic` لن يعرّف resolver.
   4. تأكد أن مسار `storage` داخل حاوية Traefik مربوط بمجلد دائم على السيرفر وقابل للكتابة. في تثبيت Dokploy الافتراضي مجلد `/etc/dokploy/traefik/dynamic` مربوط بنفس المسار. تحقّق منه بأمر:

      ```bash
      docker inspect dokploy-traefik --format '{{json .Mounts}}'
      ```

      إذا كانت المسارات الافتراضية مطابقة، جهّز الملف على السيرفر دون مسح محتواه الموجود:

      ```bash
      touch /etc/dokploy/traefik/dynamic/acme-resta-dns.json
      chmod 600 /etc/dokploy/traefik/dynamic/acme-resta-dns.json
      ```

      إذا كان Traefik يعمل بمستخدم مخصص، يجب أن يملك صلاحية الكتابة على الملف. لا تحذف ملف ACME القديم؛ الشهادات الحالية تعتمد عليه.
   5. احفظ، ثم استخدم **Reload / Restart Traefik** في Dokploy حتى تُطبّق الإعدادات الثابتة ومتغيرات البيئة الجديدة. `docker restart` وحده لا يضيف متغير بيئة إلى حاوية موجودة. راقب السجل وتأكد من عدم وجود خطأ `nonexistent certificate resolver`.
   6. انشر compose المحدّث؛ labels الشهادة مفعّلة بالفعل على `resta-web`. سيطلب الشهادة مرة واحدة، ويستخدمها أيضاً مسار `resta-ws` لنفس النطاق. تحدي HTTP الافتراضي لا يصدر wildcard، و`HostRegexp` وحده يوجّه الطلبات ولا يحدد أسماء الشهادات.
4. يمكن الإبقاء على **DNS only**؛ المتصفح يتصل مباشرة بـ Traefik ويستخدم شهادة Let's Encrypt. إذا اخترت **Proxied** لاحقاً، اضبط Cloudflare على **Full (strict)** وانتظر تفعيل Universal SSL؛ الشهادة الصادرة هنا صالحة أيضاً للاتصال بين Cloudflare والسيرفر.
5. **لا تضف** دومين المنصة من تبويب Domains في Dokploy: التوجيه معرَّف في الـ labels، وإضافته من التبويب تكرره. تأكد أن Cloudflare يستطيع الوصول إلى السيرفر على المنفذ `443`.
6. في Environment ضع `RESTA_DOMAIN=restaerp.com` ثم أعد النشر. إذا كان النطاق غير `restaerp.com`، استبدله أيضاً في الـ labels الثلاثة وأسطر الشهادة في compose.

المراجع: [Wildcard DNS](https://developers.cloudflare.com/dns/manage-dns-records/reference/wildcard-dns-records/)، [Origin CA](https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/)، [Full (strict)](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/)، [تغطية Universal SSL](https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/limitations/).

### تشخيص شهادة TRAEFIK DEFAULT CERT

ظهورها يعني أن Traefik لم يحمّل شهادة تطابق اسم النطاق المطلوب. شهادة `restaerp.com` وحدها لا تغطي `r100001.restaerp.com`. اختبر بعد النشر من جهاز خارج السيرفر، **دون** `-k`:

```bash
curl -I https://restaerp.com/odoo/
curl -I https://r100001.restaerp.com/odoo/
docker logs --since 15m dokploy-traefik
```

يجب أن ينجح تحقق TLS على الرابطين. في تفاصيل شهادة العميل يجب أن يظهر `*.restaerp.com` ضمن Subject Alternative Names؛ لا تكتفِ باسم CN. استجابة HTTP من نوع 200 أو إعادة توجيه متوقعة تدل على وصول الطلب للتطبيق؛ 404/502 بعد نجاح TLS تحتاج فحص التوجيه أو صحة خدمة Odoo.

إذا استمرت الشهادة الافتراضية، راجع في سجل Traefik: اسم resolver مطابق تماماً لـ `letsencrypt-dns`، صلاحيات التوكن على النطاق، إمكانية كتابة ملف التخزين، وأخطاء ACME أو انتشار TXT. تأكد أن عنوانَي A وAAAA (إن وُجدا) وأي سجل خاص بالعميل يشيران للسيرفر الصحيح. تغيير الشهادة لا يتطلب تعديل Dockerfile أو كود Odoo.

**بديل عند استخدام Cloudflare Proxied فقط:** يمكنك استخدام شهادة Origin CA تشمل النطاق الرئيسي وwildcard، ثم رفعها في Dokploy ← Certificates وتسجيل `certFile` و`keyFile` في `tls.certificates` ضمن إعداد Traefik الديناميكي. عند اختيار هذا البديل، علّق labels الثلاثة `resta-web.tls.certresolver` و`resta-web.tls.domains` في compose، وأبقِ `tls=true`. يجب أن تكون السحابة برتقالية وSSL على **Full (strict)**؛ شهادة Origin CA غير موثوقة لدى المتصفح عند الاتصال المباشر بالسيرفر مع DNS only. رفع الشهادة في Dokploy وحده لا يضمن تحميلها في Traefik؛ راجع [شهادات Dokploy](https://docs.dokploy.com/docs/core/certificates).

المراجع الفنية: [إصدار wildcard في Traefik](https://doc.traefik.io/traefik/https/acme/)، [صلاحيات توكن Cloudflare لـ DNS-01](https://go-acme.github.io/lego/dns/cloudflare/)، [إعداد ومتغيرات Traefik في Dokploy](https://docs.dokploy.com/docs/api/settings).

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
