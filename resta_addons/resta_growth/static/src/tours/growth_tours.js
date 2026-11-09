/** Guided tours of the growth journey (Odoo 19 web_tour, interactive "manual" mode).
 *  Each step waits for the owner to do the action on the highlighted element. */
import { markup } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

const tours = registry.category("web_tour.tours");

tours.add("resta_growth_welcome", {
    url: "/odoo/growth",
    steps: () => [
        {
            trigger: ".o_growth_btn",
            content: markup(_t("<b>شارة مستواك</b><br/>الرقم مستواك، والدائرة نسبة تقدمك للمستوى التالي. اضغطها.")),
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            trigger: ".o_growth_panel .o_growth_conds",
            content: markup(_t("هذه <b>شروط مستواك</b>. كل شرط له زر «ماذا أفعل؟» يشرح لك الخطوة.")),
            tooltipPosition: "left",
            run: "click",
        },
        {
            trigger: ".o_growth_panel .o_growth_lines + div .btn",
            content: _t("ومن هنا تفتح رحلتك كاملة."),
            tooltipPosition: "top",
            run: "click",
        },
        {
            trigger: ".o_growth_road .o_growth_station:nth-child(3)",
            content: markup(_t("<b>خريطة الرحلة</b>: خمس محطات، وكل محطة تفتح لك أداة جديدة. اضغط على أي محطة لتعرف جائزتها.")),
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            trigger: ".o_growth_tabs .o_growth_tab:nth-child(2)",
            content: markup(_t("و<b>مؤشر الانضباط</b> من 1000: يرتفع كل ما انضبطت بياناتك، ومعه تقل تكلفة اشتراكك.")),
            tooltipPosition: "bottom",
            run: "click",
        },
    ],
});

tours.add("resta_growth_l1_product", {
    url: "/odoo/action-point_of_sale.product_template_action_pos_product",
    steps: () => [
        {
            trigger: ".o_control_panel .o-kanban-button-new, .o_control_panel .o_list_button_add",
            content: _t("نبدأ بإضافة صنف جديد للمنيو."),
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            trigger: ".o_field_widget[name=name] textarea, .o_field_widget[name=name] input",
            content: _t("اكتب اسم الصنف كما يقرأه الزبون."),
            tooltipPosition: "bottom",
            run: "edit برجر كلاسيك",
        },
        {
            trigger: ".o_field_widget[name=list_price] input",
            content: _t("سعر البيع. راجع درس تسعير الأطباق إن احتجت."),
            tooltipPosition: "bottom",
            run: "edit 25",
        },
        {
            trigger: ".o_field_widget[name=taxes_id] input",
            content: _t("الضريبة: اختر ضريبة القيمة المضافة 15%."),
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            trigger: ".o_notebook .nav-link[name=pos]",
            content: _t("افتح تبويب نقطة البيع لاختيار التصنيف."),
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            trigger: ".o_field_widget[name=pos_categ_ids] input",
            content: _t("اختر تصنيف الصنف (وجبات، مشروبات…) حتى يظهر في مكانه على الكاشير."),
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            trigger: ".o_form_button_save",
            content: _t("احفظ. كل صنف مكتمل يقربك من شرط «المنيو كامل»."),
            tooltipPosition: "bottom",
            run: "click",
        },
    ],
});

tours.add("resta_growth_l1_session", {
    url: "/odoo/action-point_of_sale.action_pos_config_kanban",
    steps: () => [
        {
            trigger: ".o_kanban_record:not(.o_kanban_ghost) .o_kanban_record_title, .o_kanban_record:not(.o_kanban_ghost) .fw-bold",
            content: markup(_t("هذه بطاقة <b>الكاشير</b>. كل يوم تشغيل يبدأ بفتح جلسة وينتهي بإغلاقها.")),
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            trigger: ".o_kanban_record:not(.o_kanban_ghost) button.btn-primary",
            content: markup(_t("صباحاً: اضغط هنا لفتح الجلسة وأدخل النقد الافتتاحي.<br/>مساءً: من نفس المكان «إغلاق»، وعُدّ النقد الفعلي وأدخله كما هو.")),
            tooltipPosition: "bottom",
            run: "click",
        },
    ],
});

tours.add("resta_growth_l1_einvoice", {
    url: "/odoo/action-point_of_sale.action_pos_pos_form",
    steps: () => [
        {
            trigger: ".o_searchview_input",
            content: _t("هذه قائمة طلباتك. ابحث برقم الطلب أو التاريخ."),
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            trigger: ".o_data_row:first-child .o_data_cell",
            content: _t("افتح أي طلب لترى فاتورته."),
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            trigger: ".o_form_view .o_field_widget[name=account_move]",
            content: markup(_t("هنا <b>الفاتورة</b>. افتحها لترى حالتها في الربط الإلكتروني: مقبولة أو تحتاج إعادة إرسال.")),
            tooltipPosition: "bottom",
            run: "click",
        },
    ],
});

tours.add("resta_growth_l1_holiday", {
    url: "/odoo/growth",
    steps: () => [
        {
            trigger: ".o_growth_tabs .o_growth_tab:last-child",
            content: _t("إعلان الإجازة من «ملف مطعمك»."),
            tooltipPosition: "bottom",
            run: "click",
        },
        {
            trigger: ".o_growth_holiday_form input:not([type=date])",
            content: _t("اكتب السبب: عيد، صيانة، سفر…"),
            tooltipPosition: "bottom",
            run: "edit إجازة العيد",
        },
        {
            trigger: ".o_growth_holiday_form .btn-primary",
            content: _t("حدد التاريخين ثم اضغط «أعلن الإجازة». هذه الأيام لن تكسر سلسلة تسجيلك."),
            tooltipPosition: "top",
            run: "click",
        },
    ],
});
