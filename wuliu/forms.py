import datetime as datetime_
import math

from django import forms
from django.utils import timezone
from django.db import transaction
from django.db.models import Q, F, QuerySet

from .models import (
    User, Waybill, WaybillRouting, Department, Customer, TransportOut, Truck,
    DepartmentPayment, CargoPricePayment, Permission, CustomerScoreLog,
)
from .common import get_global_settings, get_logged_user, is_logged_user_is_goods_yard
from utils.common import SortableModelChoiceField


DATA_MIN = datetime_.date(1970, 1, 1)
DEPARTMENT_GROUP_CHOICES = {
    0: "全部",
    **{index: dept.name for index, dept in enumerate(Department.objects.filter(is_branch_group=True), 1)}
}


def _init_form_fields_class(self_: forms.BaseForm):
    """ 为所有表单项配置样式 """
    for field in self_.fields.values():
        if isinstance(field, (forms.ModelMultipleChoiceField, forms.MultipleChoiceField)):
            # 将多选field设置为multiple-select样式
            field.widget.attrs["class"] = "form-control multiple-select"
        elif isinstance(field, forms.ChoiceField):
            # 将单选field设置为select2样式
            field.widget.attrs["class"] = "form-control select2"
        elif isinstance(field, forms.DateField):
            # 将日期选择field设置为md-date-picker样式
            field.widget.attrs["class"] = "form-control md-date-picker"
        elif isinstance(field, forms.TimeField):
            # 将时间选择field设置为md-time-picker样式
            field.widget.attrs["class"] = "form-control md-time-picker"
        else:
            field.widget.attrs["class"] = "form-control"

def _destroy_model_form_save(self_: forms.ModelForm):
    """ 破坏ModelForm的save方法 """
    def save_(self__, *args, **kwargs):
        raise NotImplemented
    self_.save = save_

class _FormBase(forms.Form):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _init_form_fields_class(self)

    def need_filter(self, field_name: str, cleaned_value_list: list) -> bool:
        """
        对多选field判断是否有必要进行筛选(未选择或者全选的话就没必要进行筛选)
        """
        field = self.fields[field_name]
        assert isinstance(field, (forms.ModelMultipleChoiceField, forms.MultipleChoiceField))
        return len(cleaned_value_list) not in (0, len(field.choices))

class _ModelFormBase(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _init_form_fields_class(self)

class ChangePassword(_FormBase):
    old_password = forms.CharField(label="旧密码", widget=forms.PasswordInput)
    new_password = forms.CharField(label="新密码", widget=forms.PasswordInput)
    new_password_again = forms.CharField(label="再次输入新密码", widget=forms.PasswordInput)

class WaybillForm(_ModelFormBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 不允许发货部门选择空值
        self.fields["src_department"].empty_label = None
        # 到达部门只能选择分支机构, 如果该部门的单价字段不为零就认为是分支机构
        self.fields["dst_department"].queryset = Department.objects.filter_is_branch()
        # 只显示已启用的客户
        self.fields["src_customer"].queryset = Customer.objects.filter(enabled=True)
        self.fields["src_customer"].sort_key = lambda c: (-c.is_vip, c.name)
        self.fields["dst_customer"].queryset = self.fields["src_customer"].queryset
        self.fields["dst_customer"].sort_key = self.fields["src_customer"].sort_key
        # 手续费只读
        self.fields["cargo_handling_fee"].widget.attrs["readonly"] = True
        # 如果不加这两个属性, 则货物体积和重量不能输入小数
        self.fields["cargo_volume"].widget.attrs["step"] = "0.01"
        self.fields["cargo_weight"].widget.attrs["step"] = "0.1"
        # 为某些字段设置最小值
        self.fields["cargo_num"].widget.attrs["min"] = "1"
        self.fields["cargo_volume"].widget.attrs["min"] = "0.01"
        self.fields["cargo_weight"].widget.attrs["min"] = "0.1"
        # 对这些项禁用tab键跳转
        for field_name in [
            "src_department", "src_customer_credential_num", "src_customer_address",
            "dst_customer_credential_num", "dst_customer_address",
            "cargo_handling_fee", "customer_remark", "company_remark"
        ]:
            self.fields[field_name].widget.attrs["tabindex"] = -1

    class Meta:
        model = Waybill
        fields = [
            "src_department", "dst_department",

            "src_customer", "src_customer_name", "src_customer_phone",
            "src_customer_credential_num", "src_customer_address",

            "dst_customer", "dst_customer_name", "dst_customer_phone",
            "dst_customer_credential_num", "dst_customer_address",

            "cargo_name", "cargo_num", "cargo_volume", "cargo_weight",
            "cargo_price", "cargo_handling_fee",

            "fee", "fee_type", "customer_remark", "company_remark"
        ]
        widgets = {
            "customer_remark": forms.Textarea(attrs={"style": "resize: vertical; height: 60px;"}),
            "company_remark": forms.Textarea(attrs={"style": "resize: vertical; height: 60px;"}),
        }
        field_classes = {
            "src_customer": SortableModelChoiceField,
            "dst_customer": SortableModelChoiceField,
        }

    @classmethod
    def init_from_request(cls, request, *args, **kwargs):
        form_obj = cls(*args, **kwargs)
        # 发货部门只能选用户所属部门
        form_obj.fields["src_department"].queryset = Department.objects.filter_is_branch().filter(
            id=request.session["user"]["department_id"], enable_src=True,
        )
        return form_obj

    def add_id_field(self, id_: int, id_full: str):
        """ 新增一个完整运单编号的field, 和一个隐藏的id field """
        self.fields["id_"] = forms.CharField(label="运单号码", max_length=8)
        self.fields["id_"].widget.attrs = {
            "class": "form-control",
            "readonly": True,
            "value": id_full,
        }
        self.fields["id"] = forms.IntegerField()
        self.fields["id"].widget.attrs = {
            "hidden": "hidden",
            "readonly": True,
            "value": id_,
        }

    def check_again(self, request):
        """ 对提交的表单数据进行二次检查 """

        def _gen_handling_fee(cargo_price):
            if cargo_price == 0:
                return 0
            return math.ceil(cargo_price * get_global_settings().handling_fee_ratio)

        form_dic = self.cleaned_data
        logged_user = get_logged_user(request)
        _handling_fee = _gen_handling_fee(form_dic["cargo_price"])
        custom_validators = [
            # 发货部门必须与登录用户所属部门一致
            (
                form_dic["src_department"] == logged_user.department,
                "发货部门(%s)与登录用户所属部门(%s, %s)不一致" % (
                    form_dic["src_department"].name, logged_user.name, logged_user.department.name
                )
            ),
            # 手续费检查
            (form_dic["cargo_handling_fee"] == _handling_fee, "手续费金额与系统后台计算金额(%d)不一致" % _handling_fee),
        ]
        for validator, error_text in custom_validators:
            assert validator, error_text

    def change_to_detail_form(self):
        """ 将表单更改为运单详情页面的表单 """
        assert self.instance.id is None, "若要生成只读表单, 应该在初始化表单时通过data参数初始化数据, 不要使用instance参数"
        assert self.is_bound, "表单尚未绑定, 不能转换为详情页表单"
        change_dic = {
            "src_department": {
                "label": Waybill.src_department.field.verbose_name,
                "value": self.data["src_department"],
            },
            "dst_department": {
                "label": Waybill.dst_department.field.verbose_name,
                "value": self.data["dst_department"],
            },
            "src_customer": {
                "label": Waybill.src_customer.field.verbose_name,
                "value": self.data["src_customer"].name if self.data["src_customer"] else None,
            },
            "dst_customer": {
                "label": Waybill.dst_customer.field.verbose_name,
                "value": self.data["dst_customer"].name if self.data["dst_customer"] else None,
            },
            "fee_type": {
                "label": "支付方式",
                "value": self.data["fee_type"],
            },
            "create_time": {
                "label": Waybill.create_time.field.verbose_name,
                "value": timezone.make_naive(self.data["create_time"]).strftime("%Y-%m-%d %H:%M:%S"),
            },
            "status": {
                "label": "运单状态",
                "value": self.data["status"],
            },
            "cargo_price_status": {
                "label": "代收款状态",
                "value": self.data["cargo_price_status"],
            },
        }
        # 重写/添加这些fields
        # 由于ChoiceField设置为readonly之后仍然可以在前端进行修改, 所以将ChoiceField全部改成CharField
        for name, info in change_dic.items():
            label_ = info["label"]
            value_ = info["value"]
            self.fields[name] = forms.CharField(label=label_)
            self.fields[name].widget.attrs["class"] = "form-control"
            self.data[name] = value_
        # 由于ChoiceField被改成CharField, fee_type的value属性被写入为支付方式的字符串
        # 但是前端计算总价仍然需要引用fee_type的id, 所以将id写入该widget的自定义属性中
        self.fields["fee_type"].widget.attrs["data-fee_type_id"] = self.data["fee_type_id"]
        # 将全部fields设置为只读
        for field in self.fields.values():
            field.widget.attrs["readonly"] = True
        _destroy_model_form_save(self)

class WaybillSearchForm(_FormBase):
    create_date_start = forms.DateField(
        label="开票日期", required=False,
        initial=timezone.make_naive(timezone.now() - timezone.timedelta(days=7)).strftime("%Y-%m-%d"),
    )
    create_time_start = forms.TimeField(label="-", required=False, initial="00:00")
    create_date_end = forms.DateField(
        label="至", required=False,
        initial=timezone.make_naive(timezone.now()).strftime("%Y-%m-%d"),
    )
    create_time_end = forms.TimeField(label="-", required=False, initial="23:59")

    arrival_date_start = forms.DateField(label="到货日期", required=False)
    arrival_date_end = forms.DateField(label="至", required=False)
    sign_for_date_start = forms.DateField(label="签收日期", required=False)
    sign_for_date_end = forms.DateField(label="至", required=False)

    src_department = forms.ModelChoiceField(
        Department.objects.filter_is_branch(), required=False, label="开票部门",
    )
    dst_department = forms.ModelChoiceField(
        Department.objects.filter_is_branch(), required=False, label="到达部门",
    )

    src_department_group = forms.ChoiceField(
        label="-", required=False, choices=DEPARTMENT_GROUP_CHOICES.items(), initial=0,
    )
    dst_department_group = forms.ChoiceField(
        label="-", required=False, choices=DEPARTMENT_GROUP_CHOICES.items(), initial=0,
    )

    src_customer_name = forms.CharField(label="发货人", required=False)
    src_customer_phone = forms.CharField(label="发货人电话", required=False)
    dst_customer_name = forms.CharField(label="收货人", required=False)
    dst_customer_phone = forms.CharField(label="收货人电话", required=False)

    waybill_id = forms.CharField(label="运单号码", required=False, max_length=8)
    waybill_status = forms.MultipleChoiceField(
        label="运单状态", required=False,
        choices=Waybill.Statuses.choices,
        initial=[Waybill.Statuses.Created, ],
    )
    waybill_fee_type = forms.MultipleChoiceField(
        label="结算方式", required=False,
        choices=Waybill.FeeTypes.choices,
        initial=Waybill.FeeTypes.values,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 隐藏"到货日期"和"签收日期"选择区间
        for field_name in "arrival_date_start arrival_date_end sign_for_date_start sign_for_date_end".split():
            self.fields[field_name].widget.attrs["hidden"] = True

    @classmethod
    def init_from_request(cls, request, *args, **kwargs):
        form_obj = cls(*args, **kwargs)
        user = get_logged_user(request)
        user_type = user.get_type
        # 分支机构用户只能管理自己部门的运单
        if user_type == User.Types.Branch:
            form_obj.fields["src_department"].choices = [(user.department_id, user.department.name), ]
            form_obj.fields["src_department_group"].choices = [(0, "全部"), ]
        # 货场用户不限制开票日期的时间范围
        elif user_type == User.Types.GoodsYard:
            form_obj.fields["create_date_start"].initial = None
            form_obj.fields["waybill_status"].initial = [Waybill.Statuses.GoodsYardArrived, ]
        # 公司用户和管理员用户不做限制
        return form_obj

    @staticmethod
    def filter_by_create_time(form_dic: dict, queryset: QuerySet) -> QuerySet:
        create_time_start = form_dic["create_time_start"] or datetime_.time()
        create_time_end = form_dic["create_time_end"] or datetime_.time(23, 59, 59)
        q_obj = Q()
        if form_dic["create_date_start"]:
            q_obj &= Q(create_time__gte=timezone.make_aware(
                timezone.datetime.combine(form_dic["create_date_start"], create_time_start)
            ))
        if form_dic["create_date_end"]:
            q_obj &= Q(create_time__lte=timezone.make_aware(
                timezone.datetime.combine(form_dic["create_date_end"], create_time_end)
            ))
        return queryset.filter(q_obj)

    @staticmethod
    def filter_by_arrival_time(form_dic: dict, queryset: QuerySet) -> QuerySet:
        q_obj = Q()
        if form_dic["arrival_date_start"]:
            q_obj &= Q(arrival_time__gte=timezone.make_aware(
                timezone.datetime.combine(form_dic["arrival_date_start"], datetime_.time())
            ))
        if form_dic["arrival_date_end"]:
            q_obj &= Q(arrival_time__lte=timezone.make_aware(
                timezone.datetime.combine(form_dic["arrival_date_end"], datetime_.time(23, 59, 59))
            ))
        return queryset.filter(q_obj)

    @staticmethod
    def filter_by_sign_for_time(form_dic: dict, queryset: QuerySet) -> QuerySet:
        q_obj = Q()
        if form_dic["sign_for_date_start"]:
            q_obj &= Q(sign_for_time__gte=timezone.make_aware(
                timezone.datetime.combine(form_dic["sign_for_date_start"], datetime_.time())
            ))
        if form_dic["sign_for_date_end"]:
            q_obj &= Q(sign_for_time__lte=timezone.make_aware(
                timezone.datetime.combine(form_dic["sign_for_date_end"], datetime_.time(23, 59, 59))
            ))
        return queryset.filter(q_obj)

    def gen_waybill_list_to_queryset(self) -> QuerySet:
        """ 根据表单内容执行查询操作, 返回一个QuerySet对象 """
        form_dic = self.cleaned_data
        if form_dic["src_department_group"]:
            form_dic["src_department_group"] = int(form_dic["src_department_group"])
        if form_dic["dst_department_group"]:
            form_dic["dst_department_group"] = int(form_dic["dst_department_group"])
        # 查询结果中要显示发货部门和收货部门, 所以在此顺便进行查询, 可以减少查询次数并大幅提升性能
        r_queryset = Waybill.objects.all().select_related("src_department", "dst_department")
        # 按运单编号查询
        if form_dic["waybill_id"]:
            str_waybill_id = str(form_dic["waybill_id"]).upper()
            if str_waybill_id.upper().startswith("YF"):
                str_waybill_id = str_waybill_id[2:]
            try:
                r_queryset = r_queryset.filter(
                    Q(id=int(str_waybill_id)) | Q(return_waybill_id=int(str_waybill_id))
                )
            except ValueError:
                return Waybill.objects.none()
        # 按开票日期区间查询, 如果开始日期和结束日期都没有指定, 则不用筛选
        if form_dic["create_date_start"] or form_dic["create_date_end"]:
            r_queryset = self.filter_by_create_time(form_dic, r_queryset)
        # 按运单状态查询
        if form_dic["waybill_status"] and self.need_filter("waybill_status", form_dic["waybill_status"]):
            r_queryset = r_queryset.filter(status__in=form_dic["waybill_status"])
        # 按结算方式查询
        if form_dic["waybill_fee_type"] and self.need_filter("waybill_fee_type", form_dic["waybill_fee_type"]):
            r_queryset = r_queryset.filter(fee_type__in=form_dic["waybill_fee_type"])
        # 按开票/到达部门查询
        if form_dic["src_department"]:
            # 若指定了某部门, 则直接按该部门筛选
            r_queryset = r_queryset.filter(src_department=form_dic["src_department"])
        elif form_dic["src_department_group"]:
            r_queryset = r_queryset.filter(
                src_department__father_department__name=(
                    DEPARTMENT_GROUP_CHOICES.get(form_dic["src_department_group"])
                )
            )
        if form_dic["dst_department"]:
            r_queryset = r_queryset.filter(dst_department=form_dic["dst_department"])
        elif form_dic["dst_department_group"]:
            r_queryset = r_queryset.filter(
                dst_department__father_department__name=(
                    DEPARTMENT_GROUP_CHOICES.get(form_dic["dst_department_group"])
                )
            )
        # 按发货/收货人姓名/电话查询 (电话优先级高于姓名)
        if form_dic["src_customer_phone"]:
            r_queryset = r_queryset.filter(src_customer_phone=form_dic["src_customer_phone"])
        elif form_dic["src_customer_name"]:
            r_queryset = r_queryset.filter(src_customer_name=form_dic["src_customer_name"])
        if form_dic["dst_customer_phone"]:
            r_queryset = r_queryset.filter(dst_customer_phone=form_dic["dst_customer_phone"])
        elif form_dic["dst_customer_name"]:
            r_queryset = r_queryset.filter(dst_customer_name=form_dic["dst_customer_name"])
        return r_queryset

class WaybillQuickSearchForm(forms.Form):
    search_string = forms.CharField(max_length=32)

    def gen_waybill_list_to_queryset(self) -> QuerySet:
        search_str = self.cleaned_data["search_string"]
        if not search_str:
            return Waybill.objects.none()
        # 查询结果中要显示发货部门和收货部门, 所以在此顺便进行查询, 可以减少查询次数并大幅提升性能
        r_queryset = Waybill.objects.all().select_related("src_department", "dst_department")
        # 假设用户输入的是运单号
        _waybill_id = search_str
        if search_str.upper().startswith("YF"):
            _waybill_id = _waybill_id[2:]
        try:
            r_queryset = r_queryset.filter(Q(id=int(_waybill_id)) | Q(return_waybill_id=int(_waybill_id)))
        except ValueError:
            pass
        else:
            if r_queryset.exists():
                return r_queryset
        # 假设用户输入的是发货人/收货人的姓名/电话号
        return Waybill.objects.filter(
                Q(src_customer_name=search_str) | Q(dst_customer_name=search_str) |
                Q(src_customer_phone=search_str) | Q(dst_customer_phone=search_str)
            )

class SignForSearchForm(WaybillSearchForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in "create_date_start create_time_start create_date_end create_time_end".split():
            self.fields[field_name].widget.attrs["hidden"] = True
        for field_name in "arrival_date_start arrival_date_end sign_for_date_start sign_for_date_end".split():
            self.fields[field_name].widget.attrs["hidden"] = False
        # 开票日期区间设置为空
        self.fields["create_date_start"].initial = None
        self.fields["create_date_end"].initial = None
        # 运单状态只能选择"到站待提"或"客户提货"
        self.fields["waybill_status"].choices = [
            (Waybill.Statuses.Arrived, Waybill.Statuses.Arrived.label),
            (Waybill.Statuses.SignedFor, Waybill.Statuses.SignedFor.label),
        ]
        self.fields["waybill_status"].initial = [Waybill.Statuses.Arrived, ]
        # 初始化到货日期字段
        self.fields["arrival_date_start"].initial = timezone.make_naive(
                timezone.now() - timezone.timedelta(days=7)
            ).strftime("%Y-%m-%d")
        self.fields["arrival_date_end"].initial = timezone.make_naive(timezone.now()).strftime("%Y-%m-%d")

    @classmethod
    def init_from_request(cls, request, *args, **kwargs):
        form_obj = cls(*args, **kwargs)
        user = get_logged_user(request)
        # 开票部门不限制, 对于分支机构和货场, 到达部门只能选择自己部门
        if user.get_type in (User.Types.Branch, User.Types.GoodsYard):
            form_obj.fields["dst_department"].choices = [(user.department_id, user.department.name), ]
            form_obj.fields["dst_department_group"].choices = [(0, "全部"), ]
        return form_obj

    def gen_waybill_list_to_queryset(self) -> QuerySet:
        r_queryset = super().gen_waybill_list_to_queryset()
        form_dic = self.cleaned_data
        # 指定运单状态为"到站待提"/"客户签收"时, 按到货/签收日期查询
        if len(form_dic["waybill_status"]) == 1:
            waybill_status = int(form_dic["waybill_status"][0])
            if waybill_status == Waybill.Statuses.Arrived:
                if form_dic["arrival_date_start"] or form_dic["arrival_date_end"]:
                    r_queryset = self.filter_by_arrival_time(form_dic, r_queryset)
            elif waybill_status == Waybill.Statuses.SignedFor:
                if form_dic["sign_for_date_start"] or form_dic["sign_for_date_end"]:
                    r_queryset = self.filter_by_sign_for_time(form_dic, r_queryset)
        # 指定运单状态为"全部"时, 首先按"到货日期"区间查询, 其次再按"签收日期"区间查询
        else:
            r_queryset = r_queryset.filter(
                status__in=(Waybill.Statuses.Arrived, Waybill.Statuses.SignedFor)
            )
            if form_dic["arrival_date_start"] or form_dic["arrival_date_end"]:
                r_queryset = self.filter_by_arrival_time(form_dic, r_queryset)
            elif form_dic["sign_for_date_start"] or form_dic["sign_for_date_end"]:
                r_queryset = self.filter_by_sign_for_time(form_dic, r_queryset)
        return r_queryset

class TransportOutForm(_ModelFormBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 不允许发车部门选择空值
        self.fields["src_department"].empty_label = None

    class Meta:
        model = TransportOut
        exclude = ["status", "create_time", "start_time", "end_time"]

    @classmethod
    def init_from_request(cls, request, *args, **kwargs):
        form_obj = cls(*args, **kwargs)
        user = get_logged_user(request)
        user_type = user.get_type
        # 对于任何用户, 发车部门只能选择用户所属部门
        form_obj.fields["src_department"].queryset = Department.objects.filter(id=user.department_id)
        # 货场只能发车到分支机构
        if user_type == User.Types.GoodsYard:
            form_obj.fields["dst_department"].queryset = Department.objects.filter_is_branch()
        # 其他任何部门都只能发车到货场
        else:
            form_obj.fields["dst_department"].queryset = Department.objects.filter_is_goods_yard()
        return form_obj

    def add_id_field(self, id_: int, id_full: str):
        """ 新增一个完整车次编号的field, 和一个隐藏的id field """
        self.fields["id_"] = forms.CharField(label="车次编号", max_length=8)
        self.fields["id_"].widget.attrs = {
            "class": "form-control",
            "readonly": True,
            "value": id_full,
        }
        self.fields["id"] = forms.IntegerField()
        self.fields["id"].widget.attrs = {
            "hidden": "hidden",
            "readonly": True,
            "value": id_,
        }

    def check_again(self, request, transport_out_id=None):
        """ 对提交的表单数据进行二次检查 """
        form_dic = self.cleaned_data
        logged_user_is_goods_yard = is_logged_user_is_goods_yard(request)

        # 发车部门必须与登录用户所属部门一致
        assert form_dic["src_department"] == get_logged_user(request).department, "发车部门必须与登录用户所属部门一致"
        # 分支机构只能发车到货场, 货场只能发车到分支机构
        assert (
            form_dic["dst_department"].is_goods_yard() if not logged_user_is_goods_yard else True
        ), "分支机构只能发车到货场"
        assert (
            form_dic["dst_department"].is_branch() if logged_user_is_goods_yard else True
        ), "货场只能发车到分支机构"
        # 对于货场, 只允许有"货场入库"或"货场配载"的运单
        if is_logged_user_is_goods_yard(request):
            assert not form_dic["waybills"].exclude(
                status__in=[Waybill.Statuses.GoodsYardArrived, Waybill.Statuses.GoodsYardLoaded]
            ).exists(), '只允许有"货场入库"或"货场配载"的运单'
        # 否则, 只允许有"已开票 已配载"的运单, 并检查发车部门和开票部门是否一致
        else:
            assert not form_dic["waybills"].filter(
                status__gt=Waybill.Statuses.Loaded
            ).exists(), '只允许有"已开票"或"已配载"的运单'
            assert (
                not form_dic["waybills"].exclude(src_department__id=request.session["user"]["department_id"]).exists()
            ), "存在发车部门和开票部门不一致的运单"
        # 对于已创建并保存的车次, 检查"已配载/货场配载"的运单所配载的车次id与本车次id是否一致
        if transport_out_id:
            assert (
                not form_dic["waybills"].filter(
                    status__in=[Waybill.Statuses.Loaded, Waybill.Statuses.GoodsYardLoaded]
                ).exclude(transportout_id=transport_out_id).exists()
            ), "该车次中存在所属车次不一致的配载状态运单"
        # 对于尚未创建和保存的车次, 禁止存在"已配载/货场配载"的运单
        else:
            assert (
                not form_dic["waybills"].filter(
                    status__in=[Waybill.Statuses.Loaded, Waybill.Statuses.GoodsYardLoaded]
                ).exists()
            ), "新建车次中不允许存在配载状态运单"

    def change_to_detail_form(self):
        """ 将表单更改为运单详情页面的表单 """
        assert self.instance.id is None, "若要生成只读表单, 应该在初始化表单时通过data参数初始化数据, 不要使用instance参数"
        assert self.is_bound, "表单尚未绑定, 不能转换为详情页表单"
        change_dic = {
            "src_department": {
                "label": TransportOut.src_department.field.verbose_name,
                "value": self.data["src_department"],
            },
            "dst_department": {
                "label": TransportOut.dst_department.field.verbose_name,
                "value": self.data["dst_department"],
            },
            "truck": {
                "label": "车牌号",
                "value": self.data["truck"],
            },
            "status": {
                "label": "车次状态",
                "value": self.data["status"],
            },
            "create_time": {
                "label": TransportOut.create_time.field.verbose_name,
                "value": timezone.make_naive(self.data["create_time"]).strftime("%Y-%m-%d %H:%M:%S"),
            },
            "start_time": {
                "label": TransportOut.start_time.field.verbose_name,
                "value": (
                    timezone.make_naive(self.data["start_time"]).strftime("%Y-%m-%d %H:%M:%S")
                    if self.data["start_time"] else ""
                ),
            },
            "end_time": {
                "label": TransportOut.end_time.field.verbose_name,
                "value": (
                    timezone.make_naive(self.data["end_time"]).strftime("%Y-%m-%d %H:%M:%S")
                    if self.data["end_time"] else ""
                ),
            },
        }
        # 重写这些fields
        for name, info in change_dic.items():
            label_ = info["label"]
            value_ = info["value"]
            self.fields[name] = forms.CharField(label=label_)
            self.fields[name].widget.attrs["class"] = "form-control"
            self.data[name] = value_
        # 将全部fields设置为只读
        for field in self.fields.values():
            field.widget.attrs["readonly"] = True
        _destroy_model_form_save(self)

class TransportOutSearchForm(_FormBase):
    transport_out_id = forms.CharField(label="车次编号", required=False, max_length=8)
    truck_number_plate = forms.MultipleChoiceField(
        label="车牌号", required=False,
        choices=((truck.id, truck.number_plate) for truck in Truck.objects.all()),
    )
    driver_name = forms.CharField(label="驾驶员姓名", required=False)
    status = forms.MultipleChoiceField(
        label="车次状态", required=False,
        choices=TransportOut.Statuses.choices, initial=[TransportOut.Statuses.Ready, ],
    )
    create_date_start = forms.DateField(
        label="创建日期", required=False,
        initial=timezone.make_naive(timezone.now() - timezone.timedelta(days=7)).strftime("%Y-%m-%d"),
    )
    create_date_end = forms.DateField(
        label="至", required=False,
        initial=timezone.make_naive(timezone.now()).strftime("%Y-%m-%d"),
    )
    start_date_start = forms.DateField(
        label="发车日期", required=False,
        initial=timezone.make_naive(timezone.now() - timezone.timedelta(days=7)).strftime("%Y-%m-%d"),
    )
    start_date_end = forms.DateField(
        label="至", required=False,
        initial=timezone.make_naive(timezone.now()).strftime("%Y-%m-%d"),
    )
    src_department = forms.ModelChoiceField(
        Department.objects.filter_is_branch() | Department.objects.filter_is_goods_yard(),
        required=False, label="发车部门",
    )
    dst_department = forms.ModelChoiceField(
        Department.objects.filter_is_branch() | Department.objects.filter_is_goods_yard(),
        required=False, label="到达部门",
    )

    def __init__(self, *args, search_mode="src", **kwargs):
        super().__init__(*args, **kwargs)
        assert search_mode in ("src", "dst"), 'search_mode参数只能为"src"或"dst"'
        self._search_mode = search_mode

    @classmethod
    def init_from_request(cls, request, *args, search_mode="src", **kwargs):
        form_obj = cls(*args, search_mode=search_mode, **kwargs)
        user = get_logged_user(request)
        if user.get_type in (User.Types.Branch, User.Types.GoodsYard):
            # 分支机构和货场用户只能管理自己部门的发车车次
            if form_obj._search_mode == "src":
                form_obj.fields["src_department"].choices = [
                    (user.department_id, user.department.name),
                ]
            # 分支机构和货场用户只能管理到达自己部门的车次
            elif form_obj._search_mode == "dst":
                form_obj.fields["dst_department"].choices = [
                    (user.department_id, user.department.name),
                ]
        if form_obj._search_mode == "dst":
            form_obj.fields["status"].initial = [TransportOut.Statuses.OnTheWay, ]
        return form_obj

    @staticmethod
    def filter_by_create_date(form_dic: dict, queryset: QuerySet) -> QuerySet:
        q_obj = Q()
        if form_dic["create_date_start"]:
            q_obj &= Q(create_time__gte=timezone.make_aware(
                timezone.datetime.combine(form_dic["create_date_start"], datetime_.time())
            ))
        if form_dic["create_date_end"]:
            q_obj &= Q(create_time__lte=timezone.make_aware(
                timezone.datetime.combine(form_dic["create_date_end"], datetime_.time(23, 59, 59))
            ))
        return queryset.filter(q_obj)

    @staticmethod
    def filter_by_start_date(form_dic: dict, queryset: QuerySet) -> QuerySet:
        q_obj = Q()
        if form_dic["start_date_start"]:
            q_obj &= Q(start_time__gte=timezone.make_aware(
                timezone.datetime.combine(form_dic["start_date_start"], datetime_.time())
            ))
        if form_dic["start_date_end"]:
            q_obj &= Q(start_time__lte=timezone.make_aware(
                timezone.datetime.combine(form_dic["start_date_end"], datetime_.time(23, 59, 59))
            ))
        return queryset.filter(q_obj)

    def gen_transport_out_list_to_queryset(self) -> QuerySet:
        """ 根据表单内容执行查询操作, 返回一个QuerySet对象 """
        form_dic = self.cleaned_data
        r_queryset = TransportOut.objects.all().select_related("truck", "src_department", "dst_department")
        # 指定车次编号的话, 直接返回对应的一个车次
        if form_dic["transport_out_id"]:
            if str(form_dic["transport_out_id"]).upper().startswith("SN"):
                transport_out_id = str(form_dic["transport_out_id"])[2:]
            else:
                transport_out_id = form_dic["transport_out_id"]
            try:
                r_queryset = r_queryset.filter(id=int(transport_out_id))
            except ValueError:
                return TransportOut.objects.none()
        # 按创建/发车日期筛选
        if self._search_mode == "src":
            if form_dic["create_date_start"] or form_dic["create_date_end"]:
                r_queryset = self.filter_by_create_date(form_dic, r_queryset)
        else:
            if form_dic["start_date_start"] or form_dic["start_date_end"]:
                r_queryset = self.filter_by_start_date(form_dic, r_queryset)
        # 按发车/到达部门查询
        if form_dic["src_department"]:
            r_queryset = r_queryset.filter(src_department=form_dic["src_department"])
        if form_dic["dst_department"]:
            r_queryset = r_queryset.filter(dst_department=form_dic["dst_department"])
        # 按车牌号/驾驶员姓名查询
        if form_dic["truck_number_plate"]:
            r_queryset = r_queryset.filter(truck_id__in=form_dic["truck_number_plate"])
        if form_dic["driver_name"]:
            r_queryset = r_queryset.filter(driver_name=form_dic["driver_name"])
        # 按车次状态查询
        if form_dic["status"] and self.need_filter("status", form_dic["status"]):
            r_queryset = r_queryset.filter(status__in=form_dic["status"])
        for transport_out_obj in r_queryset:
            transport_out_waybills_info = transport_out_obj.gen_waybills_info()
            for k, v in transport_out_waybills_info.items():
                setattr(transport_out_obj, k, v)
        return r_queryset

class DepartmentPaymentDetailForm(_ModelFormBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        change_dic = {
            "id_": {
                "label": "账单编号",
                "value": self.data["id_"],
            },
            "status": {
                "label": DepartmentPayment.status.field.verbose_name,
                "value": self.data["status"],
            },
            "src_department": {
                "label": DepartmentPayment.src_department.field.verbose_name,
                "value": self.data["src_department"],
            },
            "dst_department": {
                "label": DepartmentPayment.dst_department.field.verbose_name,
                "value": self.data["dst_department"],
            },
            "create_time": {
                "label": DepartmentPayment.create_time.field.verbose_name,
                "value": timezone.make_naive(self.data["create_time"]).strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
        for name, info in change_dic.items():
            label_ = info["label"]
            value_ = info["value"]
            self.fields[name] = forms.CharField(label=label_)
            self.fields[name].widget.attrs["class"] = "form-control"
            self.data[name] = value_
        # 将全部fields设置为只读
        for field in self.fields.values():
            if isinstance(field, forms.DateField):
                field.widget.attrs["class"] = "form-control"
            field.widget.attrs["readonly"] = True
        _destroy_model_form_save(self)

    class Meta:
        model = DepartmentPayment
        exclude = ["waybills"]
        widgets = {
            "src_remark": forms.Textarea(attrs={"style": "resize: vertical; height: 60px;"}),
            "dst_remark": forms.Textarea(attrs={"style": "resize: vertical; height: 60px;"}),
        }

class DepartmentPaymentAddForm(_FormBase):
    src_department = forms.ModelMultipleChoiceField(
        Department.objects.filter_is_branch(), required=False, label="回款部门",
    )
    src_department_group = forms.ChoiceField(
        label="-", choices=tuple(DEPARTMENT_GROUP_CHOICES.items()), initial=0,
    )
    dst_department = forms.ModelChoiceField(Department.objects.all(), label="收款部门")
    payment_date = forms.DateField(
        label="应回款日期",
        initial=timezone.make_naive(timezone.now() - timezone.timedelta(days=1)).strftime("%Y-%m-%d"),
    )
    dst_remark = forms.CharField(
        label="收款部门备注", required=False,
        widget=forms.Textarea(attrs={"style": "resize: vertical; height: 60px;"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 不允许收款部门选择空值
        self.fields["dst_department"].empty_label = None
        # 应回款日期最大值为昨天
        self.fields["payment_date"].widget.attrs["data-maxdate"] = self.fields["payment_date"].initial
        self.fields["src_department"].widget.attrs["data-placeholder"] = "请选择..."

    @classmethod
    def init_from_request(cls, request, *args, **kwargs):
        form_obj = cls(*args, **kwargs)
        # 收款部门只能选当前部门
        form_obj.fields["dst_department"].queryset = Department.objects.filter(
            id=request.session["user"]["department_id"]
        )
        return form_obj

    def save_(self):
        form_dic = self.cleaned_data
        if form_dic["payment_date"] > datetime_.date.today() - timezone.timedelta(days=1):
            raise Exception("应回款日期的最大值为昨天！")
        if form_dic["src_department"]:
            src_depts = form_dic["src_department"]
        else:
            if form_dic["src_department_group"] != 0:
                src_depts = Department.objects.filter(
                    father_department__name=DEPARTMENT_GROUP_CHOICES[form_dic["src_department_group"]]
                )
            else:
                src_depts = Department.objects.all()
        existed_dp = DepartmentPayment.objects.filter(
            src_department__in=src_depts, payment_date=form_dic["payment_date"]
        )
        if existed_dp.exists():
            raise Exception("部门 %s，在%s已有回款单，不允许再次添加！" % (
                "、".join(['"%s"' % dp_obj.src_department.name for dp_obj in existed_dp]),
                form_dic["payment_date"],
            ))
        with transaction.atomic():
            # 不使用bulk_create, 因为DepartmentPayment中存在多对多关系字段
            for dept in src_depts:
                new_dp_obj = DepartmentPayment(
                    src_department=dept,
                    dst_department=form_dic["dst_department"],
                    payment_date=form_dic["payment_date"],
                    dst_remark=form_dic["dst_remark"],
                )
                new_dp_obj.save()
                new_dp_obj.set_waybills_auto()

class DepartmentPaymentSearchForm(_FormBase):
    src_department = forms.ModelChoiceField(
        Department.objects.filter_is_branch(), required=False, label="回款部门",
    )
    src_department_group = forms.ChoiceField(
        label="-", required=False, choices=DEPARTMENT_GROUP_CHOICES.items(), initial=0,
    )
    payment_date_start = forms.DateField(
        label="应回款日期", required=False,
        initial=timezone.make_naive(timezone.now() - timezone.timedelta(days=1)).strftime("%Y-%m-%d"),
    )
    payment_date_end = forms.DateField(
        label="至", required=False,
        initial=timezone.make_naive(timezone.now() - timezone.timedelta(days=1)).strftime("%Y-%m-%d"),
    )
    status = forms.MultipleChoiceField(
        label="状态", required=False,
        choices=DepartmentPayment.Statuses.choices,
        initial=DepartmentPayment.Statuses.values,
    )

    @classmethod
    def init_from_request(cls, request, *args, **kwargs):
        form_obj = cls(*args, **kwargs)
        user = get_logged_user(request)
        # 分支机构用户只能管理自己部门的转账单
        if user.get_type == User.Types.Branch:
            form_obj.fields["src_department"].choices = [(user.department_id, user.department.name), ]
            form_obj.fields["src_department_group"].choices = [(0, "全部"), ]
            form_obj.fields["status"].initial = [
                DepartmentPayment.Statuses.Created, DepartmentPayment.Statuses.Reviewed,
            ]
        return form_obj

    def gen_department_payment_list_to_queryset(self) -> QuerySet:
        form_dic = self.cleaned_data
        if form_dic["src_department_group"]:
            form_dic["src_department_group"] = int(form_dic["src_department_group"])
        r_queryset = DepartmentPayment.objects.all().select_related("src_department", "dst_department")
        # 按应回款日期区间查询, 如果开始日期和结束日期都没有指定, 则不用筛选
        if form_dic["payment_date_start"] or form_dic["payment_date_end"]:
            payment_date_start = form_dic["payment_date_start"] or DATA_MIN
            payment_date_end = form_dic["payment_date_end"] or datetime_.date.today()
            r_queryset = r_queryset.filter(
                payment_date__gte=payment_date_start, payment_date__lte=payment_date_end,
            )
        # 按回款部门查询
        if form_dic["src_department"]:
            # 若指定了某部门, 则直接按该部门筛选
            r_queryset = r_queryset.filter(src_department=form_dic["src_department"])
        elif form_dic["src_department_group"]:
            r_queryset = r_queryset.filter(
                src_department__father_department__name=(
                    DEPARTMENT_GROUP_CHOICES.get(form_dic["src_department_group"])
                )
            )
        # 按状态查询
        if form_dic["status"] and self.need_filter("status", form_dic["status"]):
            r_queryset = r_queryset.filter(status__in=form_dic["status"])
        return r_queryset

class CargoPricePaymentForm(_ModelFormBase):

    customer = forms.ModelChoiceField(
        Customer.objects.filter(enabled=True), required=False, label="选择客户",
    )

    class Meta:
        model = CargoPricePayment
        exclude = [
            "create_time", "settle_accounts_time", "create_user", "status", "reject_reason"
        ]

    def add_id_field(self, id_: int, id_full: str):
        """ 新增一个完整账单号码的field, 和一个隐藏的id field """
        self.fields["id_"] = forms.CharField(label="账单号码", max_length=8)
        self.fields["id_"].widget.attrs = {
            "class": "form-control",
            "readonly": True,
            "value": id_full,
        }
        self.fields["id"] = forms.IntegerField()
        self.fields["id"].widget.attrs = {
            "hidden": "hidden",
            "readonly": True,
            "value": id_,
        }

    def change_to_detail_form(self):
        """ 将表单更改为详情页面的表单 """
        del self.fields["customer"]
        change_dic = {
            "create_time": {
                "label": CargoPricePayment.create_time.field.verbose_name,
                "value": timezone.make_naive(self.data["create_time"]).strftime("%Y-%m-%d %H:%M:%S"),
            },
            "settle_accounts_time": {
                "label": CargoPricePayment.settle_accounts_time.field.verbose_name,
                "value": (
                    timezone.make_naive(self.data["settle_accounts_time"]).strftime("%Y-%m-%d %H:%M:%S")
                    if self.data["settle_accounts_time"] else ""
                ),
            },
            "status": {
                "label": CargoPricePayment.status.field.verbose_name,
                "value": self.data["status"],
            },
            "reject_reason": {
                "label": CargoPricePayment.reject_reason.field.verbose_name,
                "value": self.data["reject_reason"],
            },
        }
        # 重写/添加这些fields
        # 由于ChoiceField设置为readonly之后仍然可以在前端进行修改, 所以将ChoiceField全部改成CharField
        for name, info in change_dic.items():
            label_ = info["label"]
            value_ = info["value"]
            self.fields[name] = forms.CharField(label=label_)
            self.fields[name].widget.attrs["class"] = "form-control"
            self.data[name] = value_
        for field in self.fields.values():
            field.widget.attrs["readonly"] = True
        _destroy_model_form_save(self)

class CargoPricePaymentSearchForm(_FormBase):
    create_user = forms.ModelChoiceField(User.objects.all(), required=False, label="创建人")
    create_department = forms.ModelChoiceField(Department.objects.all(), required=False, label="创建部门")
    payee_name = forms.CharField(label="收款人", required=False)
    status = forms.MultipleChoiceField(
        label="状态", required=False,
        choices=CargoPricePayment.Statuses.choices,
        initial=CargoPricePayment.Statuses.values,
    )
    create_date_start = forms.DateField(
        label="创建日期", required=False,
        initial=timezone.make_naive(timezone.now() - timezone.timedelta(days=7)).strftime("%Y-%m-%d"),
    )
    create_date_end = forms.DateField(
        label="至", required=False,
        initial=timezone.make_naive(timezone.now()).strftime("%Y-%m-%d"),
    )
    settle_accounts_date_start = forms.DateField(label="支付日期", required=False)
    settle_accounts_date_end = forms.DateField(label="至", required=False)

    @classmethod
    def init_from_request(cls, request, *args, **kwargs):
        form_obj = cls(*args, **kwargs)
        user = get_logged_user(request)
        # 分支机构和货场用户只能查询自己创建的转账单(虽然货场用户不应该有创建转账单的权限...)
        if user.get_type >= User.Types.Branch:
            form_obj.fields["create_user"].queryset = User.objects.filter(id=user.id)
            form_obj.fields["create_department"].queryset = Department.objects.none()
        return form_obj

    def gen_cargo_price_payment_list_to_queryset(self) -> QuerySet:
        form_dic = self.cleaned_data
        r_queryset = CargoPricePayment.objects.all().select_related("create_user")
        # 按创建日期/结算日期区间查询, 如果开始日期和结束日期都没有指定, 则不用筛选
        if form_dic["create_date_start"] or form_dic["create_date_end"]:
            create_date_start = form_dic["create_date_start"] or DATA_MIN
            create_date_end = form_dic["create_date_end"] or datetime_.date.today()
            r_queryset = r_queryset.filter(
                create_time__gte=timezone.make_aware(
                    timezone.datetime.combine(create_date_start, datetime_.time())
                ),
                create_time__lte=timezone.make_aware(
                    timezone.datetime.combine(create_date_end, datetime_.time(23, 59, 59))
                ),
            )
        elif form_dic["settle_accounts_start"] or form_dic["settle_accounts_end"]:
            settle_accounts_date_start = form_dic["settle_accounts_date_start"] or DATA_MIN
            settle_accounts_date_end = form_dic["settle_accounts_date_end"] or datetime_.date.today()
            r_queryset = r_queryset.filter(
                settle_accounts_time__gte=timezone.make_aware(
                    timezone.datetime.combine(settle_accounts_date_start, datetime_.time())
                ),
                settle_accounts_time__lte=timezone.make_aware(
                    timezone.datetime.combine(settle_accounts_date_end, datetime_.time(23, 59, 59))
                ),
            )
        # 按创建人查询
        if form_dic["create_user"]:
            r_queryset = r_queryset.filter(create_user=form_dic["create_user"])
        # 按创建部门查询
        elif form_dic["create_department"]:
            r_queryset = r_queryset.filter(create_user__department=form_dic["create_department"])
        # 按状态查询
        if form_dic["status"] and self.need_filter("status", form_dic["status"]):
            r_queryset = r_queryset.filter(status__in=form_dic["status"])
        # 按收款人姓名查询
        if form_dic["payee_name"]:
            r_queryset = r_queryset.filter(payee_name=form_dic["payee_name"])
        return r_queryset

class CustomerScoreLogFrom(_ModelFormBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._logged_in_user = None
        # 只能选择会员客户, 且必须为启用状态
        self.fields["customer"].queryset = Customer.objects.filter(enabled=True, is_vip=True)

    @classmethod
    def init_from_request(cls, request, *args, **kwargs):
        form_obj = cls(*args, **kwargs)
        form_obj._logged_in_user = get_logged_user(request)
        return form_obj

    class Meta:
        model = CustomerScoreLog
        exclude = ["create_time", "waybill", "user"]
        widgets = {
            "remark": forms.Textarea(attrs={"style": "resize: vertical; height: 60px;"}),
            "inc_or_dec": forms.Select(choices=((1, "增加"), (0, "扣减"))),
        }

    def save(self, *args, **kwargs):
        assert self._logged_in_user is not None, (
            "应该从%s.init_from_request方法初始化表单, 以携带当前登录的用户信息." % self.__class__.__name__
        )
        assert self._logged_in_user.has_perm("customer_score_log__add"), "你没有变更积分的权限."
        self.instance.user = self._logged_in_user
        customer = self.instance.customer
        assert customer.is_vip, '客户"%s"不是会员客户.' % customer.name
        assert customer.enabled, '会员客户"%s"没有启用.' % customer.name
        if not self.instance.inc_or_dec:
            assert customer.score >= self.instance.score, '客户"%s"剩余积分(%s)不足.' % (customer.name, customer.score)
        with transaction.atomic():
            instance = super().save(*args, **kwargs)
            if self.instance.inc_or_dec:
                customer.score = F("score") + self.instance.score
            else:
                customer.score = F("score") - self.instance.score
            customer.save()
            return instance

class CustomerScoreLogSearchForm(_FormBase):
    customer_name = forms.CharField(label="客户姓名", required=False)
    customer_phone = forms.CharField(label="客户电话", required=False)
    create_date_start = forms.DateField(
        label="积分变更日期", required=False,
        initial=timezone.make_naive(timezone.now() - timezone.timedelta(days=7)).strftime("%Y-%m-%d"),
    )
    create_date_end = forms.DateField(
        label="至", required=False,
        initial=timezone.make_naive(timezone.now()).strftime("%Y-%m-%d"),
    )

    def gen_log_list_to_queryset(self) -> QuerySet:
        """ 根据表单内容执行查询操作, 返回一个QuerySet对象 """
        form_dic = self.cleaned_data
        r_queryset = CustomerScoreLog.objects.select_related(
                "customer", "user", "waybill"
            ).filter(customer__is_vip=True)
        # 按积分变更日期区间查询
        if form_dic["create_date_start"] or form_dic["create_date_end"]:
            r_queryset = TransportOutSearchForm.filter_by_create_date(form_dic, r_queryset)
        # 按客户姓名/电话查询 (电话优先级高于姓名)
        if form_dic["customer_phone"]:
            r_queryset = r_queryset.filter(customer__phone=form_dic["customer_phone"])
        elif form_dic["customer_name"]:
            r_queryset = r_queryset.filter(customer__name=form_dic["customer_name"])
        # 返回结果倒序排列(新的在前面)
        return r_queryset.order_by("-id")

class ReportTableSrcWaybill(WaybillSearchForm):
    cargo_price_status = forms.MultipleChoiceField(
        label="代收货款状态", required=False,
        choices=[(0, "无代收"), (1, "未支付"), (2, "已支付")],
        initial=[0, 1, 2],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["waybill_status"].initial = []

    def gen_waybill_list_to_queryset(self) -> QuerySet:
        r_queryset = super().gen_waybill_list_to_queryset()
        form_dic = self.cleaned_data
        form_dic_cargo_price_status = set(form_dic["cargo_price_status"])
        if self.need_filter("cargo_price_status", form_dic["cargo_price_status"]):
            if form_dic_cargo_price_status == {"0"}:
                # 无代收
                r_queryset = r_queryset.filter(cargo_price=0)
            elif form_dic_cargo_price_status == {"1", "2"}:
                # 有代收
                r_queryset = r_queryset.exclude(cargo_price=0)
            else:
                q_obj = Q()
                if "0" in form_dic_cargo_price_status:
                    # 无代收
                    q_obj |= Q(cargo_price=0)
                if "1" in form_dic_cargo_price_status:
                    # 未支付(无代收款转账单, 或代收款转账单状态不为"已支付")
                    q_obj |= (
                        Q(cargo_price__gt=0, cargo_price_payment__isnull=True) |
                        (Q(cargo_price__gt=0) & ~Q(cargo_price_payment__status=CargoPricePayment.Statuses.Paid))
                    )
                if "2" in form_dic_cargo_price_status:
                    # 已支付(代收款转账单状态为"已支付")
                    q_obj |= Q(cargo_price__gt=0, cargo_price_payment__status=CargoPricePayment.Statuses.Paid)
                r_queryset = r_queryset.filter(q_obj)
        return r_queryset

class ReportTableStockWaybill(WaybillSearchForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["waybill_status"].initial = [
            Waybill.Statuses.Created, Waybill.Statuses.Loaded, Waybill.Statuses.Departed
        ]

    def gen_waybill_list_to_queryset(self):
        r_queryset = super().gen_waybill_list_to_queryset()
        r_queryset = r_queryset.only(
            *"id create_time src_department dst_department fee status arrival_time".split()
        )
        for wb_obj in r_queryset:
            waybill_routing = wb_obj.waybillrouting_set.all()
            for key, operation_type in (
                ("departed", Waybill.Statuses.Departed),
                ("goods_yard_arrived", Waybill.Statuses.GoodsYardArrived),
                ("goods_yard_departed", Waybill.Statuses.GoodsYardDeparted)
            ):
                key += "_time"
                try:
                    setattr(wb_obj, key, waybill_routing.get(operation_type=operation_type).time)
                except WaybillRouting.DoesNotExist:
                    setattr(wb_obj, key, None)
        return r_queryset

class ReportTableDstWaybill(SignForSearchForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["waybill_status"].initial = [Waybill.Statuses.Arrived, Waybill.Statuses.SignedFor]

class ReportTableDstStockWaybill(WaybillSearchForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 显示"到货日期"和"签收日期"选择区间
        for field_name in "arrival_date_start arrival_date_end".split():
            self.fields[field_name].widget.attrs["hidden"] = False
        # "运单状态"字段默认值为"到站待提", 移除前端样式并隐藏
        self.fields["waybill_status"].initial = [Waybill.Statuses.Arrived, ]
        self.fields["waybill_status"].widget.attrs["class"] = ""
        self.fields["waybill_status"].widget.attrs["hidden"] = True
        # 初始化到货日期字段
        self.fields["arrival_date_start"].initial = timezone.make_naive(
                timezone.now() - timezone.timedelta(days=7)
            ).strftime("%Y-%m-%d")
        self.fields["arrival_date_end"].initial = timezone.make_naive(timezone.now()).strftime("%Y-%m-%d")

    @classmethod
    def init_from_request(cls, request, *args, **kwargs):
        form_obj = cls(*args, **kwargs)
        user = get_logged_user(request)
        user_type = user.get_type
        # 分支机构用户只能管理自己部门的运单
        if user_type == User.Types.Branch:
            form_obj.fields["dst_department"].choices = [(user.department_id, user.department.name), ]
            form_obj.fields["dst_department_group"].choices = [(0, "全部"), ]
        return form_obj

    def gen_waybill_list_to_queryset(self) -> QuerySet:
        r_queryset = super().gen_waybill_list_to_queryset()
        form_dic = self.cleaned_data
        if form_dic["arrival_date_start"] or form_dic["arrival_date_end"]:
            r_queryset = self.filter_by_arrival_time(form_dic, r_queryset)
        timezone_now = timezone.now()
        for wb_obj in r_queryset:
            wb_obj.stay_days = (timezone_now - wb_obj.arrival_time).days
        return r_queryset

class ReportTableSignForWaybill(SignForSearchForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # "运单状态"字段默认值为"客户签收", 移除前端样式并隐藏
        self.fields["waybill_status"].initial = [Waybill.Statuses.SignedFor, ]
        self.fields["waybill_status"].widget.attrs["class"] = ""
        self.fields["waybill_status"].widget.attrs["hidden"] = True
        # 初始化到货日期字段
        self.fields["sign_for_date_start"].initial = timezone.make_naive(
                timezone.now() - timezone.timedelta(days=7)
            ).strftime("%Y-%m-%d")
        self.fields["sign_for_date_end"].initial = timezone.make_naive(timezone.now()).strftime("%Y-%m-%d")

class ManageUsers(_FormBase):
    user = forms.ModelChoiceField(
        User.objects.all(), required=False, label="用户",
    )
    reset_password = forms.CharField(label="重置密码（不需要则留空）", widget=forms.PasswordInput, required=False)
    reset_password_again = forms.CharField(label="再次输入密码", widget=forms.PasswordInput, required=False)
    enabled = forms.BooleanField(label="用户状态", required=False, initial=False)
    administrator = forms.BooleanField(label="管理员", required=False, initial=False)
    department = forms.ChoiceField(
        label="所属部门",
        choices=((None, "---------"), *((dept.id, dept.tree_str()) for dept in Department.objects.all())),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["enabled"].widget.attrs |= {
            "data-on-text": "启用",
            "data-off-text": "禁用",
            "data-bootstrap-switch": "",
        }
        self.fields["administrator"].widget.attrs |= {
            "data-on-text": "属于",
            "data-off-text": "不属于",
            "data-bootstrap-switch": "",
        }

class UserForm(_ModelFormBase):
    password_again = forms.CharField(label="再次输入密码", widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password"].widget.input_type = "password"
        self.fields["enabled"].widget.attrs |= {
            "data-on-text": "启用",
            "data-off-text": "禁用",
            "checked": "",
            "data-bootstrap-switch": "",
        }
        self.fields["administrator"].widget.attrs |= {
            "data-on-text": "属于",
            "data-off-text": "不属于",
            "data-bootstrap-switch": "",
        }

    class Meta:
        model = User
        fields = ["name", "password", "enabled", "administrator", "department"]

class ManageUserPermission(_FormBase):
    user = forms.ModelChoiceField(User.objects.all(), required=False, label="用户")
    permission = forms.ModelMultipleChoiceField(Permission.objects.all(), label="权限", required=False)
    permission_source_user = forms.ModelChoiceField(User.objects.all(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["permission"].widget.attrs["hidden"] = True
        self.fields["permission_source_user"].widget.attrs["hidden"] = True

class BatchEditUserPermission(ManageUserPermission):
    user = forms.ModelMultipleChoiceField(User.objects.all(), label="用户")
    grant_or_deny = forms.BooleanField(required=False, initial=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["grant_or_deny"].widget.attrs |= {
            "data-on-text": "授予",
            "data-off-text": "拒绝",
            "data-bootstrap-switch": "",
        }
