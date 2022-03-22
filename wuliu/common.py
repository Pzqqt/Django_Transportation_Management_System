from functools import wraps

from django.shortcuts import redirect
from django.http import Http404, HttpResponseForbidden
from django.utils import timezone

from .models import (
    User, Waybill, TransportOut, DepartmentPayment, CargoPricePayment, Permission, PermissionGroup,
    _get_global_settings,
)
from utils.common import ExpireLruCache, model_to_dict_


expire_lru_cache_three_hours = ExpireLruCache(expire_time=timezone.timedelta(hours=3))
expire_lru_cache_one_minute = ExpireLruCache(expire_time=timezone.timedelta(minutes=1))

get_global_settings = expire_lru_cache_three_hours(_get_global_settings)

@expire_lru_cache_one_minute
def _get_logged_user_by_id(user_id: int) -> User:
    """ 根据用户名返回用户模型对象 """
    return User.objects.get(id=user_id)

def get_logged_user(request) -> User:
    """ 获取已登录的用户对象 """
    return _get_logged_user_by_id(request.session["user"]["id"])

def get_logged_user_type(request) -> User.Types:
    """ 获取已登录的用户的用户类型 """
    return get_logged_user(request).get_type

@expire_lru_cache_one_minute
def _get_user_permissions(user: User) -> set:
    """ 获取用户拥有的权限, 注意该方法返回的是集合而不是QuerySet """
    return set(user.permission.all().values_list("name", flat=True))

def is_logged_user_has_perm(request, perm_name: str) -> bool:
    """ 检查已登录用户是否具有perm_name权限
    :return: True或False
    """
    if not perm_name:
        return True
    return perm_name in _get_user_permissions(get_logged_user(request))

def is_logged_user_is_goods_yard(request) -> bool:
    """ 判断已登录的用户是否属于货场 """
    return get_logged_user_type(request) == User.Types.GoodsYard

def _gen_permission_tree_list(root_pg_=PermissionGroup.objects.get(father__isnull=True)) -> list:
    """ 根据所有的权限组和权限的层级结构生成列表, 用于前端渲染 """
    tree_list = []
    for pg in PermissionGroup.objects.filter(father=root_pg_):
        tree_list.append({
            "id": pg.id, "name": pg.name, "print_name": pg.print_name, "children": _gen_permission_tree_list(pg)
        })
    for p in Permission.objects.filter(father=root_pg_):
        tree_list.append({
            "id": p.id, "name": p.name, "print_name": p.print_name,
        })
    return tree_list

PERMISSION_TREE_LIST = _gen_permission_tree_list()

def login_required(raise_404=False):
    """ 自定义装饰器, 用于装饰路由方法
    若用户未登录, 则跳转到登录页面
    raise_404为True时, 则跳转到404页面
    """
    def _login_required(func):
        @wraps(func)
        def login_check(request, *args, **kwargs):
            if not request.session.get("user"):
                if raise_404:
                    raise Http404
                return redirect("wuliu:login")
            return func(request, *args, **kwargs)
        return login_check
    return _login_required

def check_permission(perm_name: str):
    """ 自定义装饰器, 用于在请求前检查用户是否具有perm_name权限
    若无perm_name权限则跳转至403页面
    """
    def _check_permission(func):
        @wraps(func)
        def perm_check(request, *args, **kwargs):
            if perm_name and not is_logged_user_has_perm(request, perm_name):
                return HttpResponseForbidden()
            return func(request, *args, **kwargs)
        return perm_check
    return _check_permission

def check_administrator(func):
    """ 自定义装饰器, 用于在请求前检查用户是否为管理员
    若不是管理员则跳转至403页面
    """
    @wraps(func)
    def admin_check(request, *args, **kwargs):
        if not get_logged_user(request).administrator:
            return HttpResponseForbidden()
        return func(request, *args, **kwargs)
    return admin_check

def waybill_to_dict(waybill_obj: Waybill) -> dict:
    """ 将Waybill对象转为字典 """
    waybill_dic = model_to_dict_(waybill_obj)
    waybill_dic["id_"] = waybill_obj.get_full_id
    waybill_dic["fee_type_id"] = waybill_dic["fee_type"]
    waybill_dic["fee_type"] = waybill_obj.get_fee_type_display()
    waybill_dic["status_id"] = waybill_dic["status"]
    waybill_dic["status"] = waybill_obj.get_status_display()
    if waybill_obj.return_waybill is not None:
        waybill_dic["return_waybill"] = waybill_to_dict(waybill_obj.return_waybill)
    else:
        waybill_dic["return_waybill"] = None
    return waybill_dic

def transport_out_to_dict(transport_out_obj: TransportOut) -> dict:
    """ 将TransportOut对象转为字典 """
    to_dic = model_to_dict_(transport_out_obj)
    to_dic["id_"] = transport_out_obj.get_full_id
    to_dic["status_id"] = to_dic["status"]
    to_dic["status"] = transport_out_obj.get_status_display()
    to_dic.update(transport_out_obj.gen_waybills_info())
    return to_dic

def department_payment_to_dict(department_payment_obj: DepartmentPayment) -> dict:
    """ 将DepartmentPayment对象转为字典 """
    dp_dic = model_to_dict_(department_payment_obj)
    dp_dic["id_"] = department_payment_obj.get_full_id
    dp_dic["status_id"] = dp_dic["status"]
    dp_dic["status"] = department_payment_obj.get_status_display()
    total_fee_dic = department_payment_obj.gen_total_fee()
    dp_dic["total_fee_now"] = total_fee_dic["fee_now"]
    dp_dic["total_fee_sign_for"] = total_fee_dic["fee_sign_for"]
    dp_dic["total_cargo_price"] = total_fee_dic["cargo_price"]
    dp_dic["final_total_fee"] = sum(total_fee_dic.values())
    return dp_dic

def cargo_price_payment_to_dict(cargo_price_payment_obj: CargoPricePayment) -> dict:
    """ 将CargoPricePayment对象转为字典 """
    cpp_dic = model_to_dict_(cargo_price_payment_obj)
    cpp_dic["id_"] = cargo_price_payment_obj.get_full_id
    cpp_dic["status_id"] = cpp_dic["status"]
    cpp_dic["status"] = cargo_price_payment_obj.get_status_display()
    total_fee_dic = cargo_price_payment_obj.gen_total_fee()
    cpp_dic["total_cargo_price"] = total_fee_dic["cargo_price"]
    cpp_dic["total_deduction_fee"] = total_fee_dic["deduction_fee"]
    cpp_dic["total_cargo_handling_fee"] = total_fee_dic["cargo_handling_fee"]
    cpp_dic["final_fee"] = (
        total_fee_dic["cargo_price"] - total_fee_dic["deduction_fee"] - total_fee_dic["cargo_handling_fee"]
    )
    return cpp_dic
