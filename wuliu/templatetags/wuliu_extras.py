from django import template
from django.urls import reverse
from django.forms.fields import ChoiceField
from django.contrib.messages import constants

from ..common import get_global_settings, is_logged_user_has_perm, get_logged_user, PERMISSION_TREE_LIST
from ..models import WaybillRouting

register = template.Library()

_message_icons = {
    constants.INFO: "fas fa-info",
    constants.SUCCESS: "fas fa-check",
    constants.WARNING: "fas fa-exclamation-triangle",
    constants.ERROR: "fas fa-ban",
}

class _MenuItem:

    __slots__ = ["name", "url", "icon", "opened", "children", "need_perm", "admin_only"]

    def __init__(self, name="未命名项", url="", *,
                 icon="far fa-circle", opened=False, children=tuple(), need_perm="", admin_only=False):
        """
        :param name: 页面中显示的名称
        :param url: url
        :param icon: 指定文字前图标的class样式
        :param opened: 是否为已打开状态
        :param children: 子项, 注意: 目前只能嵌套一次
        :param need_perm: 需要的权限
        :param admin_only: 为True时需要管理员权限
        """
        self.name = name
        self.url = url
        self.icon = icon
        self.opened = opened
        self.children = children
        self.need_perm = need_perm
        self.admin_only = admin_only

def get_sidebar_menu_items():
    return [
        _MenuItem(
            name="运单管理",
            icon="fas fa-notes-medical",
            children=(
                _MenuItem(
                    "运单录入", reverse("wuliu:add_waybill"), icon="fas fa-plus-circle", need_perm="add_waybill",
                ),
                _MenuItem(
                    "运单管理", reverse("wuliu:manage_waybill"), icon="fas fa-tasks", need_perm="manage_waybill__search",
                ),
            ),
        ),
        _MenuItem(
            name="发车管理",
            icon="fas fa-truck-moving",
            children=(
                _MenuItem(
                    "发车出库", reverse("wuliu:manage_transport_out"),
                    icon="fas fa-truck-pickup", need_perm="manage_transport_out__search",
                ),
            ),
        ),
        _MenuItem(
            name="到达管理",
            icon="fas fa-truck-loading",
            children=(
                _MenuItem(
                    "到货管理", reverse("wuliu:manage_arrival"), icon="fas fa-plane-arrival", need_perm="manage_arrival",
                ),
                _MenuItem(
                    "客户签收", reverse("wuliu:manage_sign_for"), icon="fas fa-box-open", need_perm="manage_sign_for",
                ),
            ),
        ),
        _MenuItem(
            name="财务管理",
            icon="fas fa-yen-sign",
            children=(
                _MenuItem(
                    "部门回款单", reverse("wuliu:manage_department_payment"),
                    icon="fas fa-money-check-alt", need_perm="manage_department_payment__search",
                ),
                _MenuItem(
                    "代收款转账单", reverse("wuliu:manage_cargo_price_payment"),
                    icon="fas fa-money-check", need_perm="manage_cargo_price_payment__search",
                ),
                _MenuItem(
                    "客户积分记录", reverse("wuliu:manage_customer_score"),
                    icon="fas fa-donate", need_perm="customer_score_log__search",
                ),
            ),
        ),
        _MenuItem(
            name="业务报表",
            icon="fas fa-table",
            children=(
                _MenuItem(
                    "收货报表", reverse("wuliu:report_table_src_waybill"), need_perm="report_table_src_waybill"
                ),
                _MenuItem(
                    "发货库存", reverse("wuliu:report_table_stock_waybill"), need_perm="report_table_stock_waybill"
                ),
                _MenuItem(
                    "到货报表", reverse("wuliu:report_table_dst_waybill"), need_perm="report_table_dst_waybill"
                ),
                _MenuItem(
                    "到货库存", reverse("wuliu:report_table_dst_stock_waybill"),
                    need_perm="report_table_dst_stock_waybill"
                ),
                _MenuItem(
                    "提货报表", reverse("wuliu:report_table_sign_for_waybill"), need_perm="report_table_sign_for_waybill"
                ),
            ),
        ),
        _MenuItem(
            name="系统设置",
            icon="fas fa-cog",
            children=(
                _MenuItem(
                    "用户管理", reverse("wuliu:manage_users"),
                    icon="fas fa-users", admin_only=True,
                ),
                _MenuItem(
                    "用户权限管理", reverse("wuliu:manage_user_permission"),
                    icon="fas fa-users-cog", admin_only=True,
                ),
                _MenuItem(),
            ),
        ),
    ]

@register.filter(name="is_logged_user_has_perm")
def _is_logged_user_has_perm(perm_name, request):
    """ 检查已登录用户是否具有perm_name权限
    示例:
    {% if "report_table_sign_for_waybill"|is_logged_user_has_perm:request %}
      true
    {% endif %}
    """
    return is_logged_user_has_perm(request, perm_name)

@register.filter()
def is_logged_user_is_admin(request):
    """ 检查已登录用户是否具是管理员
    示例:
    {% if request|is_logged_user_is_admin %}
      true
    {% endif %}
    """
    return get_logged_user(request).administrator

@register.simple_tag()
def get_company_name():
    """ 获取公司名称 """
    return get_global_settings().company_name

@register.inclusion_tag('wuliu/_inclusions/_message.html')
def show_message(message):
    """ 页面中的消息
    :param message: 消息对象
    """
    return {
        "message": message,
        "icon": _message_icons.get(message.level, _message_icons[constants.INFO]),
    }

@register.inclusion_tag('wuliu/_inclusions/_sidebar_menu_items.html', takes_context=True)
def show_sidebar_menu_items(context):
    """ 侧边栏树状菜单 """
    current_url = context.request.path
    items = get_sidebar_menu_items()
    for item in items:
        for child in item.children:
            if child is None:
                continue
            # 根据url展开列表
            if child.url == current_url:
                item.opened = True
                child.opened = True
                break
    return {"items": items, "request": context.request}

@register.inclusion_tag('wuliu/_inclusions/_form_input_field.html')
def show_form_input_field(field, label="", div_class="col-md"):
    """ 表单字段输入框
    :param field: 表单字段对象
    :param label: 自定义label, 默认为该表单字段的label属性
    :param div_class: 自定义class
    """
    if not label:
        label = field.label
    return {"field": field, "label": label, "div_class": div_class}

@register.inclusion_tag('wuliu/_inclusions/_form_input_field_with_append_select.html')
def show_form_input_field_with_append_select(field, field_append, label="", div_class="col-md"):
    """ 右边带有Dropdown按钮组的表单字段输入框
    :param field: 表单字段对象
    :param field_append: Dropdown按钮组的表单字段, 必须为ChoiceField
    :param label: 自定义label, 默认为该表单字段的label属性
    :param div_class: 自定义class
    """
    assert isinstance(field_append.field, ChoiceField)
    field_append_choices = [(str(k), v) for k, v in field_append.field.choices]
    field_append_initial_value = str(field_append.value() or field_append.initial)
    field_append_initial_string = dict(field_append_choices).get(field_append_initial_value, "")
    return {
        "field": field,
        "field_append": field_append,
        "field_append_choices": field_append_choices,
        "field_append_initial_value": field_append_initial_value,
        "field_append_initial_string": field_append_initial_string,
        "label": label,
        "div_class": div_class
    }

@register.inclusion_tag('wuliu/_inclusions/_waybill_routing_operation_info.html')
def show_waybill_routing_operation_info(wr: WaybillRouting):
    """ 生成运单路由的详细文本内容
    :param wr: WaybillRouting对象
    """
    return wr._template_context()

@register.inclusion_tag('wuliu/_inclusions/_tables/_waybill_table.html')
def show_waybill_table(waybills_info_list, table_id, have_check_box=True, high_light_fee=False, high_light_dept_id=-1):
    """ 运单表格
    :param waybills_info_list: 包含所有运单信息字典的表格
    :param table_id: DataTables对象id
    :param have_check_box: 为False时不显示复选框
    :param high_light_fee: 高亮应回款费用单元格(仅在部门回款单中使用)
    :param high_light_dept_id: 高亮部门单元格id(仅在部门回款单中使用)
    """
    return {
        "waybills_info_list": waybills_info_list,
        "table_id": table_id,
        "have_check_box": have_check_box,
        "high_light_fee": high_light_fee,
        "high_light_dept_id": high_light_dept_id,
    }

@register.inclusion_tag('wuliu/_inclusions/_tables/_waybill_table_row.html')
def show_waybill_table_row(waybill_dic, table_id, have_check_box=True):
    return {
        "waybill": waybill_dic,
        "table_id": table_id,
        "have_check_box": have_check_box,
    }

@register.inclusion_tag('wuliu/_inclusions/_tables/_stock_waybill_table.html')
def show_stock_waybill_table(waybills_info_list, table_id):
    return {
        "waybills_info_list": waybills_info_list,
        "table_id": table_id,
    }

@register.inclusion_tag('wuliu/_inclusions/_tables/_dst_stock_waybill_table.html')
def show_dst_stock_waybill_table(waybills_info_list, table_id):
    return {
        "waybills_info_list": waybills_info_list,
        "table_id": table_id,
    }

@register.inclusion_tag('wuliu/_inclusions/_tables/_transport_out_table.html')
def show_transport_out_table(transport_out_list, table_id):
    return {
        "transport_out_list": transport_out_list,
        "table_id": table_id,
    }

@register.inclusion_tag('wuliu/_inclusions/_tables/_department_payment_table.html')
def show_department_payment_table(department_payment_list, table_id):
    return {
        "department_payment_list": department_payment_list,
        "table_id": table_id,
    }

@register.inclusion_tag('wuliu/_inclusions/_tables/_cargo_price_payment_table.html')
def show_cargo_price_payment_table(cargo_price_payment_list, table_id):
    return {
        "cargo_price_payment_list": cargo_price_payment_list,
        "table_id": table_id,
    }

@register.inclusion_tag('wuliu/_inclusions/_tables/_customer_score_log_table.html')
def show_customer_score_log_table(customer_score_logs, table_id):
    return {
        "customer_score_logs": customer_score_logs,
        "table_id": table_id,
    }

@register.inclusion_tag('wuliu/_inclusions/_sign_for_waybill_info.html')
def show_sign_for_waybill_info(waybill_info_dic):
    return {"waybill": waybill_info_dic}

@register.inclusion_tag('wuliu/_inclusions/_permission_tree.html')
def _show_permission_tree(list_):
    """ 权限树图(递归使用) """
    return {"list": list_}

@register.inclusion_tag('wuliu/_inclusions/_full_permission_tree.html')
def show_full_permission_tree(div_id):
    """ 完整的权限树图(附js) """
    return {"div_id": div_id, "list": PERMISSION_TREE_LIST}

@register.inclusion_tag('wuliu/_inclusions/_js/_export_table_to_excel.js.html')
def js_export_table_to_excel(table_id, button_css_selector, skip_td_num=1,
                             table_title="", table_title_is_js=False, min_time_interval=60):
    """ 导出(excel表格)功能的js实现代码, 外层已被<script>标签包裹, 不要重复添加
    :param table_id: DataTables对象id
    :param button_css_selector: 导出按钮的css选择器(有单引号时必须转义)
    :param skip_td_num: 导出时跳过前skip_td_num列, 默认是跳过首列(序号), 注: 表格有复选框时应该跳过两列
    :param table_title: 自定义导出表格的标题和文件名, 默认为页面内容中的h1标签文本,
                        table_title_is_js为False时最好不要有特殊符号(单双引号会自动去除)
    :param table_title_is_js: 定义table_title参数是否为js表达式, 默认为False
    :param min_time_interval: 最小导出时间间隔, 默认60秒
    """
    if table_title and not table_title_is_js:
        table_title = '"%s"' % table_title.replace('\"', "").replace("\'", "")
    return {
        "table_id": table_id,
        "button_css_selector": button_css_selector,
        "skip_td_num": skip_td_num,
        "table_title": table_title,
        "min_time_interval": min_time_interval,
    }

@register.inclusion_tag('wuliu/_inclusions/_js/_init_datatable.js.html')
def js_init_datatable(table_id, have_check_box=True, custom_fixed_columns_left=None):
    """ 初始化DataTable的js实现代码(初始化, 全选, 添加序号), 外层已被<script>标签包裹, 不要重复添加
    :param table_id: DataTables对象的id属性
    :param have_check_box: 表格第二列是否有复选框, 默认为True
    :param custom_fixed_columns_left: 自定义表格左侧固定显示的列数,
                                      为None时则冻结两列(如果have_check_box为True则同时固定第二列的复选框)
    """
    return {
        "table_id": table_id,
        "have_check_box": have_check_box,
        "custom_fixed_columns_left": custom_fixed_columns_left
    }
