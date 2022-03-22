from functools import wraps

from django.contrib import messages
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.template.loader import render_to_string
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.core.signals import got_request_exception

from .common import (
    is_logged_user_has_perm, get_logged_user, is_logged_user_is_goods_yard, waybill_to_dict,
)
from .models import (
    User, Customer, Department, Waybill, WaybillRouting, Truck, TransportOut, DepartmentPayment, CargoPricePayment
)
from utils.common import (
    UnescapedJsonResponse, model_to_dict_, validate_comma_separated_integer_list_and_split
)


def api_json_response(message_text="unknown", code=400, **kwargs):
    """ 返回一个标准的ajax json响应格式 """
    return UnescapedJsonResponse({
        "code": code,
        "data": {"message": message_text} | kwargs,
    })

def _api_login_required(func):
    @wraps(func)
    def login_check(request, *args, **kwargs):
        if not request.session.get("user"):
            return api_json_response("unauthorized", 401)
        return func(request, *args, **kwargs)
    return login_check

def _api_check_permission(perm_name):
    def _check_permission(func):
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            if not is_logged_user_has_perm(request, perm_name):
                return api_json_response("access denied", 403)
            return func(request, *args, **kwargs)
        return wrapper
    return _check_permission

class ActionApi(View):

    """ 一个通用的类视图, 用以简化以下步骤:
    从request.POST读取参数 -> 鉴权&数据清洗 -> 从数据库中查询 -> 更新数据库
    如果需要在不同的方法间传递变量, 可以通过self._private_dic字典
    注: 仅支持POST请求
    """

    http_method_names = ("post", )
    need_permissions = ()

    class AbortException(Exception):
        pass

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.response_dic = {
            "code": 400,
            "data": {
                "message": "unknown",
            },
        }
        self._private_dic = {}

    def actions(self):
        """ 在这里完成鉴权, 数据清洗, 查询等操作
        若要中断, 应该抛出ActionApi.AbortException异常, 并附带提示文本
        """
        raise NotImplementedError

    def write_database(self):
        """ 在这里完成数据库更新操作 """
        pass

    def actions_after_success(self):
        """ 在这里进行数据库更新成功后的后续操作 """
        pass

    def post(self, request):
        # 未登录或没有权限时
        if not request.session.get("user"):
            return api_json_response("unauthorized", 401)
        for perm in self.need_permissions:
            if not is_logged_user_has_perm(request, perm):
                return api_json_response("access denied", 403)
        try:
            self.actions()
        except ActionApi.AbortException as e:
            return api_json_response(str(e))
        try:
            with transaction.atomic():
                self.write_database()
        except Exception as e:
            got_request_exception.send(None, request=request)
            return api_json_response(str(e), 500)
        self.response_dic["code"] = 200
        self.response_dic["data"]["message"] = "success"
        self.actions_after_success()
        return UnescapedJsonResponse(self.response_dic)

    # 使用csrf_exempt装饰器装饰之后, 在post时就不再需要携带csrfmiddlewaretoken了
    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

@csrf_exempt
@require_POST
def check_old_password(request):
    """ 检查已登录用户的当前密码(旧密码) """
    old_password = request.POST.get("old_password", "").strip()
    if not old_password:
        return api_json_response('Missing parameter "old_password".')
    try:
        user = get_logged_user(request)
    except KeyError:
        return api_json_response("unauthorized", 401)
    return api_json_response(
        "success" if check_password(old_password, user.password) else "failed",
        200,
    )

@_api_login_required
@require_GET
def get_customer_info(request):
    """ 获取客户详情 """
    customer_id = request.GET.get("customer_id")
    if not customer_id:
        return api_json_response('Missing parameter "customer_id".', customer_info={})
    try:
        customer = Customer.objects.get(id=customer_id)
    except Customer.DoesNotExist:
        return api_json_response("Customer does not exist!", 404, customer_info={})
    if not customer.enabled:
        return api_json_response("Customer not enabled!", 403, customer_info={})
    return api_json_response("success", 200, customer_info=model_to_dict_(customer))

@_api_login_required
@require_GET
def get_department_info(request):
    """ 获取部门详情 """
    department_id = request.GET.get("department_id")
    if not department_id:
        return api_json_response('Missing parameter "department_id".', department_info={})
    try:
        department = Department.objects.get(id=department_id)
    except Department.DoesNotExist:
        return api_json_response("Department does not exist!", 404, department_info={})
    return api_json_response("success", 200, department_info={
        "name": department.name,
        "father_department": department.father_department.name,
        "is_branch": department.is_branch(),
        "unit_price": department.unit_price,
        "enable_src": department.enable_src,
        "enable_dst": department.enable_dst,
        "enable_cargo_price": department.enable_cargo_price,
    })

# Unused
@_api_login_required
@require_GET
def get_waybills_info(request):
    """ 获取运单详情 """
    waybill_ids = request.GET.get("waybill_ids")
    if not waybill_ids:
        return api_json_response('Missing parameter "waybill_ids".', waybills=[])
    try:
        waybill_ids = validate_comma_separated_integer_list_and_split(waybill_ids)
    except ValidationError:
        return api_json_response("waybill_ids: Wrong parameter format!", waybills=[])
    waybills_info = [waybill_to_dict(wb) for wb in Waybill.objects.filter(id__in=waybill_ids)]
    if len(waybills_info) == 0:
        return api_json_response("No results.", 404, waybills=[])
    for waybill_dict in waybills_info:
        for field_name in "src_department dst_department src_customer dst_customer".split():
            try:
                waybill_dict[field_name + "_id"] = waybill_dict[field_name].id
                waybill_dict[field_name] = waybill_dict[field_name].name
            except AttributeError:
                waybill_dict[field_name + "_id"] = None
                waybill_dict[field_name] = None
        del waybill_dict["cargo_price_payment"]
    return api_json_response("success", 200, waybills=waybills_info)

@_api_login_required
@require_GET
def get_truck_info(request):
    """ 获取车辆详情 """
    truck_id = request.GET.get("truck_id")
    if not truck_id:
        return api_json_response('Missing parameter "truck_id".', truck_info={})
    try:
        truck = Truck.objects.get(id=truck_id)
    except Department.DoesNotExist:
        return api_json_response("Truck does not exist!", 404, truck_info={})
    return api_json_response("success", 200, truck_info={
        "number_plate": truck.number_plate,
        "driver_name": truck.driver_name,
        "driver_phone": truck.driver_phone,
    })

@_api_login_required
@require_GET
def get_user_info(request):
    """ 获取用户详情 """
    if not get_logged_user(request).administrator:
        return api_json_response("Access denied.", 403, perms=[])
    user_id = request.GET.get("user_id")
    if not user_id:
        return api_json_response('Missing parameter "user_id".', perms=[])
    try:
        user = User.objects.get(id=user_id)
    except Department.DoesNotExist:
        return api_json_response("User does not exist!", 404, perms=[])
    user_info_dic = model_to_dict_(user)
    user_info_dic.pop("password")
    user_info_dic.pop("permission")
    user_info_dic.pop("department")
    user_info_dic["department_id"] = user.department_id
    return api_json_response("success", 200, user_info=user_info_dic)

@_api_login_required
@require_GET
def get_user_permission(request):
    """ 获取用户拥有的权限 """
    if not get_logged_user(request).administrator:
        return api_json_response("Access denied.", 403, perms=[])
    user_id = request.GET.get("user_id")
    if not user_id:
        return api_json_response('Missing parameter "user_id".', perms=[])
    try:
        user = User.objects.get(id=user_id)
    except Department.DoesNotExist:
        return api_json_response("User does not exist!", 404, perms=[])
    return api_json_response("success", 200, perms=list(user.permission.all().values_list("name", flat=True)))

@_api_login_required
@require_GET
def gen_standard_fee(request):
    """ 给定发货部门id, 到达部门id, 货物总体积和总重量, 计算标准运费 """
    src_dept_id = request.GET.get("src_dept_id")
    dst_dept_id = request.GET.get("dst_dept_id")
    cargo_volume = request.GET.get("cargo_volume")
    cargo_weight = request.GET.get("cargo_weight")
    if not all([src_dept_id, dst_dept_id, cargo_volume, cargo_weight]):
        return api_json_response("Missing parameters.", standard_fee=0)
    try:
        cargo_volume = float(cargo_volume)
        cargo_weight = float(cargo_weight)
    except (ValueError, TypeError):
        return api_json_response("Illegal parameter: 'cargo_volume' or 'cargo_weight'!", standard_fee=0)
    try:
        src_dept_uprice = Department.objects.get(id=int(src_dept_id)).unit_price
        dst_dept_uprice = Department.objects.get(id=int(dst_dept_id)).unit_price
    except (Department.DoesNotExist, ValueError):
        return api_json_response("Department dose not exist!", standard_fee=0)
    standard_fee = int((src_dept_uprice + dst_dept_uprice) * cargo_volume * cargo_weight)
    return api_json_response("success", 200, standard_fee=standard_fee)

@_api_login_required
@_api_check_permission("manage_transport_out__add_edit_delete_start")
@require_POST
@csrf_exempt
def remove_waybill_when_add_transport_out(request):
    """ 新增车次时移除运单 """
    try:
        wb_remove_list = set(validate_comma_separated_integer_list_and_split(request.POST.get("remove_list", "")))
    except ValidationError:
        return api_json_response("remove_list: Wrong parameter format!")
    waybills_added = {int(wb_id) for wb_id in request.session.get("add_transport_out__waybills_added", [])}
    wb_remove_list &= waybills_added
    # 新增车次时不允许添加已配载的运单, 所以无需过多处理
    for id_ in wb_remove_list:
        try:
            waybills_added.remove(id_)
        except KeyError:
            pass
    request.session["add_transport_out__waybills_added"] = sorted(waybills_added)
    return api_json_response("success", 200)

@_api_login_required
@_api_check_permission("manage_transport_out__add_edit_delete_start")
@require_POST
@csrf_exempt
def remove_waybill_when_edit_transport_out(request):
    """ 修改车次信息时移除运单 """
    try:
        wb_remove_list = set(validate_comma_separated_integer_list_and_split(request.POST.get("remove_list", "")))
    except ValidationError:
        return api_json_response("remove_list: Wrong parameter format!")
    waybills_added = {int(wb_id) for wb_id in request.session.get("edit_transport_out__waybills_added", [])}
    wb_remove_list &= waybills_added
    # 需要立即修改车次信息和运单状态
    if is_logged_user_is_goods_yard(request):
        set_wb_status = Waybill.Statuses.GoodsYardArrived
    else:
        set_wb_status = Waybill.Statuses.Created
    try:
        with transaction.atomic():
            TransportOut.objects.get(
                    id=request.session["edit_transport_out__transport_out_id"]
                ).waybills.remove(*wb_remove_list)
            Waybill.objects.filter(id__in=wb_remove_list).update(status=set_wb_status)
    except Exception as e:
        got_request_exception.send(None, request=request)
        return api_json_response(str(e), 500)
    for id_ in wb_remove_list:
        try:
            waybills_added.remove(id_)
        except KeyError:
            pass
    request.session["edit_transport_out__waybills_added"] = sorted(waybills_added)
    return api_json_response("success", 200)

@_api_login_required
@_api_check_permission("manage_sign_for")
@require_POST
@csrf_exempt
def add_waybill_when_confirm_sign_for(request):
    """ 确认签收时添加运单 """
    add_waybill_id = str(request.POST.get("add_waybill_id"))
    try:
        if add_waybill_id.upper().startswith("YF"):
            add_waybill = Waybill.objects.get(return_waybill_id=int(add_waybill_id[2:]))
        else:
            add_waybill = Waybill.objects.get(id=int(add_waybill_id))
    except (ValueError, Waybill.DoesNotExist):
        return api_json_response("该运单不存在！", 404, html="", waybill_id=-1)
    if add_waybill.status != Waybill.Statuses.Arrived:
        return api_json_response('只能添加"到站待提"状态的运单！', 403, html="", waybill_id=-1)
    if add_waybill.dst_department_id != request.session["user"]["department_id"]:
        return api_json_response("运单的到达部门与当前部门不一致！", 403, html="", waybill_id=-1)
    return api_json_response(
        "success", 200,
        html=render_to_string(
            "wuliu/_inclusions/_sign_for_waybill_info.html",
            {"waybill": add_waybill}
        ),
        waybill_id=add_waybill.id,
    )

@_api_login_required
@_api_check_permission("manage_cargo_price_payment__add_edit_delete_submit")
@require_POST
@csrf_exempt
def add_waybill_when_edit_cargo_price_payment(request):
    """ 编辑代收款转账单时添加运单 """
    add_waybill_id = str(request.POST.get("add_waybill_id"))
    current_cpp_id = request.POST.get("current_cpp_id")
    table_id = request.POST.get("table_id")
    if not (add_waybill_id and table_id):
        return HttpResponseBadRequest()
    try:
        if add_waybill_id.upper().startswith("YF"):
            add_waybill = Waybill.objects.get(return_waybill_id=int(add_waybill_id[2:]))
        else:
            add_waybill = Waybill.objects.get(id=int(add_waybill_id))
    except (ValueError, Waybill.DoesNotExist):
        return api_json_response("该运单不存在！", 403, html="", waybill_id=-1)
    if current_cpp_id:
        try:
            current_cpp_id = int(current_cpp_id)
            CargoPricePayment.objects.get(id=current_cpp_id)
        except (ValueError, CargoPricePayment.DoesNotExist):
            return api_json_response("该转账单不存在！", 404, html="", waybill_id=-1)
    if add_waybill.status != Waybill.Statuses.SignedFor:
        return api_json_response("只能添加已签收的运单！", 403, html="", waybill_id=-1)
    if add_waybill.cargo_price == 0:
        return api_json_response("该运单无代收货款！", 403, html="", waybill_id=-1)
    if add_waybill.cargo_price_payment:
        if not (current_cpp_id and add_waybill.cargo_price_payment_id == current_cpp_id):
            return api_json_response("该运单已存在于其他转账单中！", 403)
    return api_json_response(
        "success", 200,
        html=render_to_string(
            "wuliu/_inclusions/_tables/_waybill_table_row.html",
            {"waybill": add_waybill, "table_id": table_id, "have_check_box": True}
        ),
        waybill_id=add_waybill.id
    )

class DropWaybill(ActionApi):
    """ 作废运单 """

    need_permissions = ("manage_waybill__edit_delete_print", )

    def actions(self):
        drop_id = self.request.POST.get("drop_waybill_id")
        drop_reason = self.request.POST.get("drop_waybill_reason")
        if not (drop_id and drop_reason):
            raise ActionApi.AbortException("请求格式无效！")
        try:
            waybill = Waybill.objects.get(id=drop_id)
        except Waybill.DoesNotExist as exc:
            raise ActionApi.AbortException("该运单不存在！") from exc
        # 禁止跨部门作废运单
        if waybill.src_department_id != self.request.session["user"]["department_id"]:
            raise ActionApi.AbortException("禁止跨部门作废运单！")
        # 只能作废未配载/发车的运单
        if waybill.status != Waybill.Statuses.Created:
            raise ActionApi.AbortException('只允许作废"已开票"状态的运单！')
        if waybill.return_waybill:
            raise ActionApi.AbortException("退货运单禁止作废")
        self._private_dic = {
            "waybill": waybill, "drop_reason": drop_reason,
        }

    def write_database(self):
        waybill = self._private_dic["waybill"]
        drop_reason = self._private_dic["drop_reason"]
        logged_user = get_logged_user(self.request)
        logged_user_department = logged_user.department
        waybill.status = Waybill.Statuses.Dropped
        waybill.drop_reason = drop_reason
        waybill.waybillrouting_set.create(
            # waybill=waybill,
            time=timezone.now(),
            operation_type=Waybill.Statuses.Dropped,
            operation_dept=logged_user_department,
            operation_user=logged_user,
        )
        waybill.save()

    def actions_after_success(self):
        self.response_dic["data"]["waybill_status_now"] = Waybill.Statuses.Dropped.value

class DropTransportOut(ActionApi):
    """ 删除车次 """

    need_permissions = ("manage_transport_out__add_edit_delete_start", )

    def actions(self):
        drop_id = self.request.POST.get("drop_transport_out_id")
        if not drop_id:
            raise ActionApi.AbortException("请求格式无效！")
        try:
            to_obj = TransportOut.objects.get(id=drop_id)
        except TransportOut.DoesNotExist as exc:
            raise ActionApi.AbortException("该车次不存在！") from exc
        # 禁止跨部门删除车次
        if to_obj.src_department_id != self.request.session["user"]["department_id"]:
            raise ActionApi.AbortException("禁止跨部门删除车次！")
        # 禁止删除已发车的车次
        if to_obj.status != TransportOut.Statuses.Ready:
            raise ActionApi.AbortException('只允许删除"货物配载"状态的车次！')
        # 确保车次中所有运单的状态一致, 并处于"已配载"或"货场配载"状态
        try:
            waybills_status = to_obj.waybills.order_by("status").values("status").distinct()
            assert len(waybills_status) == 1
            waybills_status = waybills_status[0]["status"]
            if is_logged_user_is_goods_yard(self.request):
                assert waybills_status == Waybill.Statuses.GoodsYardLoaded.value
            else:
                assert waybills_status == Waybill.Statuses.Loaded.value
        except AssertionError as exc:
            raise ActionApi.AbortException("该车次中存在状态异常的运单！") from exc
        self._private_dic = {
            "to_obj": to_obj, "waybills_status": waybills_status,
        }

    def write_database(self):
        to_obj = self._private_dic["to_obj"]
        waybills_status = self._private_dic["waybills_status"]
        to_obj.waybills.update(status=waybills_status-1)
        to_obj.delete()

class StartTransportOut(ActionApi):
    """ 车次发车 """

    need_permissions = ("manage_transport_out__add_edit_delete_start", )

    def actions(self):
        start_id = self.request.POST.get("start_transport_out_id")
        if not start_id:
            raise ActionApi.AbortException("请求格式无效！")
        try:
            to_obj = TransportOut.objects.get(id=start_id)
        except TransportOut.DoesNotExist as exc:
            raise ActionApi.AbortException("该车次不存在！") from exc
        # 禁止跨部门发车
        if to_obj.src_department_id != self.request.session["user"]["department_id"]:
            raise ActionApi.AbortException("禁止跨部门操作车次！")
        # 禁止发车已发车的车次
        if to_obj.status != TransportOut.Statuses.Ready:
            raise ActionApi.AbortException('只允许对"货物配载"状态的车次进行发车操作！')
        # 确保车次中所有运单的状态一致, 并处于"已配载"或"货场配载"状态
        try:
            waybills_status = to_obj.waybills.order_by("status").values("status").distinct()
            assert len(waybills_status) == 1
            waybills_status = waybills_status[0]["status"]
            if is_logged_user_is_goods_yard(self.request):
                assert waybills_status == Waybill.Statuses.GoodsYardLoaded.value
            else:
                assert waybills_status == Waybill.Statuses.Loaded.value
        except AssertionError as exc:
            raise ActionApi.AbortException("该车次中存在状态异常的运单！") from exc
        timezone_now = timezone.now()
        self._private_dic = {
            "to_obj": to_obj, "waybills_status": waybills_status, "timezone_now": timezone_now,
        }

    def write_database(self):
        to_obj = self._private_dic["to_obj"]
        waybills_status = self._private_dic["waybills_status"]
        timezone_now = self._private_dic["timezone_now"]
        logged_user = get_logged_user(self.request)
        logged_user_department = logged_user.department
        to_obj.status = TransportOut.Statuses.OnTheWay
        to_obj.start_time = timezone_now
        to_obj.waybills.update(status=waybills_status+1)
        WaybillRouting.objects.bulk_create([
            WaybillRouting(
                waybill=wb,
                time=timezone_now,
                operation_type=waybills_status+1,
                operation_dept=logged_user_department,
                operation_user=logged_user,
                operation_info={"transport_out_id": to_obj.id}
            )
            for wb in to_obj.waybills.all()
        ])
        to_obj.save()

    def actions_after_success(self):
        timezone_now = self._private_dic["timezone_now"]
        timezone_now_str = timezone.make_naive(timezone_now).strftime("%Y-%m-%d %H:%M:%S")
        self.response_dic["data"]["start_time"] = timezone_now_str
        self.response_dic["data"]["start_time_timestamp"] = timezone_now.timestamp()

class ConfirmArrival(ActionApi):
    """ 确认到货/车 """

    need_permissions = ("manage_arrival", )

    def actions(self):
        to_id = self.request.POST.get("transport_out_id")
        if not to_id:
            raise ActionApi.AbortException("请求格式无效！")
        try:
            to_obj = TransportOut.objects.get(id=to_id)
        except TransportOut.DoesNotExist as exc:
            raise ActionApi.AbortException("该车次不存在！") from exc
        if to_obj.dst_department_id != self.request.session["user"]["department_id"]:
            raise ActionApi.AbortException("禁止跨部门操作车次！")
        # 只有"车次在途"状态的车次才允许确认到货/车
        if to_obj.status != TransportOut.Statuses.OnTheWay:
            raise ActionApi.AbortException('只允许对"车次在途"状态的车次进行确认到车操作！')
        # 确保车次中所有运单的状态一致, 并处于"已发车"或"货场发车"状态
        try:
            waybills_status = to_obj.waybills.order_by("status").values("status").distinct()
            assert len(waybills_status) == 1
            waybills_status = waybills_status[0]["status"]
            if is_logged_user_is_goods_yard(self.request):
                assert waybills_status == Waybill.Statuses.Departed.value
            else:
                assert waybills_status == Waybill.Statuses.GoodsYardDeparted.value
        except AssertionError as exc:
            raise ActionApi.AbortException("该车次中存在状态异常的运单！") from exc
        self._private_dic = {
            "to_obj": to_obj, "waybills_status": waybills_status,
        }

    def write_database(self):
        to_obj = self._private_dic["to_obj"]
        waybills_status_now = self._private_dic["waybills_status"] + 1
        timezone_now = timezone.now()
        logged_user = get_logged_user(self.request)
        logged_user_department = logged_user.department
        to_obj.status = TransportOut.Statuses.Arrived
        to_obj.end_time = timezone_now
        to_obj.waybills.update(status=waybills_status_now)
        if waybills_status_now == Waybill.Statuses.Arrived:
            to_obj.waybills.all().update(arrival_time=timezone_now)
        WaybillRouting.objects.bulk_create([
            WaybillRouting(
                waybill=wb,
                time=timezone_now,
                operation_type=waybills_status_now,
                operation_dept=logged_user_department,
                operation_user=logged_user,
            )
            for wb in to_obj.waybills.all()
        ])
        to_obj.save()

    def actions_after_success(self):
        messages.success(self.request, "操作成功")

class ConfirmSignFor(ActionApi):
    """ 确认签收 """

    need_permissions = ("manage_sign_for", )

    def actions(self):
        sign_for_waybill_ids = self.request.POST.get("sign_for_waybill_ids", "").strip()
        sign_for_name = self.request.POST.get("sign_for_name", "").strip()
        sign_for_credential_num = self.request.POST.get("sign_for_credential_num", "").strip()
        if not (sign_for_waybill_ids and sign_for_name and sign_for_credential_num):
            raise ActionApi.AbortException("请求格式无效！")
        try:
            sign_for_waybill_ids = validate_comma_separated_integer_list_and_split(sign_for_waybill_ids)
        except ValidationError as exc:
            raise ActionApi.AbortException("请求格式无效！") from exc
        if Waybill.objects.filter(id__in=sign_for_waybill_ids).count() != len(sign_for_waybill_ids):
            raise ActionApi.AbortException("请求中存在不存在的运单！")
        # 禁止签收到达部门与当前部门不一致的运单, 以及非"到站待提"状态的运单
        if Waybill.objects.filter(id__in=sign_for_waybill_ids).filter(
                ~Q(dst_department__id=self.request.session["user"]["department_id"]) |
                ~Q(status=Waybill.Statuses.Arrived)).exists():
            raise ActionApi.AbortException("请求中存在状态异常的运单！")
        timezone_now = timezone.now()
        self._private_dic = {
            "sign_for_waybill_ids": sign_for_waybill_ids,
            "sign_for_name": sign_for_name,
            "sign_for_credential_num": sign_for_credential_num,
            "timezone_now": timezone_now,
        }

    def write_database(self):
        sign_for_waybill_ids = self._private_dic["sign_for_waybill_ids"]
        sign_for_name = self._private_dic["sign_for_name"]
        sign_for_credential_num = self._private_dic["sign_for_credential_num"]
        timezone_now = self._private_dic["timezone_now"]
        logged_user = get_logged_user(self.request)
        logged_user_department = logged_user.department
        Waybill.objects.filter(id__in=sign_for_waybill_ids).update(
            status=Waybill.Statuses.SignedFor,
            sign_for_time=timezone_now,
            sign_for_customer_name=sign_for_name,
            sign_for_customer_credential_num=sign_for_credential_num,
        )
        WaybillRouting.objects.bulk_create([
            WaybillRouting(
                waybill=wb,
                time=timezone_now,
                operation_type=Waybill.Statuses.SignedFor,
                operation_dept=logged_user_department,
                operation_user=logged_user,
            )
            for wb in Waybill.objects.filter(id__in=sign_for_waybill_ids)
        ])

    def actions_after_success(self):
        messages.success(self.request, "操作成功")

class ModifyRemarkDepartmentPayment(ActionApi):
    """ 修改部门回款单备注 """

    need_permissions = ("manage_department_payment__search", )

    def actions(self):
        dp_id = self.request.POST.get("dp_id")
        remark_dept_type = self.request.POST.get("remark_dept_type")
        remark_text = self.request.POST.get("remark_text").strip()
        if not (dp_id and remark_dept_type and remark_text):
            raise ActionApi.AbortException("请求格式无效！")
        try:
            dp_obj = DepartmentPayment.objects.get(id=dp_id)
        except (ValueError, DepartmentPayment.DoesNotExist) as exc:
            raise ActionApi.AbortException("该回款单不存在！") from exc
        if dp_obj.status == DepartmentPayment.Statuses.Settled:
            raise ActionApi.AbortException("已结算的回款单不允许修改备注。")
        if remark_dept_type == "src":
            if self.request.session["user"]["department_id"] != dp_obj.src_department_id:
                raise ActionApi.AbortException("你没有修改备注的权限。")
            dp_obj.src_remark = remark_text
        elif remark_dept_type == "dst":
            if self.request.session["user"]["department_id"] != dp_obj.dst_department_id:
                raise ActionApi.AbortException("你没有修改备注的权限。")
            dp_obj.dst_remark = remark_text
        else:
            raise ActionApi.AbortException("请求格式无效！")
        self._private_dic = {"dp_obj": dp_obj}

    def write_database(self):
        self._private_dic["dp_obj"].save(update_fields=["src_remark", "dst_remark"])

class DropDepartmentPayment(ActionApi):
    """ 删除回款单 """

    need_permissions = ("manage_department_payment__add_delete", )

    def actions(self):
        dp_ids = self.request.POST.get("dp_ids", "")
        try:
            dp_ids = validate_comma_separated_integer_list_and_split(dp_ids)
        except ValidationError as exc:
            raise ActionApi.AbortException("请求格式无效！") from exc
        dp_qs = DepartmentPayment.objects.filter(id__in=dp_ids)
        if dp_qs.exclude(status=DepartmentPayment.Statuses.Created).exists():
            raise ActionApi.AbortException("只能删除尚未审核的回款单！")
        self._private_dic = {"dp_queryset": dp_qs}

    def write_database(self):
        self._private_dic["dp_queryset"].delete()

class ConfirmReviewDepartmentPayment(ActionApi):
    """ 审核回款单 """

    need_permissions = ("manage_department_payment__review", )

    def actions(self):
        dp_ids = self.request.POST.get("dp_ids", "")
        try:
            dp_ids = validate_comma_separated_integer_list_and_split(dp_ids)
        except ValidationError as exc:
            raise ActionApi.AbortException("请求格式无效！") from exc
        dp_qs = DepartmentPayment.objects.filter(id__in=dp_ids)
        if dp_qs.exclude(status=DepartmentPayment.Statuses.Created).exists():
            raise ActionApi.AbortException("只能审核尚未审核的回款单！")
        self._private_dic = {"dp_queryset": dp_qs}

    def write_database(self):
        self._private_dic["dp_queryset"].update(status=DepartmentPayment.Statuses.Reviewed)

class ConfirmPayDepartmentPayment(ActionApi):
    """ 回款单确认支付 """

    need_permissions = ("manage_department_payment__pay", )

    def actions(self):
        dp_ids = self.request.POST.get("dp_ids", "")
        try:
            dp_ids = validate_comma_separated_integer_list_and_split(dp_ids)
        except ValidationError as exc:
            raise ActionApi.AbortException("请求格式无效！") from exc
        dp_qs = DepartmentPayment.objects.filter(id__in=dp_ids)
        if dp_qs.exclude(status=DepartmentPayment.Statuses.Reviewed).exists():
            raise ActionApi.AbortException('只能对"已审核"的回款单进行确认支付操作！')
        if dp_qs.exclude(src_department_id=self.request.session["user"]["department_id"]).exists():
            raise ActionApi.AbortException("只能对当前部门的回款单进行确认支付操作。")
        self._private_dic = {"dp_queryset": dp_qs}

    def write_database(self):
        self._private_dic["dp_queryset"].update(status=DepartmentPayment.Statuses.Paid)

class ConfirmSettleAccountsDepartmentPayment(ActionApi):
    """ 回款单确认结算 """

    need_permissions = ("manage_department_payment__settle", )

    def actions(self):
        dp_ids = self.request.POST.get("dp_ids", "")
        try:
            dp_ids = validate_comma_separated_integer_list_and_split(dp_ids)
        except ValidationError as exc:
            raise ActionApi.AbortException("请求格式无效！") from exc
        dp_qs = DepartmentPayment.objects.filter(id__in=dp_ids)
        if dp_qs.exclude(status=DepartmentPayment.Statuses.Paid).exists():
            raise ActionApi.AbortException('只能结算"已支付"的回款单。')
        self._private_dic = {"dp_queryset": dp_qs, "timezone_now": timezone.now()}

    def write_database(self):
        # 修改部门回款单状态
        self._private_dic["dp_queryset"].update(
            status=DepartmentPayment.Statuses.Settled,
            settle_accounts_time=self._private_dic["timezone_now"],
        )
        for dp in self._private_dic["dp_queryset"]:
            dp.update_customer_score_change()

    def actions_after_success(self):
        timezone_now = self._private_dic["timezone_now"]
        timezone_now_str = timezone.make_naive(timezone_now).strftime("%Y-%m-%d %H:%M:%S")
        self.response_dic["data"]["dp_settle_accounts_time"] = timezone_now_str
        self.response_dic["data"]["dp_settle_accounts_time_timestamp"] = timezone_now.timestamp()

class DropCargoPricePayment(ActionApi):
    """ 删除转账单 """

    need_permissions = ("manage_cargo_price_payment__add_edit_delete_submit", )

    def actions(self):
        try:
            cpp_id = self.request.POST.get("cpp_id", "").strip()
            cpp_obj = CargoPricePayment.objects.get(id=cpp_id)
        except (ValueError, CargoPricePayment.DoesNotExist) as exc:
            raise ActionApi.AbortException("该转账单不存在！") from exc
        if cpp_obj.status not in (CargoPricePayment.Statuses.Created, CargoPricePayment.Statuses.Rejected):
            raise ActionApi.AbortException("提交后的转账单不允许删除！")
        if cpp_obj.create_user != get_logged_user(self.request):
            raise ActionApi.AbortException("你只能删除自己创建的转账单！")
        self._private_dic["cpp_obj"] = cpp_obj

    def write_database(self):
        self._private_dic["cpp_obj"].delete()

class ConfirmSubmitCargoPricePayment(ActionApi):
    """ 确认提交转账单 """

    need_permissions = ("manage_cargo_price_payment__add_edit_delete_submit", )

    def actions(self):
        try:
            cpp_id = self.request.POST.get("cpp_id", "").strip()
            cpp_obj = CargoPricePayment.objects.get(id=cpp_id)
        except (ValueError, CargoPricePayment.DoesNotExist) as exc:
            raise ActionApi.AbortException("该转账单不存在！") from exc
        if cpp_obj.status not in (CargoPricePayment.Statuses.Created, CargoPricePayment.Statuses.Rejected):
            raise ActionApi.AbortException('只允许对"已创建"状态的转账单进行确认提交操作！')
        if cpp_obj.create_user != get_logged_user(self.request):
            raise ActionApi.AbortException("你只能提交自己创建的转账单！")
        self._private_dic["cpp_obj"] = cpp_obj

    def write_database(self):
        self._private_dic["cpp_obj"].status = CargoPricePayment.Statuses.Submitted
        self._private_dic["cpp_obj"].save()

class ConfirmReviewCargoPricePayment(ActionApi):
    """ 审核转账单 """

    need_permissions = ("manage_cargo_price_payment__review_reject", )

    def actions(self):
        try:
            cpp_id = self.request.POST.get("cpp_id", "").strip()
            cpp_obj = CargoPricePayment.objects.get(id=cpp_id)
        except (ValueError, CargoPricePayment.DoesNotExist) as exc:
            raise ActionApi.AbortException("该转账单不存在！") from exc
        if cpp_obj.status != CargoPricePayment.Statuses.Submitted:
            raise ActionApi.AbortException('只允许对"已提交"状态的转账单进行审核操作！')
        self._private_dic["cpp_obj"] = cpp_obj

    def write_database(self):
        self._private_dic["cpp_obj"].status = CargoPricePayment.Statuses.Reviewed
        self._private_dic["cpp_obj"].reject_reason = ""
        self._private_dic["cpp_obj"].save()

class ConfirmRejectCargoPricePayment(ActionApi):
    """ 驳回转账单 """

    need_permissions = ("manage_cargo_price_payment__review_reject", )

    def actions(self):
        try:
            cpp_id = self.request.POST.get("cpp_id", "").strip()
            reject_reason = self.request.POST.get("reject_reason", "").strip()
            cpp_obj = CargoPricePayment.objects.get(id=cpp_id)
        except (ValueError, CargoPricePayment.DoesNotExist) as exc:
            raise ActionApi.AbortException("该转账单不存在！") from exc
        if not reject_reason:
            raise ActionApi.AbortException("驳回原因不允许为空！")
        if cpp_obj.status != CargoPricePayment.Statuses.Submitted:
            raise ActionApi.AbortException('只允许对"已提交"状态的转账单进行驳回操作！')
        self._private_dic["cpp_obj"] = cpp_obj
        self._private_dic["reject_reason"] = reject_reason

    def write_database(self):
        self._private_dic["cpp_obj"].status = CargoPricePayment.Statuses.Rejected
        self._private_dic["cpp_obj"].reject_reason = self._private_dic["reject_reason"]
        self._private_dic["cpp_obj"].save()

class ConfirmPayCargoPricePayment(ActionApi):
    """ 确认支付转账单 """

    need_permissions = ("manage_cargo_price_payment__pay", )

    def actions(self):
        try:
            cpp_id = self.request.POST.get("cpp_id", "").strip()
            cpp_obj = CargoPricePayment.objects.get(id=cpp_id)
        except (ValueError, CargoPricePayment.DoesNotExist) as exc:
            raise ActionApi.AbortException("该转账单不存在！") from exc
        if cpp_obj.status != CargoPricePayment.Statuses.Reviewed:
            raise ActionApi.AbortException('只允许对"已审核"状态的转账单进行确认支付操作！')
        self._private_dic = {"cpp_obj": cpp_obj, "timezone_now": timezone.now()}

    def write_database(self):
        cpp_obj = self._private_dic["cpp_obj"]
        cpp_obj.status = CargoPricePayment.Statuses.Paid
        cpp_obj.settle_accounts_time = self._private_dic["timezone_now"]
        cpp_obj.save()

    def actions_after_success(self):
        timezone_now = self._private_dic["timezone_now"]
        timezone_now_str = timezone.make_naive(timezone_now).strftime("%Y-%m-%d %H:%M:%S")
        self.response_dic["data"]["cpp_settle_accounts_time"] = timezone_now_str
        self.response_dic["data"]["cpp_settle_accounts_time_timestamp"] = timezone_now.timestamp()
