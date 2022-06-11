import datetime as datetime_

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.safestring import mark_safe
from django.views import View
from django.views.decorators.http import require_POST
from django.conf import settings
from django.db import transaction
from django.db.models import Q, Count, Sum
from django.core.signals import got_request_exception

from . import forms
from .models import (
    User, Waybill, Department, WaybillRouting, TransportOut, DepartmentPayment, CargoPricePayment, CustomerScoreLog
)
from .common import (
    get_global_settings, login_required, check_permission, check_administrator, is_logged_user_has_perm,
    get_logged_user, get_logged_user_type, is_logged_user_is_goods_yard,
    waybill_to_dict, transport_out_to_dict, department_payment_to_dict, cargo_price_payment_to_dict,
)
from utils.common import del_session_item, validate_comma_separated_integer_list_and_split


class WaybillSearchView(View):

    form_class = forms.WaybillSearchForm
    template_name = ""
    need_login = True
    need_permissions = ()

    def __init__(self, *args, **kwargs):
        assert getattr(self, "template_name"), (
            "Subclasses inherited must specify the 'template_name' property when defining!"
        )
        assert isinstance(self.form_class(), forms.WaybillSearchForm)
        super().__init__(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return render(
            request,
            self.template_name,
            {
                "form": self.form_class.init_from_request(request),
                "waybill_list": [],
                "logged_user_type": get_logged_user_type(request),
            }
        )

    def post(self, request, *args, **kwargs):
        form = self.form_class.init_from_request(request, data=request.POST)
        waybill_list = []
        if form.is_valid():
            try:
                waybill_list = form.gen_waybill_list_to_queryset()
            except:
                if settings.DEBUG:
                    raise
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "waybill_list": waybill_list,
                "logged_user_type": get_logged_user_type(request),
            }
        )

    def dispatch(self, request, *args, **kwargs):
        if self.need_login and not request.session.get("user"):
            return redirect("wuliu:login")
        for perm in self.need_permissions:
            if not is_logged_user_has_perm(request, perm):
                return HttpResponseForbidden()
        return super().dispatch(request, *args, **kwargs)

def _transport_out_detail_view(request, render_path):
    transport_out_id = request.GET.get("transport_out_id")
    if not transport_out_id:
        return HttpResponseBadRequest()
    transport_out = get_object_or_404(TransportOut, pk=transport_out_id)
    to_dic = transport_out_to_dict(transport_out)
    # 注: 此处只能通过data参数传入TransportOut的字典格式, 不能使用instance参数传入TransportOut对象
    # 否则前端渲染时仍然是从instance中取值, 导致某些表单字段不显示
    form = forms.TransportOutForm.init_from_request(request, data=to_dic)
    # form = forms.TransportOutForm.init_from_request(request, instance=transport_out)
    form.add_id_field(id_=transport_out.id, id_full=transport_out.get_full_id)
    form.change_to_detail_form()
    return render(
        request,
        render_path,
        {
            "form": form,
            "detail_view": True,
            "waybills_info_list": transport_out.waybills.all().select_related("src_department", "dst_department"),
        }
    )

def _transport_out_search_view(request, render_path, search_mode):
    assert search_mode in ("src", "dst"), 'search_mode参数只能为"src"或"dst"'
    if request.method == "GET":
        form = forms.TransportOutSearchForm.init_from_request(request, search_mode=search_mode)
        return render(
            request,
            render_path,
            {
                "form": form,
                "transport_out_list": [],
            }
        )
    if request.method == "POST":
        form = forms.TransportOutSearchForm.init_from_request(request, data=request.POST, search_mode=search_mode)
        transport_out_list = []
        if form.is_valid():
            try:
                transport_out_list = form.gen_transport_out_list_to_queryset()
            except ValueError:
                pass
        return render(
            request,
            render_path,
            {
                "form": form,
                "transport_out_list": transport_out_list,
            }
        )

def login(request):

    def _login_abort(message_text):
        messages.error(request, message_text)
        return redirect("wuliu:login")

    if request.method == "GET":
        if request.session.get('user'):
            return redirect("wuliu:welcome")
        return render(request, 'wuliu/login.html')
    if request.method == "POST":
        username = request.POST.get('username')
        password = request.POST.get('password')
        try:
            user = User.objects.get(name=username)
        except User.DoesNotExist:
            return _login_abort('用户名或密码错误，请重新输入！')
        if not user.enabled:
            return _login_abort('该用户未被启用，请联系管理员！')
        if not check_password(password, user.password):
            return _login_abort('用户名或密码错误，请重新输入！')
        request.session['user'] = {
            "logged_in_time": timezone.make_naive(timezone.now()).strftime("%Y-%m-%d %H:%M:%S"),
            "id": user.id,
            "name": user.name,
            "department_id": user.department_id,
            "department_name": user.department.name,
        }
        return redirect("wuliu:welcome")

def logout(request):
    request.session.flush()
    request.COOKIES.clear()
    return redirect("wuliu:login")

@login_required()
def welcome(request):
    # messages.debug(request, "Test debug message...")
    # messages.info(request, "Test info message...")
    # messages.success(request, "Test success message...")
    # messages.warning(request, "Test warning message...")
    # messages.error(request, "Test error message...")
    logged_user_type = get_logged_user_type(request)
    dic = {
        "today": {"waybill": 0, "transport_out": 0, "arrival": 0, "sign_for": 0},
        "wait": {"waybill": 0, "transport_out": 0, "arrival": 0, "sign_for": 0},
    }
    today_start_datetime = timezone.make_aware(
        timezone.datetime.combine(datetime_.date.today(), datetime_.time(0, 0))
    )
    today_end_datetime = timezone.make_aware(
        timezone.datetime.combine(datetime_.date.today(), datetime_.time(23, 59, 59))
    )
    today_weekday = timezone.now().isoweekday()
    weekdays = [
        {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}.get(
            today_weekday-i if today_weekday-i > 0 else today_weekday+7-i
        )
        for i in range(7)[::-1]
    ]
    # 14天内每天新增运单数和运费收入
    # 分支机构和货场只统计自己部门的新增运单数(虽然货场没有开票权限...)
    if logged_user_type == User.Types.GoodsYard:
        waybill_num_in_past_two_weeks = [0] * 14
        waybill_fee_in_past_two_weeks = [0] * 14
    else:
        waybill_num_in_past_two_weeks = []
        waybill_fee_in_past_two_weeks = []
        for i in range(14)[::-1]:
            queryset = Waybill.objects.only("pk", "fee").filter(
                    create_time__gte=today_start_datetime - timezone.timedelta(days=i),
                    create_time__lte=today_end_datetime - timezone.timedelta(days=i),
                ).exclude(status=Waybill.Statuses.Dropped)
            if logged_user_type == User.Types.Branch:
                queryset = queryset.filter(src_department__id=request.session["user"]["department_id"])
            day_info = queryset.aggregate(fee_total=Sum("fee"), count=Count("pk"))
            waybill_num_in_past_two_weeks.append(day_info["count"])
            waybill_fee_in_past_two_weeks.append(day_info["fee_total"] or 0)
    # 今日新增
    dic["today"]["waybill"] = waybill_num_in_past_two_weeks[-1]
    # 今日发车
    today_transport_out = TransportOut.objects.filter(
        start_time__gte=today_start_datetime,
        start_time__lte=today_end_datetime,
        status__in=(TransportOut.Statuses.OnTheWay, TransportOut.Statuses.Arrived),
    )
    if logged_user_type not in (User.Types.Administrator, User.Types.Company):
        today_transport_out = today_transport_out.filter(src_department__id=request.session["user"]["department_id"])
    dic["today"]["transport_out"] = today_transport_out.aggregate(_=Count("waybills"))["_"] or 0
    # 今日到货
    today_arrival = Waybill.objects.filter(
        arrival_time__gte=today_start_datetime, arrival_time__lte=today_end_datetime,
    )
    if logged_user_type not in (User.Types.Administrator, User.Types.Company):
        today_arrival = today_arrival.filter(dst_department__id=request.session["user"]["department_id"])
    dic["today"]["arrival"] = today_arrival.count() or 0
    # 今日签收
    today_sign_for = Waybill.objects.filter(
        sign_for_time__gte=today_start_datetime, sign_for_time__lte=today_end_datetime,
    )
    if logged_user_type not in (User.Types.Administrator, User.Types.Company):
        today_sign_for = today_sign_for.filter(dst_department__id=request.session["user"]["department_id"])
    dic["today"]["sign_for"] = today_sign_for.count()
    # 待确认订单
    dic["wait"]["waybill"] = 0
    # 待发车
    if logged_user_type == User.Types.GoodsYard:
        dic["wait"]["transport_out"] = Waybill.objects.filter(
                status__in=(Waybill.Statuses.GoodsYardArrived, Waybill.Statuses.GoodsYardLoaded)
            ).count()
    elif logged_user_type == User.Types.Branch:
        dic["wait"]["transport_out"] = Waybill.objects.filter(
                src_department__id=request.session["user"]["department_id"],
                status__in=(Waybill.Statuses.Created, Waybill.Statuses.Loaded),
            ).count()
    else:
        dic["wait"]["transport_out"] = Waybill.objects.filter(
                status__in=(Waybill.Statuses.Created, Waybill.Statuses.Loaded)
            ).count()
    # 待到车
    wait_arrival = TransportOut.objects.filter(status=TransportOut.Statuses.OnTheWay)
    if logged_user_type not in (User.Types.Administrator, User.Types.Company):
        wait_arrival = wait_arrival.filter(dst_department__id=request.session["user"]["department_id"])
    dic["wait"]["arrival"] = wait_arrival.count()
    # 待签收
    wait_sign_for = Waybill.objects.filter(status=Waybill.Statuses.Arrived)
    if logged_user_type not in (User.Types.Administrator, User.Types.Company):
        wait_sign_for = wait_sign_for.filter(dst_department__id=request.session["user"]["department_id"])
    dic["wait"]["sign_for"] = wait_sign_for.count()

    return render(
        request,
        "wuliu/welcome.html",
        {
            "data_dic": dic,
            "weekdays": weekdays,
            "waybill_num_last_week": waybill_num_in_past_two_weeks[:7],
            "waybill_num_this_week": waybill_num_in_past_two_weeks[7:],
            "waybill_num_this_week_total": sum(waybill_num_in_past_two_weeks[7:]),
            "waybill_num_change_rate_percentage": (
                (sum(waybill_num_in_past_two_weeks[7:]) / sum(waybill_num_in_past_two_weeks[:7]) - 1) * 100
                if sum(waybill_num_in_past_two_weeks[:7]) else (
                    100 if sum(waybill_num_in_past_two_weeks[7:]) else 0
                )
            ),
            "waybill_fee_last_week": waybill_fee_in_past_two_weeks[:7],
            "waybill_fee_this_week": waybill_fee_in_past_two_weeks[7:],
            "waybill_fee_this_week_total": sum(waybill_fee_in_past_two_weeks[7:]),
            "waybill_fee_change_rate_percentage": (
                (sum(waybill_fee_in_past_two_weeks[7:]) / sum(waybill_fee_in_past_two_weeks[:7]) - 1) * 100
                if sum(waybill_fee_in_past_two_weeks[:7]) else (
                    100 if sum(waybill_fee_in_past_two_weeks[7:]) else 0
                )
            ),
        }
    )

@login_required(raise_404=True)
def welcome_js(request):
    return render(
        request, "wuliu/_js/welcome_action.js.html",
        {
            "today": timezone.make_naive(timezone.now()).strftime("%Y-%m-%d"),
            "logged_user_dept_id": request.session["user"]["department_id"],
            "logged_user_type": get_logged_user_type(request),
        },
        content_type="text/javascript"
    )

@login_required()
def change_password(request):
    if request.method == "GET":
        return render(request, "wuliu/change_password.html", {"form": forms.ChangePassword()})
    if request.method == "POST":
        form = forms.ChangePassword(request.POST)
        custom_error_messages = []

        def _failed():
            messages.error(
                request,
                mark_safe("<br>".join([
                    "提交失败！",
                    *["%s: %s" % (k, "".join(v)) for k, v in form.errors.items()],
                    *custom_error_messages,
                ])),
            )
            return redirect("wuliu:change_password")

        if not form.is_valid():
            return _failed()
        user = get_logged_user(request)
        form_cleaned_data = form.cleaned_data
        if not check_password(form_cleaned_data["old_password"], user.password):
            custom_error_messages.append("旧密码错误！")
            return _failed()
        if form_cleaned_data["new_password"] != form_cleaned_data["new_password_again"]:
            custom_error_messages.append("两次输入的新密码不一致！")
            return _failed()
        try:
            with transaction.atomic():
                user.password = make_password(form_cleaned_data["new_password"])
                user.save()
        except Exception as e:
            got_request_exception.send(None, request=request)
            custom_error_messages.append(str(e))
            custom_error_messages.append("修改密码失败！请联系管理员！")
            return _failed()
        messages.success(request, "密码修成功！")
        return redirect("wuliu:welcome")

@check_administrator
def manage_users(request):
    if request.method == "GET":
        return render(
            request,
            "wuliu/settings/user/manage_users.html",
            {"form": forms.ManageUsers()},
        )
    if request.method == "POST":
        form = forms.ManageUsers(request.POST)
        custom_error_messages = []

        def _failed():
            messages.error(
                request,
                mark_safe("<br>".join([
                    "提交失败！",
                    *["%s: %s" % (k, "".join(v)) for k, v in form.errors.items()],
                    *custom_error_messages,
                ])),
            )
            return redirect("wuliu:manage_users")

        if not form.is_valid():
            return _failed()
        form_cleaned_data = form.cleaned_data
        user = form_cleaned_data["user"]
        user_dept = Department.objects.get(id=form_cleaned_data["department"])
        reset_password_flag = False
        if form_cleaned_data["reset_password"] and form_cleaned_data["reset_password_again"]:
            reset_password_flag = True
            if form_cleaned_data["reset_password"] != form_cleaned_data["reset_password_again"]:
                custom_error_messages.append("两次输入的密码不一致！")
                return _failed()
        try:
            with transaction.atomic():
                user.enabled = form_cleaned_data["enabled"]
                user.administrator = form_cleaned_data["administrator"]
                user.department = user_dept
                if reset_password_flag:
                    user.password = make_password(form_cleaned_data["reset_password"])
                user.save()
        except Exception as e:
            got_request_exception.send(None, request=request)
            custom_error_messages.append(str(e))
            return _failed()
        messages.success(request, "用户 %s 编辑成功！" % user.name)
        return redirect("wuliu:manage_users")

@check_administrator
def add_user(request):
    if request.method == "GET":
        return render(
            request,
            "wuliu/settings/user/add_user.html",
            {"form": forms.UserForm()},
        )
    if request.method == "POST":
        form = forms.UserForm(request.POST)
        custom_error_messages = []

        def _failed():
            messages.error(
                request,
                mark_safe("<br>".join([
                    "提交失败！",
                    *["%s: %s" % (k, "".join(v)) for k, v in form.errors.items()],
                    *custom_error_messages,
                ])),
            )
            return redirect("wuliu:add_user")

        if not form.is_valid():
            return _failed()
        form_cleaned_data = form.cleaned_data
        # 不需要检查用户名是否已被占用, form.is_valid时就已经检查了
        form.instance.password = make_password(form_cleaned_data["password"])
        try:
            with transaction.atomic():
                new_user = form.save()
        except Exception as e:
            got_request_exception.send(None, request=request)
            custom_error_messages.append(str(e))
            return _failed()
        messages.success(request, "用户 %s 新增成功！请为该用户分配权限。" % new_user.name)
        return redirect("wuliu:manage_users")

@check_administrator
def manage_user_permission(request):
    if request.method == "GET":
        return render(
            request,
            "wuliu/settings/user_permission/manage_user_permission.html",
            {"form": forms.ManageUserPermission()},
        )
    if request.method == "POST":
        form = forms.ManageUserPermission(request.POST)
        custom_error_messages = []

        def _failed():
            messages.error(
                request,
                mark_safe("<br>".join([
                    "提交失败！",
                    *["%s: %s" % (k, "".join(v)) for k, v in form.errors.items()],
                    *custom_error_messages,
                ])),
            )
            return redirect("wuliu:manage_user_permission")

        if not form.is_valid():
            return _failed()
        form_cleaned_data = form.cleaned_data
        user = form_cleaned_data["user"]
        permission_source_user = form_cleaned_data["permission_source_user"]
        if permission_source_user:
            permission_queryset = permission_source_user.permission.all()
        else:
            permission_queryset = form_cleaned_data["permission"]
        try:
            with transaction.atomic():
                user.permission.set(permission_queryset)
        except Exception as e:
            got_request_exception.send(None, request=request)
            custom_error_messages.append(str(e))
            return _failed()
        messages.success(request, "用户 %s 的权限修改成功！" % user.name)
        return redirect("wuliu:manage_user_permission")

@check_administrator
def batch_edit_user_permission(request):
    if request.method == "GET":
        return render(
            request,
            "wuliu/settings/user_permission/batch_edit_user_permission.html",
            {"form": forms.BatchEditUserPermission()},
        )
    if request.method == "POST":
        form = forms.BatchEditUserPermission(request.POST)
        custom_error_messages = []

        def _failed():
            messages.error(
                request,
                mark_safe("<br>".join([
                    "提交失败！",
                    *["%s: %s" % (k, "".join(v)) for k, v in form.errors.items()],
                    *custom_error_messages,
                ])),
            )
            return redirect("wuliu:batch_edit_user_permission")

        if not form.is_valid():
            return _failed()
        form_cleaned_data = form.cleaned_data
        users = form_cleaned_data["user"]
        permission_queryset_ids = form_cleaned_data["permission"].values_list("id", flat=True)
        is_grant = form.cleaned_data["grant_or_deny"]
        try:
            with transaction.atomic():
                if is_grant:
                    for user in users:
                        user.permission.add(*permission_queryset_ids)
                else:
                    for user in users:
                        user.permission.remove(*permission_queryset_ids)
        except Exception as e:
            got_request_exception.send(None, request=request)
            custom_error_messages.append(str(e))
            return _failed()
        messages.success(request, "批量修改用户权限成功！")
        return redirect("wuliu:batch_edit_user_permission")

@login_required()
@check_permission("add_waybill")
def add_waybill(request):
    if request.method == "GET":
        form = forms.WaybillForm.init_from_request(request)
        return render(request, 'wuliu/waybill/add_waybill.html', {"form": form})
    if request.method == "POST":
        custom_error_messages = []

        def _failed():
            messages.error(
                request,
                mark_safe("<br>".join([
                    "提交失败！",
                    *["%s: %s" % (k, "".join(v)) for k, v in form.errors.items()],
                    *custom_error_messages,
                ])),
            )
            return render(request, 'wuliu/waybill/add_waybill.html', {"form": form})

        form = forms.WaybillForm(request.POST)
        if not form.is_valid():
            return _failed()
        try:
            form.check_again(request)
        except AssertionError as e:
            custom_error_messages.append(str(e))
            return _failed()
        try:
            with transaction.atomic():
                new_waybill = form.save()
                if (new_waybill.create_time.hour, new_waybill.create_time.second) == (23, 59):
                    new_waybill.create_time += timezone.timedelta(seconds=2)
                    new_waybill.save(update_fields=["create_time"])
                WaybillRouting.objects.create(
                    waybill=new_waybill,
                    time=new_waybill.create_time,
                    operation_type=Waybill.Statuses.Created,
                    operation_dept=get_logged_user(request).department,
                    operation_user=get_logged_user(request),
                )
        except ValidationError as e:
            custom_error_messages += e.messages
            return _failed()
        except Exception as e:
            if settings.DEBUG:
                raise
            got_request_exception.send(None, request=request)
            custom_error_messages.append(str(e))
            custom_error_messages.append("运单保存失败！请联系管理员！")
            return _failed()
        messages.success(request, "操作成功！")
        return redirect("wuliu:add_waybill")

@login_required(raise_404=True)
@check_permission("manage_waybill__edit_delete_print")
def edit_waybill(request):
    if request.method == "GET":
        waybill_id = request.GET.get("waybill_id")
        if not waybill_id:
            return HttpResponseBadRequest()
        waybill = get_object_or_404(Waybill, pk=waybill_id)
        # 禁止跨部门修改运单
        if waybill.src_department_id != request.session["user"]["department_id"]:
            return HttpResponseForbidden()
        # 禁止修改已发车的运单
        if waybill.status > Waybill.Statuses.Loaded:
            return HttpResponseForbidden()
        form = forms.WaybillForm.init_from_request(request, instance=waybill)
        form.add_id_field(id_=waybill.id, id_full=waybill.get_full_id)
        return render(request, 'wuliu/waybill/edit_waybill.html', {"form": form})
    if request.method == "POST":
        custom_error_messages = []
        try:
            waybill_id_full = request.POST["id_"]
            waybill_id = request.POST["id"]
        except KeyError:
            return HttpResponseBadRequest()
        form = forms.WaybillForm(request.POST)

        def _failed():
            messages.error(
                request,
                mark_safe("<br>".join([
                    "提交失败！",
                    *["%s: %s" % (k, "".join(v)) for k, v in form.errors.items()],
                    *custom_error_messages,
                ])),
            )
            form.add_id_field(id_=waybill_id, id_full=waybill_id_full)
            return render(request, 'wuliu/waybill/edit_waybill.html', {"form": form})

        try:
            waybill = Waybill.objects.get(id=waybill_id)
        except Waybill.DoesNotExist:
            custom_error_messages.append("运单 %s 不存在！" % waybill_id_full)
            return _failed()
        form = forms.WaybillForm(request.POST, instance=waybill)
        if not form.is_valid():
            return _failed()
        try:
            form.check_again(request)
        except AssertionError as e:
            custom_error_messages.append(str(e))
            return _failed()
        # 禁止跨部门修改运单
        # 必须要求：旧发货部门 == 新发货部门 == 用户当前部门
        # WaybillForm.check_again方法中已经检查"新发货部门 == 用户当前部门"
        if waybill.src_department_id != request.session["user"]["department_id"]:
            custom_error_messages.append("禁止跨部门修改运单！")
            return _failed()
        # 禁止修改已发车的运单
        if waybill.status > Waybill.Statuses.Loaded:
            custom_error_messages.append("运单 %s 已发车，禁止修改！" % waybill_id_full)
            return _failed()
        try:
            form.save()
        except ValidationError as e:
            custom_error_messages += e.messages
            return _failed()
        except Exception as e:
            if settings.DEBUG:
                raise
            got_request_exception.send(None, request=request)
            custom_error_messages.append(str(e))
            custom_error_messages.append("运单保存失败！请联系管理员！")
            return _failed()
        messages.success(request, "运单 %s 修改成功！" % waybill_id_full)
        return redirect("wuliu:manage_waybill")

@login_required(raise_404=True)
def edit_waybill_js(request):
    return render(
        request, "wuliu/_js/edit_waybill.js.html",
        {"handling_fee_ratio": get_global_settings().handling_fee_ratio},
        content_type="text/javascript"
    )

@login_required(raise_404=True)
def detail_waybill(request, waybill_id):
    waybill = get_object_or_404(Waybill, pk=waybill_id)
    waybill_dic = waybill_to_dict(waybill)
    # 注: 此处只能通过data参数传入Waybill的字典格式, 不能使用instance参数传入Waybill对象
    # 否则前端渲染时仍然是从instance中取值, 导致某些表单字段不显示
    form = forms.WaybillForm.init_from_request(request, data=waybill_dic)
    # form = forms.WaybillForm.init_from_request(request, instance=waybill)
    form.add_id_field(id_=waybill.id, id_full=waybill.get_full_id)
    form.change_to_detail_form()
    wb_routing = WaybillRouting.objects.filter(waybill_id=waybill_id)
    wb_routing = wb_routing.select_related("operation_dept", "operation_user")
    if waybill.cargo_price > 0:
        wb_final_cpp_fee = waybill.cargo_price - waybill.cargo_handling_fee - (
            waybill.fee if waybill.fee_type == Waybill.FeeTypes.Deduction else 0
        )
    else:
        wb_final_cpp_fee = 0
    return render(
        request,
        'wuliu/waybill/detail_waybill.html',
        {
            "form": form,
            "detail_view": True,
            "wb": waybill,
            "wb_routing": wb_routing,
            "wb_final_cpp_fee": wb_final_cpp_fee,
        }
    )

class ManageWaybill(WaybillSearchView):

    template_name = "wuliu/waybill/manage_waybill.html"
    need_permissions = ("manage_waybill__search", )

    def dispatch(self, request, *args, **kwargs):
        del_session_item(
            request,
            "add_transport_out__waybills_ready_to_add",
            "add_transport_out__waybills_added",
            "edit_transport_out__transport_out_id",
            "edit_transport_out__waybills_ready_to_add",
            "edit_transport_out__waybills_added",
        )
        return super().dispatch(request, *args, **kwargs)

@login_required(raise_404=True)
def manage_waybill_js(request):
    return render(
        request,
        'wuliu/_js/manage_waybill.js.html',
        {"logged_user_type": get_logged_user_type(request)},
        content_type="text/javascript"
    )

@login_required()
@require_POST
def quick_search_waybill(request):
    form = forms.WaybillQuickSearchForm(request.POST)
    waybill_list = []
    if form.is_valid():
        try:
            waybill_list = form.gen_waybill_list_to_queryset()
        except:
            if settings.DEBUG:
                raise
    return render(request, "wuliu/waybill/quick_search_waybill.html", {"waybill_list": waybill_list})

@login_required(raise_404=True)
@check_permission("manage_sign_for")
def confirm_return_waybill(request):

    def _check_waybill(waybill_):
        assert waybill_.dst_department_id == request.session["user"]["department_id"], "禁止跨部门操作运单"
        assert waybill_.status == Waybill.Statuses.Arrived, "只允许对到站待提状态的运单进行退货操作"
        assert waybill_.return_waybill is None, "退货运单禁止再次进行退货操作"

    if request.method == "GET":
        waybill_id = request.GET.get("waybill_id")
        if not waybill_id:
            return HttpResponseBadRequest()
        waybill = get_object_or_404(Waybill, pk=waybill_id)
        try:
            _check_waybill(waybill)
        except AssertionError as exc:
            messages.error(request, "操作失败：%s" % exc)
            return redirect("wuliu:manage_sign_for")
        return render(
            request,
            "wuliu/waybill/confirm_return_waybill.html",
            {"waybill": waybill}
        )
    if request.method == "POST":
        return_waybill_id = request.POST.get("return_waybill_id")
        return_reason = request.POST.get("return_reason").strip()
        waybill = get_object_or_404(Waybill, pk=return_waybill_id)
        try:
            _check_waybill(waybill)
        except AssertionError as exc:
            messages.error(request, "操作失败：%s" % exc)
            return redirect("wuliu:manage_sign_for")
        if not return_reason:
            messages.error(request, "请仔细填写退货原因！")
            return redirect("%s?%s" % (
                reverse("wuliu:confirm_return_waybill"),
                urlencode({"waybill_id": return_waybill_id})
            ))
        timezone_now = timezone.now()
        try:
            with transaction.atomic():
                returned_waybill = Waybill.objects.create(
                    src_department=waybill.dst_department,
                    dst_department=waybill.src_department,
                    src_customer=waybill.dst_customer,
                    src_customer_name=waybill.dst_customer_name,
                    src_customer_phone=waybill.dst_customer_phone,
                    src_customer_credential_num=waybill.dst_customer_credential_num,
                    src_customer_address=waybill.dst_customer_address,
                    dst_customer=waybill.src_customer,
                    dst_customer_name=waybill.src_customer_name,
                    dst_customer_phone=waybill.src_customer_phone,
                    dst_customer_credential_num=waybill.src_customer_credential_num,
                    dst_customer_address=waybill.src_customer_address,
                    cargo_name=waybill.cargo_name,
                    cargo_num=waybill.cargo_num,
                    cargo_volume=waybill.cargo_volume,
                    cargo_weight=waybill.cargo_weight,
                    cargo_price=0,  # 退货运单货款强制设置为0
                    cargo_handling_fee=0,
                    # 如果原始运单运费类型不是现付, 则退货运单运费为原始运单运费的双倍(因为我们没收到运费)
                    fee=waybill.fee if waybill.fee_type == Waybill.FeeTypes.Now else waybill.fee * 2,
                    fee_type=Waybill.FeeTypes.SignFor,  # 退货运单运费强制设置为提付
                    return_waybill=waybill,
                )
                returned_waybill.waybillrouting_set.create(
                    # waybill=waybill,
                    time=timezone_now,
                    operation_type=Waybill.Statuses.Created,
                    operation_dept=get_logged_user(request).department,
                    operation_user=get_logged_user(request),
                    operation_info={"return_reason": return_reason},
                )
                waybill.status = Waybill.Statuses.Returned
                waybill.save()
                waybill.waybillrouting_set.create(
                    # waybill=waybill,
                    time=timezone_now,
                    operation_type=Waybill.Statuses.Returned,
                    operation_dept=get_logged_user(request).department,
                    operation_user=get_logged_user(request),
                    operation_info={"return_reason": return_reason, "return_waybill_id": returned_waybill.id},
                )
        except Exception as e:
            if settings.DEBUG:
                raise
            got_request_exception.send(None, request=request)
            messages.error(request, str(e))
            messages.error(request, "保存数据库失败，请联系管理员！")
            return redirect("%s?%s" % (
                reverse("wuliu:confirm_return_waybill"),
                urlencode({"waybill_id": return_waybill_id}),
            ))
        messages.success(request, mark_safe('退货提交成功，退货运单单号【<a href="%s">%s</a>】' % (
            reverse("wuliu:detail_waybill", args=[returned_waybill.id, ]),
            returned_waybill.get_full_id,
        )))
        return redirect("wuliu:manage_waybill")

@login_required()
@check_permission("manage_transport_out__add_edit_delete_start")
def add_transport_out(request):
    if request.method == "GET":
        form = forms.TransportOutForm.init_from_request(request)
        # 会话操作
        del_session_item(
            request,
            "edit_transport_out__transport_out_id",
            "edit_transport_out__waybills_ready_to_add",
            "edit_transport_out__waybills_added",
        )
        waybills_added = [int(wb_id) for wb_id in request.session.get("add_transport_out__waybills_added", [])]
        waybills_add = [int(wb_id) for wb_id in request.session.pop("add_transport_out__waybills_ready_to_add", [])]
        waybills_added = sorted(set(waybills_added) | set(waybills_add), key=int)
        request.session["add_transport_out__waybills_added"] = waybills_added
        return render(
            request,
            'wuliu/transport_out/add_transport_out.html',
            {
                "form": form,
                "waybills_info_list": Waybill.objects.filter(
                        id__in=waybills_added
                    ).select_related("src_department", "dst_department"),
            }
        )
    if request.method == "POST":
        custom_error_messages = []

        def _failed():
            messages.error(
                request,
                mark_safe("<br>".join([
                    "提交失败！",
                    *["%s: %s" % (k, "".join(v)) for k, v in form.errors.items()],
                    *custom_error_messages,
                ])),
            )
            return redirect("wuliu:add_transport_out")

        form = forms.TransportOutForm(request.POST)
        if not form.is_valid():
            return _failed()
        try:
            form.check_again(request)
        except AssertionError as e:
            custom_error_messages.append(str(e))
            return _failed()
        if is_logged_user_is_goods_yard(request):
            wb_new_status = Waybill.Statuses.GoodsYardLoaded
        else:
            wb_new_status = Waybill.Statuses.Loaded
        try:
            with transaction.atomic():
                to_obj = form.save()
                to_obj.waybills.update(status=wb_new_status)
        except Exception as e:
            if settings.DEBUG:
                raise
            got_request_exception.send(None, request=request)
            custom_error_messages.append(str(e))
            custom_error_messages.append("车次信息保存失败！请联系管理员！")
            return _failed()
        del_session_item(request, "add_transport_out__waybills_added", "add_transport_out__waybills_ready_to_add")
        messages.success(request, "操作成功！")
        return redirect("wuliu:manage_transport_out")

@login_required(raise_404=True)
@check_permission("manage_transport_out__add_edit_delete_start")
def edit_transport_out(request):
    if request.method == "GET":
        transport_out_id = request.GET.get("transport_out_id")
        if not transport_out_id:
            return HttpResponseBadRequest()
        transport_out = get_object_or_404(TransportOut, pk=transport_out_id)
        # 禁止修改已发车的车次
        if transport_out.status != TransportOut.Statuses.Ready:
            return HttpResponseForbidden()
        # 禁止跨部门修改车次
        if transport_out.src_department_id != request.session["user"]["department_id"]:
            return HttpResponseForbidden()
        form = forms.TransportOutForm.init_from_request(request, instance=transport_out)
        form.add_id_field(id_=transport_out_id, id_full=transport_out.get_full_id)
        # 会话操作
        del_session_item(request, "add_transport_out__waybills_ready_to_add", "add_transport_out__waybills_added")
        if int(request.session.get("edit_transport_out__transport_out_id", -1)) != int(transport_out_id):
            del_session_item(
                request,
                "edit_transport_out__waybills_ready_to_add",
                "edit_transport_out__waybills_added",
            )
        request.session["edit_transport_out__transport_out_id"] = int(transport_out_id)
        waybills_added = request.session.get(
            "edit_transport_out__waybills_added", list(transport_out.waybills.values_list("id", flat=True))
        )
        waybills_add = [int(wb_id) for wb_id in request.session.pop("edit_transport_out__waybills_ready_to_add", [])]
        waybills_added = sorted(set(waybills_added) | set(waybills_add), key=int)
        request.session["edit_transport_out__waybills_added"] = waybills_added
        return render(
            request,
            'wuliu/transport_out/edit_transport_out.html',
            {
                "form": form,
                "waybills_info_list": Waybill.objects.filter(
                        id__in=waybills_added
                    ).select_related("src_department", "dst_department")
            }
        )
    if request.method == "POST":
        custom_error_messages = []
        try:
            transport_out_id_full = request.POST["id_"]
            transport_out_id = request.POST["id"]
        except KeyError:
            return HttpResponseBadRequest()
        form = forms.TransportOutForm(request.POST)

        def _failed():
            messages.error(
                request,
                mark_safe("<br>".join([
                    "提交失败！",
                    *["%s: %s" % (k, "".join(v)) for k, v in form.errors.items()],
                    *custom_error_messages,
                ])),
            )
            form.add_id_field(id_=transport_out_id, id_full=transport_out_id_full)
            return render(
                request,
                'wuliu/transport_out/edit_transport_out.html',
                {
                    "form": form,
                    "waybills_info_list": Waybill.objects.filter(
                            id__in=request.session["edit_transport_out__waybills_added"]
                        ).select_related("src_department", "dst_department")
                }
            )

        try:
            transport_out = TransportOut.objects.get(id=transport_out_id)
        except TransportOut.DoesNotExist:
            custom_error_messages.append("车次 %s 不存在！" % transport_out_id_full)
            return _failed()
        if transport_out.status != TransportOut.Statuses.Ready:
            custom_error_messages.append("禁止修改已发车的车次！")
            return _failed()
        if transport_out.src_department_id != request.session["user"]["department_id"]:
            custom_error_messages.append("禁止跨部门修改车次信息！")
            return _failed()
        form = forms.TransportOutForm(request.POST, instance=transport_out)
        if not form.is_valid():
            return _failed()
        try:
            form.check_again(request, transport_out_id=transport_out_id)
        except AssertionError as e:
            custom_error_messages.append(str(e))
            return _failed()
        if is_logged_user_is_goods_yard(request):
            wb_new_status = Waybill.Statuses.GoodsYardLoaded
        else:
            wb_new_status = Waybill.Statuses.Loaded
        try:
            with transaction.atomic():
                form.save()
                transport_out.waybills.update(status=wb_new_status)
        except Exception as e:
            got_request_exception.send(None, request=request)
            custom_error_messages.append(str(e))
            custom_error_messages.append("车次信息保存失败！请联系管理员！")
            return _failed()
        del_session_item(
            request,
            "edit_transport_out__transport_out_id",
            "edit_transport_out__waybills_added",
            "edit_transport_out__waybills_ready_to_add",
        )
        messages.success(request, "操作成功！")
        return redirect("wuliu:manage_transport_out")

@login_required(raise_404=True)
def edit_transport_out_js(request):
    return render(
        request,
        'wuliu/_js/edit_transport_out.js.html',
        {"logged_user_is_goods_yard": is_logged_user_is_goods_yard(request)},
        content_type="text/javascript"
    )

@login_required(raise_404=True)
@check_permission("manage_transport_out__search")
def detail_transport_out(request):
    return _transport_out_detail_view(request, "wuliu/transport_out/detail_transport_out.html")

@login_required()
@check_permission("manage_transport_out__search")
def manage_transport_out(request):
    return _transport_out_search_view(request, "wuliu/transport_out/manage_transport_out.html", search_mode="src")

@login_required(raise_404=True)
def manage_transport_out_js(request):
    return render(
        request,
        "wuliu/_js/manage_transport_out.js.html",
        content_type="text/javascript"
    )

class SearchWaybillsToTransportOut(WaybillSearchView):
    template_name = "wuliu/transport_out/search_waybills_to_transport_out.html"
    need_permissions = ("manage_transport_out__add_edit_delete_start", )

@login_required()
@check_permission("manage_transport_out__add_edit_delete_start")
@require_POST
def add_waybills_to_transport_out(request):
    try:
        wb_add_list = validate_comma_separated_integer_list_and_split(request.POST.get("wb_add_list", ""))
    except ValidationError:
        wb_add_list = []
        messages.error(request, "添加失败：无效的请求参数！")
    if request.session.get("edit_transport_out__transport_out_id"):
        if reverse("wuliu:manage_waybill") not in request.META["HTTP_REFERER"]:
            request.session["edit_transport_out__waybills_ready_to_add"] = wb_add_list
            return redirect("%s?%s" % (
                reverse("wuliu:edit_transport_out"),
                urlencode({"transport_out_id": request.session.get("edit_transport_out__transport_out_id")})
            ))
    request.session["add_transport_out__waybills_ready_to_add"] = wb_add_list
    return redirect("wuliu:add_transport_out")

@login_required()
@check_permission("manage_arrival")
def manage_arrival(request):
    return _transport_out_search_view(request, "wuliu/arrival/manage_arrival.html", search_mode="dst")

@login_required(raise_404=True)
@check_permission("manage_arrival")
def confirm_arrival(request):
    return _transport_out_detail_view(request, "wuliu/arrival/confirm_arrival.html")

class ManageSignFor(WaybillSearchView):
    form_class = forms.SignForSearchForm
    template_name = "wuliu/sign_for/manage_sign_for.html"
    need_permissions = ("manage_sign_for", )

@login_required(raise_404=True)
@check_permission("manage_sign_for")
def confirm_sign_for(request):
    try:
        sign_for_waybill_ids = validate_comma_separated_integer_list_and_split(
            request.GET.get("sign_for_waybill_ids", "")
        )
    except ValidationError:
        messages.error(request, "操作失败：无效的请求参数！")
        return redirect("wuliu:manage_sign_for")
    waybill_queryset = Waybill.objects.filter(id__in=sign_for_waybill_ids)
    # 运单到达部门必须与当前部门一致, 且必须都是"到站待提"状态
    if waybill_queryset.filter(
            ~Q(dst_department__id=request.session["user"]["department_id"]) |
            ~Q(status=Waybill.Statuses.Arrived)).exists():
        messages.error(request, "操作失败：请求签收的运单中存在状态异常的运单")
        return redirect("wuliu:manage_sign_for")
    return render(
        request,
        "wuliu/sign_for/confirm_sign_for.html",
        {"waybill_list": waybill_queryset}
    )

@login_required(raise_404=True)
def confirm_sign_for_js(request):
    return render(request, 'wuliu/_js/confirm_sign_for.js.html', content_type="text/javascript")

@login_required()
@check_permission("manage_department_payment__add_delete")
def add_department_payment(request):
    if request.method == "GET":
        form = forms.DepartmentPaymentAddForm.init_from_request(request)
        return render(request, "wuliu/finance/department_payment/add_department_payment.html", {"form": form})
    if request.method == "POST":
        custom_error_messages = []

        def _failed():
            messages.error(
                request,
                mark_safe("<br>".join([
                    "提交失败！",
                    *["%s: %s" % (k, "".join(v)) for k, v in form.errors.items()],
                    *custom_error_messages,
                ])),
            )
            return redirect("wuliu:add_department_payment")

        form = forms.DepartmentPaymentAddForm.init_from_request(request, data=request.POST)
        if not form.is_valid():
            return _failed()
        try:
            form.save_()
        except Exception as e:
            got_request_exception.send(None, request=request)
            custom_error_messages.append(str(e))
            return _failed()
        messages.success(request, "新增成功")
        return redirect("wuliu:manage_department_payment")

@login_required(raise_404=True)
@check_permission("manage_department_payment__search")
def detail_department_payment(request, dp_id):
    dp_obj = get_object_or_404(DepartmentPayment, pk=dp_id)
    dp_dic = department_payment_to_dict(dp_obj)
    form = forms.DepartmentPaymentDetailForm(dp_dic)
    return render(
        request,
        "wuliu/finance/department_payment/detail_department_payment.html",
        {
            "form": form,
            "dp_dic": dp_dic,
            "waybills_info_list": dp_obj.waybills.all().select_related("src_department", "dst_department"),
        }
    )

@login_required()
@check_permission("manage_department_payment__search")
def manage_department_payment(request):
    if request.method == "GET":
        form = forms.DepartmentPaymentSearchForm.init_from_request(request)
        return render(
            request,
            "wuliu/finance/department_payment/manage_department_payment.html",
            {
                "form": form,
                "department_payment_list": [],
            }
        )
    if request.method == "POST":
        form = forms.DepartmentPaymentSearchForm.init_from_request(request, data=request.POST)
        department_payment_list = []
        if form.is_valid():
            try:
                department_payment_list = form.gen_department_payment_list_to_queryset()
            except:
                if settings.DEBUG:
                    raise
        return render(
            request,
            "wuliu/finance/department_payment/manage_department_payment.html",
            {
                "form": form,
                "department_payment_list": department_payment_list,
            }
        )

@login_required(raise_404=True)
def manage_department_payment_js(request):
    return render(
        request,
        'wuliu/_js/manage_department_payment.js.html',
        content_type="text/javascript",
    )

@login_required()
@check_permission("manage_cargo_price_payment__add_edit_delete_submit")
def add_cargo_price_payment(request):
    if request.method == "GET":
        form = forms.CargoPricePaymentForm()
        return render(
            request, "wuliu/finance/cargo_price_payment/add_cargo_price_payment.html",
            {"form": form, "waybill_list": []}
        )
    if request.method == "POST":
        custom_error_messages = []
        form = forms.CargoPricePaymentForm(request.POST)

        def _failed():
            messages.error(
                request,
                mark_safe("<br>".join([
                    "提交失败！",
                    *["%s: %s" % (k, "".join(v)) for k, v in form.errors.items()],
                    *custom_error_messages,
                ])),
            )
            return render(
                request, "wuliu/finance/cargo_price_payment/add_cargo_price_payment.html",
                {"form": form, "waybill_list": []}
            )

        try:
            waybill_ids = validate_comma_separated_integer_list_and_split(request.POST.get("waybill_ids", ""))
        except ValidationError:
            custom_error_messages.append("无效的请求参数！")
            return _failed()
        if not form.is_valid():
            return _failed()
        # 对提交的费用运单列表再次进行检查
        if not all([
            waybill_ids,
            # 不能存在除"客户签收"状态之外的运单
            not Waybill.objects.filter(id__in=waybill_ids).exclude(status=Waybill.Statuses.SignedFor).exists(),
            # 不能存在"已存在于其他转账单中"的运单
            not Waybill.objects.filter(id__in=waybill_ids).filter(cargo_price_payment__isnull=False).exists(),
        ]):
            custom_error_messages.append("请检查各项内容是否填写规范！")
            return _failed()
        form.instance.create_user = get_logged_user(request)
        try:
            with transaction.atomic():
                cpp_obj = form.save()
                cpp_obj.waybill_set.set(Waybill.objects.filter(id__in=waybill_ids))
        except Exception as e:
            got_request_exception.send(None, request=request)
            custom_error_messages.append(str(e))
            custom_error_messages.append("转账单保存失败！请联系管理员！")
            return _failed()
        messages.success(request, "新增成功")
        return redirect("wuliu:manage_cargo_price_payment")

@login_required(raise_404=True)
@check_permission("manage_cargo_price_payment__search")
def detail_cargo_price_payment(request, cpp_id):
    cpp_obj = get_object_or_404(CargoPricePayment, pk=cpp_id)
    cpp_dic = cargo_price_payment_to_dict(cpp_obj)
    form = forms.CargoPricePaymentForm(cpp_dic)
    form.add_id_field(cpp_obj.id, cpp_obj.get_full_id)
    form.change_to_detail_form()
    return render(
        request, "wuliu/finance/cargo_price_payment/detail_cargo_price_payment.html",
        {
            "form": form,
            "waybill_list": cpp_obj.waybill_set.all().select_related("src_department", "dst_department"),
            "cpp_dic": cpp_dic,
            "detail_view": True
        }
    )

@login_required(raise_404=True)
@check_permission("manage_cargo_price_payment__add_edit_delete_submit")
def edit_cargo_price_payment(request):

    def _check_before_edit(cpp_obj_):
        # 只能在提交之前修改, 只有创建人拥有修改权限
        return any([
            cpp_obj_.status in (CargoPricePayment.Statuses.Created, CargoPricePayment.Statuses.Rejected),
            get_logged_user(request) == cpp_obj_.create_user,
        ])

    if request.method == "GET":
        cpp_id = request.GET.get("cpp_id")
        if not cpp_id:
            return HttpResponseBadRequest()
        cpp_obj = get_object_or_404(CargoPricePayment, pk=cpp_id)
        if not _check_before_edit(cpp_obj):
            return HttpResponseForbidden()
        form = forms.CargoPricePaymentForm(instance=cpp_obj)
        form.add_id_field(cpp_obj.id, cpp_obj.get_full_id)
        return render(
            request, "wuliu/finance/cargo_price_payment/edit_cargo_price_payment.html",
            {
                "form": form,
                "waybill_list": cpp_obj.waybill_set.all().select_related("src_department", "dst_department"),
            }
        )
    if request.method == "POST":
        custom_error_messages = []

        def _failed():
            messages.error(
                request,
                mark_safe("<br>".join([
                    "提交失败！",
                    *["%s: %s" % (k, "".join(v)) for k, v in form.errors.items()],
                    *custom_error_messages,
                ])),
            )
            return redirect("%s?%s" % (reverse("wuliu:edit_cargo_price_payment"), urlencode({"cpp_id": cpp_id})))

        try:
            waybill_ids = validate_comma_separated_integer_list_and_split(request.POST.get("waybill_ids", ""))
        except ValidationError:
            custom_error_messages.append("无效的请求参数！！")
            return _failed()
        cpp_id = request.POST.get("id")
        if not (waybill_ids and cpp_id):
            custom_error_messages.append("请检查各项内容是否填写规范！")
            return _failed()
        try:
            cpp_obj = CargoPricePayment.objects.get(id=cpp_id)
        except CargoPricePayment.DoesNotExist:
            custom_error_messages.append("该转账单不存在！")
            return _failed()
        form = forms.CargoPricePaymentForm(request.POST, instance=cpp_obj)
        if not form.is_valid():
            return _failed()
        if not _check_before_edit(cpp_obj):
            return HttpResponseForbidden()
        # 对提交的费用运单列表再次进行检查
        if not all([
            # 不能存在除"客户签收"状态之外的运单
            not Waybill.objects.filter(id__in=waybill_ids).exclude(status=Waybill.Statuses.SignedFor).exists(),
            # 不能存在"已存在于其他转账单中"的运单
            not Waybill.objects.filter(id__in=waybill_ids).filter(
                    Q(cargo_price_payment__isnull=False) & ~Q(cargo_price_payment_id=cpp_id)
                ).exists(),
        ]):
            custom_error_messages.append("请检查各项内容是否填写规范！")
            return _failed()
        try:
            with transaction.atomic():
                form.save()
                cpp_obj.waybill_set.set(Waybill.objects.filter(id__in=waybill_ids))
        except Exception as e:
            got_request_exception.send(None, request=request)
            custom_error_messages.append(str(e))
            custom_error_messages.append("转账单保存失败！请联系管理员！")
            return _failed()
        messages.success(request, "修改成功")
        return redirect("wuliu:manage_cargo_price_payment")

@login_required(raise_404=True)
def edit_cargo_price_payment_js(request):
    return render(
        request,
        'wuliu/_js/edit_cargo_price_payment.js.html',
        content_type="text/javascript",
    )

@login_required()
@check_permission("manage_cargo_price_payment__search")
def manage_cargo_price_payment(request):
    if request.method == "GET":
        form = forms.CargoPricePaymentSearchForm.init_from_request(request)
        return render(
            request,
            "wuliu/finance/cargo_price_payment/manage_cargo_price_payment.html",
            {
                "form": form,
                "cargo_price_payment_list": [],
            }
        )
    if request.method == "POST":
        form = forms.CargoPricePaymentSearchForm.init_from_request(request, data=request.POST)
        department_payment_list = []
        if form.is_valid():
            try:
                department_payment_list = form.gen_cargo_price_payment_list_to_queryset()
            except:
                if settings.DEBUG:
                    raise
        return render(
            request,
            "wuliu/finance/cargo_price_payment/manage_cargo_price_payment.html",
            {
                "form": form,
                "cargo_price_payment_list": department_payment_list,
            }
        )

@login_required(raise_404=True)
def manage_cargo_price_payment_js(request):
    return render(
        request,
        'wuliu/_js/manage_cargo_price_payment.js.html',
        content_type="text/javascript",
    )

@login_required(raise_404=True)
@check_permission("customer_score_log__search")
def manage_customer_score(request):
    if request.method == "GET":
        return render(
            request,
            "wuliu/finance/customer_score_log/manage_customer_score.html",
            {
                "form": forms.CustomerScoreLogSearchForm(),
                "customer_score_logs": [],
            }
        )
    if request.method == "POST":
        form = forms.CustomerScoreLogSearchForm(request.POST)
        customer_score_logs = CustomerScoreLog.objects.none()
        if form.is_valid():
            try:
                customer_score_logs = form.gen_log_list_to_queryset()
            except:
                if settings.DEBUG:
                    raise
        return render(
            request,
            "wuliu/finance/customer_score_log/manage_customer_score.html",
            {
                "form": form,
                "customer_score_logs": customer_score_logs,
            }
        )

@login_required(raise_404=True)
@check_permission("customer_score_log__add")
def add_customer_score_log(request):
    if request.method == "GET":
        return render(
            request,
            "wuliu/finance/customer_score_log/add_customer_score_log.html",
            {
                "form": forms.CustomerScoreLogFrom(),
            }
        )
    if request.method == "POST":
        form = forms.CustomerScoreLogFrom.init_from_request(request, data=request.POST)
        custom_error_messages = []

        def _failed():
            messages.error(
                request,
                mark_safe("<br>".join([
                    "提交失败！",
                    *["%s: %s" % (k, "".join(v)) for k, v in form.errors.items()],
                    *custom_error_messages,
                ])),
            )
            return redirect("wuliu:add_customer_score_log")

        if not form.is_valid():
            return _failed()
        try:
            form.save()
        except Exception as e:
            got_request_exception.send(None, request=request)
            custom_error_messages.append(str(e))
            return _failed()
        messages.success(request, "客户积分变更成功")
        return redirect("wuliu:manage_customer_score")

class ReportTableSrcWaybill(WaybillSearchView):
    form_class = forms.ReportTableSrcWaybill
    template_name = "wuliu/report_table/src_waybill.html"
    need_permissions = ("report_table_src_waybill", )

class ReportTableStockWaybill(WaybillSearchView):
    form_class = forms.ReportTableStockWaybill
    template_name = "wuliu/report_table/stock_waybill.html"
    need_permissions = ("report_table_stock_waybill", )

class ReportTableDstWaybill(WaybillSearchView):
    form_class = forms.ReportTableDstWaybill
    template_name = "wuliu/report_table/dst_waybill.html"
    need_permissions = ("report_table_dst_waybill", )

class ReportTableDstStockWaybill(WaybillSearchView):
    form_class = forms.ReportTableDstStockWaybill
    template_name = "wuliu/report_table/dst_stock_waybill.html"
    need_permissions = ("report_table_dst_stock_waybill", )

class ReportTableSignForWaybill(WaybillSearchView):
    form_class = forms.ReportTableSignForWaybill
    template_name = "wuliu/report_table/sign_for_waybill.html"
    need_permissions = ("report_table_sign_for_waybill", )
