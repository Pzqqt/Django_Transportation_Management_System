from django.urls import path, include

from . import views, apis

# Unused
def easy_path(view_func):
    """ 只需给定视图函数, route和name都设置为视图函数的名字 """
    return path(view_func.__name__, view_func, name=view_func.__name__)

app_name = "wuliu"
urlpatterns = [
    # 登录
    path("login", views.login, name="login"),
    path("logout", views.logout, name="logout"),
    path("change_password", views.change_password, name="change_password"),
    path("", views.welcome, name="welcome"),
    path("welcome_action.js", views.welcome_js, name="welcome_js"),
    # 系统设置
    path("settings/", include([
        path("manage_users", views.manage_users, name="manage_users"),
        path("add_user", views.add_user, name="add_user"),
        path("manage_user_permission", views.manage_user_permission, name="manage_user_permission"),
        path("batch_edit_user_permission", views.batch_edit_user_permission, name="batch_edit_user_permission"),
    ])),
    # 运单管理
    path("waybill/", include([
        path("add", views.add_waybill, name="add_waybill"),
        path("edit", views.edit_waybill, name="edit_waybill"),
        path("edit.js", views.edit_waybill_js, name="edit_waybill_js"),
        path("manage", views.ManageWaybill.as_view(), name="manage_waybill"),
        path("manage.js", views.manage_waybill_js, name="manage_waybill_js"),
        path("detail/<int:waybill_id>", views.detail_waybill, name="detail_waybill"),
        path("quick_search", views.quick_search_waybill, name="quick_search_waybill"),
        path("confirm_return", views.confirm_return_waybill, name="confirm_return_waybill"),
    ])),
    # 发车管理
    path("transport_out/", include([
        path("add", views.add_transport_out, name="add_transport_out"),
        path("edit", views.edit_transport_out, name="edit_transport_out"),
        path("edit.js", views.edit_transport_out_js, name="edit_transport_out_js"),
        path("manage", views.manage_transport_out, name="manage_transport_out"),
        path("manage.js", views.manage_transport_out_js, name="manage_transport_out_js"),
        path("detail", views.detail_transport_out, name="detail_transport_out"),
        path("search_waybills", views.SearchWaybillsToTransportOut.as_view(), name="search_waybills_to_transport_out"),
        path("add_waybills", views.add_waybills_to_transport_out, name="add_waybills_to_transport_out"),
    ])),
    # 到货管理
    path("arrival/", include([
        path("manage", views.manage_arrival, name="manage_arrival"),
        path("confirm", views.confirm_arrival, name="confirm_arrival"),
    ])),
    # 客户签收
    path("sign_for/", include([
        path("manage", views.ManageSignFor.as_view(), name="manage_sign_for"),
        path("confirm", views.confirm_sign_for, name="confirm_sign_for"),
        path("confirm.js", views.confirm_sign_for_js, name="confirm_sign_for_js"),
    ])),
    # 财务管理
    path("finance/", include([
        path("department_payment/", include([
            path("add", views.add_department_payment, name="add_department_payment"),
            path("manage", views.manage_department_payment, name="manage_department_payment"),
            path("manage.js", views.manage_department_payment_js, name="manage_department_payment_js"),
            path("detail/<int:dp_id>", views.detail_department_payment, name="detail_department_payment"),
        ])),
        path("cargo_price_payment/", include([
            path("add", views.add_cargo_price_payment, name="add_cargo_price_payment"),
            path("edit", views.edit_cargo_price_payment, name="edit_cargo_price_payment"),
            path("edit.js", views.edit_cargo_price_payment_js, name="edit_cargo_price_payment_js"),
            path("detail/<int:cpp_id>", views.detail_cargo_price_payment, name="detail_cargo_price_payment"),
            path("manage", views.manage_cargo_price_payment, name="manage_cargo_price_payment"),
            path("manage.js", views.manage_cargo_price_payment_js, name="manage_cargo_price_payment_js"),
        ])),
        path("customer_score_log/", include([
            path("manage", views.manage_customer_score, name="manage_customer_score"),
            path("add", views.add_customer_score_log, name="add_customer_score_log"),
        ])),
    ])),
    # 业务报表
    path("report_table/", include([
        path("src_waybill", views.ReportTableSrcWaybill.as_view(), name="report_table_src_waybill"),
        path("stock_waybill", views.ReportTableStockWaybill.as_view(), name="report_table_stock_waybill"),
        path("dst_waybill", views.ReportTableDstWaybill.as_view(), name="report_table_dst_waybill"),
        path("dst_stock_waybill", views.ReportTableDstStockWaybill.as_view(), name="report_table_dst_stock_waybill"),
        path("sign_for_waybill", views.ReportTableSignForWaybill.as_view(), name="report_table_sign_for_waybill"),
    ])),
    # apis
    path("api/", include([
        path("gen_standard_fee", apis.gen_standard_fee, name="api_gen_standard_fee"),
        path("check_old_password", apis.check_old_password, name="api_check_old_password"),
        # 用以获取模型对象属性的api
        path("get/", include([
            path("customer_info", apis.get_customer_info, name="api_get_customer_info"),
            path("department_info", apis.get_department_info, name="api_get_department_info"),
            path("waybills_info", apis.get_waybills_info, name="api_get_waybills_info"),
            path("truck_info", apis.get_truck_info, name="api_get_truck_info"),
            path("user_info", apis.get_user_info, name="api_get_user_info"),
            path("user_permission", apis.get_user_permission, name="api_get_user_permission"),
        ])),
        # 用以操作模型和会话的api
        path("remove_waybill_when_add_transport_out", apis.remove_waybill_when_add_transport_out,
             name="api_remove_waybill_when_add_transport_out"),
        path("remove_waybill_when_edit_transport_out", apis.remove_waybill_when_edit_transport_out,
             name="api_remove_waybill_when_edit_transport_out"),
        path("add_waybill_when_confirm_sign_for", apis.add_waybill_when_confirm_sign_for,
             name="api_add_waybill_when_confirm_sign_for"),
        path("add_waybill_when_edit_cargo_price_payment", apis.add_waybill_when_edit_cargo_price_payment,
             name="api_add_waybill_when_edit_cargo_price_payment"),
        path("drop_waybill", apis.DropWaybill.as_view(), name="api_drop_waybill"),
        path("drop_transport_out", apis.DropTransportOut.as_view(), name="api_drop_transport_out"),
        path("start_transport_out", apis.StartTransportOut.as_view(), name="api_start_transport_out"),
        path("confirm_arrival", apis.ConfirmArrival.as_view(), name="api_confirm_arrival"),
        path("confirm_sign_for", apis.ConfirmSignFor.as_view(), name="api_confirm_sign_for"),
        path("department_payment/", include([
            path("modify_remark", apis.ModifyRemarkDepartmentPayment.as_view(),
                 name="api_modify_remark_department_payment"),
            path("drop", apis.DropDepartmentPayment.as_view(),
                 name="api_drop_department_payment"),
            path("review", apis.ConfirmReviewDepartmentPayment.as_view(),
                 name="api_review_department_payment"),
            path("pay", apis.ConfirmPayDepartmentPayment.as_view(),
                 name="api_pay_department_payment"),
            path("settle_accounts", apis.ConfirmSettleAccountsDepartmentPayment.as_view(),
                 name="api_settle_accounts_department_payment"),
        ])),
        path("cargo_price_payment/", include([
            path("drop", apis.DropCargoPricePayment.as_view(),
                 name="api_drop_cargo_price_payment"),
            path("submit", apis.ConfirmSubmitCargoPricePayment.as_view(),
                 name="api_submit_cargo_price_payment"),
            path("review", apis.ConfirmReviewCargoPricePayment.as_view(),
                 name="api_review_cargo_price_payment"),
            path("reject", apis.ConfirmRejectCargoPricePayment.as_view(),
                 name="api_reject_cargo_price_payment"),
            path("pay", apis.ConfirmPayCargoPricePayment.as_view(),
                 name="api_pay_cargo_price_payment"),
        ])),
    ])),
]
