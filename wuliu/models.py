import datetime as datetime_
import math
from collections import defaultdict

from django.db import models, transaction
from django.db.models import Count, Sum, Q, F
from django.db.models.query import QuerySet
from django.utils import timezone
from django.core.validators import MinValueValidator, validate_slug
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.utils.functional import cached_property
from django.utils.html import strip_tags

from utils.common import UnescapedDjangoJSONEncoder, ExpireLruCache


def _validate_handling_fee_ratio(value):
    if value <= 0 or value > 1:
        raise ValidationError("手续费比例必须大于0且小于等于1！")

def _validate_customer_score_ratio(value):
    if value <= 0 or value > 1:
        raise ValidationError("客户积分比例必须大于0且小于等于1！")

# 全局设置
class Settings(models.Model):
    company_name = models.CharField("公司名称", max_length=32)
    handling_fee_ratio = models.FloatField("手续费比例(向上取整)", validators=[_validate_handling_fee_ratio, ])
    customer_score_ratio = models.FloatField("客户积分比例(向上取整)", validators=[_validate_customer_score_ratio, ])

    class Meta:
        verbose_name = "全局配置"
        verbose_name_plural = verbose_name
        constraints = [
            models.CheckConstraint(
                check=Q(handling_fee_ratio__gt=0, handling_fee_ratio__lte=1), name="check_handling_fee_ratio"
            ),
            models.CheckConstraint(
                check=Q(customer_score_ratio__gt=0, customer_score_ratio__lte=1), name="check_customer_score_ratio"
            ),
        ]

    def save(self, *args, **kwargs):
        cls = self.__class__
        if cls.objects.exists():
            if cls.objects.count() != 1 or self.id != cls.objects.get().id:
                raise Exception("只能有一个配置！")
        self.full_clean()
        super().save(*args, **kwargs)

def _get_global_settings() -> Settings:
    """ 返回全局配置Settings对象, 如果没有, 则自动创建一个 """
    try:
        return Settings.objects.first()
    except Settings.DoesNotExist:
        settings_ = Settings(
            company_name="PP物流",
            handling_fee_ratio=0.002,  # 千分之2
            customer_score_ratio=1,  # 每1元运费折算1分
        )
        settings_.save()
        return settings_

# 权限组
class PermissionGroup(models.Model):
    name = models.CharField("权限组名", max_length=64, unique=True, validators=[validate_slug, ])
    print_name = models.CharField("打印权限组名", max_length=64)
    father = models.ForeignKey(
        "self", verbose_name="父权限组", on_delete=models.PROTECT, null=True, blank=True,
    )  # 如果为Null 则为最顶级的权限组

    class Meta:
        verbose_name = "权限组"
        verbose_name_plural = verbose_name

    def save(self, *args, **kwargs):
        cls = self.__class__
        if (self.father is None
                and cls.objects.filter(father__isnull=True).exists()
                and self.id != cls.objects.get(father__isnull=True).id):
            raise Exception("只能有一个根权限组！")
        self.full_clean()
        super().save(*args, **kwargs)

    @ExpireLruCache(expire_time=timezone.timedelta(minutes=5))
    def tree_str(self) -> str:
        return "%s %s" % (
            self.father.tree_str() + " -" if self.father is not None else "",
            self.print_name,
        )

    tree_str.short_description = "层级"

    def __str__(self):
        return "%s (%s)" % (self.print_name, self.name)

# 权限
class Permission(models.Model):
    name = models.CharField("权限名", max_length=64, unique=True, validators=[validate_slug, ])
    print_name = models.CharField("打印权限名", max_length=64)
    father = models.ForeignKey(PermissionGroup, verbose_name="父权限组", on_delete=models.PROTECT)

    class Meta:
        verbose_name = "权限"
        verbose_name_plural = verbose_name

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @ExpireLruCache(expire_time=timezone.timedelta(minutes=5))
    def tree_str(self) -> str:
        return "%s - %s" % (self.father.tree_str(), self.print_name)

    tree_str.short_description = "层级"

    def __str__(self):
        return "%s (%s)" % (self.print_name, self.name)

# 部门
class Department(models.Model):
    name = models.CharField("部门名称", max_length=32, unique=True)
    father_department = models.ForeignKey(
        "self", verbose_name="上级部门", on_delete=models.SET_NULL, null=True, blank=True,
    )  # 如果为Null 则为最顶级的部门
    unit_price = models.FloatField("单价 (元/千克/立方米)", validators=[MinValueValidator(0), ], db_index=True)
    enable_src = models.BooleanField("允许收货", default=False)
    enable_dst = models.BooleanField("允许到货", default=False)
    enable_cargo_price = models.BooleanField("允许代收", default=False)
    is_branch_group = models.BooleanField("分支机构分组", default=False)

    class _DepartmentManager(models.Manager):
        """ 新增了两个自定义的查询方法 """

        def filter_is_branch(self):
            """ 如果该部门的单价字段不为零就认为是分支机构 """
            return self.filter(unit_price__gt=0)
            # return self.filter(father_department__is_branch_group=True)

        def filter_is_goods_yard(self):
            return self.filter(name="货场")

    objects = _DepartmentManager()

    class Meta:
        # 设置模型对象的直观的名称
        verbose_name = "部门"
        # verbose_name的复数形式
        verbose_name_plural = verbose_name
        # 指定默认排序方式
        # ordering = ("name", )

    def clean(self):
        if self.father_department is not None:
            if self.father_department.is_branch_group and self.is_branch_group:
                raise ValidationError("分支机构分组下的部门不能作为分支机构分组")
            if not self.father_department.is_branch_group and self.unit_price != 0:
                raise ValidationError("不属于分支机构分组的部门单价必须为0")
            if self.father_department.is_branch_group and self.unit_price == 0:
                raise ValidationError("属于分支机构分组的部门必须录入单价")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def is_goods_yard(self) -> bool:
        """ 是否是货场 """
        return self.name == "货场"

    def is_branch(self) -> bool:
        """ 是否属于分支机构 """
        # return self.father_department.is_branch_group
        # 使用一种更简单的方法, 如果该部门的单价字段不为零就认为是分支机构, 避免多次查询数据库
        return bool(self.unit_price)

    is_branch.admin_order_field = "father_department"
    is_branch.boolean = True
    is_branch.short_description = "分支机构"

    @staticmethod
    @ExpireLruCache(expire_time=timezone.timedelta(minutes=5))
    def get_name_by_id(dept_id):
        """ 通过部门id获取部门名称 """
        # 由于User的__str__方法需要频繁获取部门名称, 因此通过添加一个额外的类方法并用缓存装饰器装饰以减少开销
        return Department.objects.get(id=dept_id).name

    @ExpireLruCache(expire_time=timezone.timedelta(minutes=5))
    def tree_str(self) -> str:
        return "%s %s" % (
            self.father_department.tree_str()+" -" if self.father_department is not None else "",
            self.name,
        )

    tree_str.short_description = "组织架构"

    def __str__(self):
        return str(self.name)

# 用户
class User(models.Model):
    name = models.CharField("用户名", max_length=32, unique=True)
    password = models.CharField("密码", max_length=128)
    enabled = models.BooleanField("启用", default=True, db_index=True)
    administrator = models.BooleanField("管理员", default=False)
    department = models.ForeignKey(Department, verbose_name="所属部门", on_delete=models.PROTECT)
    create_time = models.DateTimeField("创建时间", auto_now_add=True)
    permission = models.ManyToManyField(Permission, verbose_name="权限")

    class Meta:
        verbose_name = "用户"
        verbose_name_plural = verbose_name

    class Types(models.TextChoices):
        Administrator = "管理员"
        Company = "公司"
        Branch = "分支机构"
        GoodsYard = "货场"

    def save(self, *args, **kwargs):
        cls = self.__class__
        if not self.administrator:
            if not cls.objects.exists() or not cls.objects.filter(administrator=True).exclude(pk=self.pk).exists():
                raise ValueError("至少要有一个管理员用户！")
        self.full_clean()
        super().save(*args, **kwargs)

    def has_perm(self, perm_name: str) -> bool:
        """ 检查用户是否具有perm_name权限 """
        return self.permission.filter(name=perm_name).exists()

    @cached_property
    def get_type(self) -> Types:
        """ 获取用户类型 """
        if self.administrator:
            return self.Types.Administrator
        is_goods_yard = self.department.is_goods_yard()
        is_branch = self.department.is_branch()
        assert not (is_goods_yard and is_branch), "货场部门不应该录入单价"
        if is_goods_yard:
            return self.Types.GoodsYard
        if is_branch:
            return self.Types.Branch
        return self.Types.Company

    def __str__(self):
        return "%s (%s)%s" % (
            self.name, Department.get_name_by_id(self.department_id), " (管理员)" if self.administrator else ""
        )

# 客户
class Customer(models.Model):
    name = models.CharField("姓名", max_length=32, db_index=True)
    phone = models.CharField("电话号码", max_length=16, unique=True)
    enabled = models.BooleanField("启用", default=True, db_index=True)
    bank_name = models.CharField("银行名称", max_length=32)
    bank_number = models.CharField("银行卡号", max_length=32)
    credential_num = models.CharField("身份证号", max_length=32)
    address = models.CharField("详细地址", max_length=64, blank=True)
    is_vip = models.BooleanField("会员", default=False, db_index=True)
    score = models.PositiveIntegerField("积分", default=0)
    create_time = models.DateTimeField("创建时间", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "客户"
        verbose_name_plural = verbose_name
        # 默认按名字排序, 并把会员放在前面
        # (暂时禁用默认排序, 因为数据库优化起来比较困难)
        # ordering = ("-is_vip", "name")

    def __str__(self):
        return "%s (%s)%s" % (self.name, self.phone, " (会员客户)" if self.is_vip else "")

# 代收款转账单
class CargoPricePayment(models.Model):
    """
    注意: 通过update方法更新status字段为"已支付"时, 必须手动更新waybill_set中的冗余字段: cargo_price_status
    """

    class Statuses(models.IntegerChoices):
        Created = 0, "已创建"
        Submitted = 1, "已提交"
        Reviewed = 2, "已审核"
        Paid = 3, "已支付"
        Rejected = 4, "已驳回"

    create_time = models.DateTimeField("创建时间", auto_now_add=True, db_index=True)
    settle_accounts_time = models.DateTimeField("结算时间", null=True)
    create_user = models.ForeignKey(User, verbose_name="创建人", on_delete=models.PROTECT)
    payee_name = models.CharField("收款人姓名", max_length=32)
    payee_phone = models.CharField("收款人电话号码", max_length=16)
    payee_bank_name = models.CharField("收款人银行名称", max_length=32)
    payee_bank_number = models.CharField("收款人银行卡号", max_length=32)
    payee_credential_num = models.CharField("收款人身份证号", max_length=32)
    remark = models.CharField("备注", max_length=256, blank=True)
    reject_reason = models.CharField("驳回原因", max_length=256, blank=True)
    status = models.SmallIntegerField(
        "状态", choices=Statuses.choices, default=Statuses.Created.value, db_index=True
    )

    class Meta:
        verbose_name = "代收款转账单"
        verbose_name_plural = verbose_name

    def gen_total_fee(self) -> dict:
        """ 计算各项应付款金额 """
        total_cargo_price = self.waybill_set.only("cargo_price").aggregate(_=models.Sum("cargo_price"))["_"]
        total_deduction_fee = self.waybill_set.only("fee").filter(
                fee_type=Waybill.FeeTypes.Deduction
            ).aggregate(_=models.Sum("fee"))["_"]
        total_cargo_handling_fee = self.waybill_set.only(
                "cargo_handling_fee"
            ).aggregate(_=models.Sum("cargo_handling_fee"))["_"]
        return {
            "cargo_price": total_cargo_price or 0,
            "deduction_fee": total_deduction_fee or 0,
            "cargo_handling_fee": total_cargo_handling_fee or 0,
        }

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # 调用save方法时, 如果转账单已支付, 则为关联的运单更新冗余字段: cargo_price_status
        if self.status == self.Statuses.Paid:
            with transaction.atomic():
                self.waybill_set.update(cargo_price_status=Waybill.CargoPriceStatuses.Paid)

    @cached_property
    def get_full_id(self) -> str:
        return "".join([
            self.create_time.strftime("%Y%m%d"),
            str(self.create_user_id).zfill(3),
            str(self.id).zfill(3)[-3:],
        ])

# 运单
class Waybill(models.Model):
    """
    注意: 通过update方法更新cargo_price或cargo_price_payment字段时, 必须根据情况手动更新冗余字段: cargo_price_status
    """

    class Statuses(models.IntegerChoices):
        Created = 0, "已开票"
        Loaded = 1, "已配载"
        Departed = 2, "已发车"
        GoodsYardArrived = 3, "货场入库"
        GoodsYardLoaded = 4, "货场配载"
        GoodsYardDeparted = 5, "货场发车"
        Arrived = 6, "到站待提"
        SignedFor = 7, "客户签收"
        Returned = 8, "已退货"
        Dropped = 9, "已作废"

    class FeeTypes(models.IntegerChoices):
        SignFor = 0, "提付"
        Now = 1, "现付"
        Deduction = 2, "扣付"

    class CargoPriceStatuses(models.IntegerChoices):
        No = 0, "无代收"
        NotPaid = 1, "未支付"
        Paid = 2, "已支付"

    create_time = models.DateTimeField("创建日期", auto_now_add=True, db_index=True)
    # arrival_time和sign_for_time本质上也是一个冗余字段, 因为这个时间可以从WaybillRouting中查询
    arrival_time = models.DateTimeField("到货日期", null=True, blank=True, db_index=True)
    sign_for_time = models.DateTimeField("提货日期", null=True, blank=True, db_index=True)
    src_department = models.ForeignKey(
        Department, verbose_name="发货部门", on_delete=models.PROTECT, related_name="wb_src_department"
    )
    dst_department = models.ForeignKey(
        Department, verbose_name="到达部门", on_delete=models.PROTECT, related_name="wb_dst_department"
    )

    src_customer = models.ForeignKey(
        Customer, verbose_name="发货客户", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="src_customer"
    )
    src_customer_name = models.CharField("发货人", max_length=32)
    src_customer_phone = models.CharField("发货人电话", max_length=16)
    src_customer_credential_num = models.CharField("发货人身份证号码", max_length=32, blank=True)
    src_customer_address = models.CharField("发货人地址", max_length=64, blank=True)
    dst_customer = models.ForeignKey(
        Customer, verbose_name="收货客户", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="dst_customer"
    )
    dst_customer_name = models.CharField("收货人", max_length=32)
    dst_customer_phone = models.CharField("收货人电话", max_length=16)
    dst_customer_credential_num = models.CharField("收货人身份证号码", max_length=32, blank=True)
    dst_customer_address = models.CharField("收货人地址", max_length=64, blank=True)

    cargo_name = models.CharField("货物名称", max_length=16)
    cargo_num = models.PositiveIntegerField("件数", validators=[MinValueValidator(1), ])
    cargo_volume = models.FloatField("体积", validators=[MinValueValidator(0.01), ])
    cargo_weight = models.FloatField("重量", validators=[MinValueValidator(0.1), ])
    cargo_price = models.PositiveIntegerField("货款", default=0)
    cargo_handling_fee = models.PositiveIntegerField("手续费", default=0)

    fee = models.PositiveIntegerField("运费", validators=[MinValueValidator(1), ])
    fee_type = models.SmallIntegerField("运费类型", choices=FeeTypes.choices, db_index=True)

    customer_remark = models.CharField("客户备注", max_length=64, blank=True)
    company_remark = models.CharField("公司备注", max_length=64, blank=True)

    sign_for_customer_name = models.CharField("签收人", max_length=32, blank=True)
    sign_for_customer_credential_num = models.CharField("签收人身份证号码", max_length=32, blank=True)

    status = models.SmallIntegerField(
        "状态", default=Statuses.Created.value, choices=Statuses.choices, db_index=True
    )
    drop_reason = models.CharField("作废原因", max_length=64, blank=True)
    # return_waybill不为NULL时则说明该运单是另外一个运单(return_waybill)的退货运单
    return_waybill = models.OneToOneField(
        "self", verbose_name="退货原运单", on_delete=models.SET_NULL, null=True, blank=True
    )
    # 一个运单只能对应一个代收款转账单, 一个代收款转账单可以包含多个运单
    cargo_price_payment = models.ForeignKey(
        CargoPricePayment, verbose_name="代收款转账单", on_delete=models.SET_NULL, null=True, blank=True
    )
    # 反范式设计: 冗余字段
    cargo_price_status = models.SmallIntegerField("代收款状态", choices=CargoPriceStatuses.choices, db_index=True)

    class Meta:
        verbose_name = "运单"
        verbose_name_plural = verbose_name

    def clean(self):
        custom_validators = [
            # 发货/到货部门必须拥有发货/到货权限
            (self.src_department.enable_src, "发货部门无发货权限"),
            (self.dst_department.enable_dst, "到货部门无到货权限"),
            # 发货部门和到货部门不能一致
            (self.src_department != self.dst_department, "发货部门和到货部门不能一致"),
            # 若填写了发货/收货客户 则该客户必须启用
            (self.src_customer.enabled if self.src_customer else True, "发货客户未启用"),
            (self.dst_customer.enabled if self.dst_customer else True, "收货客户未启用"),
            # 若使用扣付, 则到货部门必须允许代收, 且运费不得高于货款
            (
                (
                    self.dst_department.enable_cargo_price
                    if self.fee_type == self.FeeTypes.Deduction else True
                ),
                "到货部门不允许代收"
            ),
            (
                (
                    self.fee <= self.cargo_price
                    if self.fee_type == self.FeeTypes.Deduction else True
                ),
                "扣付运费不得高于货款"
            ),
            (self.return_waybill != self, "不能将运单作为自己的退货运单"),
        ]
        for validator, error_text in custom_validators:
            if not validator:
                raise ValidationError(error_text)

    def save(self, *args, **kwargs):
        # 调用save方法时更新冗余字段: cargo_price_status
        if self.cargo_price:
            if self.cargo_price_payment and (self.cargo_price_payment.status == CargoPricePayment.Statuses.Paid):
                self.cargo_price_status = self.CargoPriceStatuses.Paid
            else:
                self.cargo_price_status = self.CargoPriceStatuses.NotPaid
        else:
            self.cargo_price_status = self.CargoPriceStatuses.No
        self.full_clean()
        super().save(*args, **kwargs)

    @cached_property
    def get_full_id(self) -> str:
        if self.return_waybill:
            return "YF" + str(self.return_waybill_id).zfill(8)
        return str(self.id).zfill(8)

    get_full_id.admin_order_field = "pk"
    get_full_id.short_description = "运单编号"

    def __str__(self):
        return self.get_full_id

# 货车（卡车）
class Truck(models.Model):
    number_plate = models.CharField("车牌号", max_length=8, unique=True)
    driver_name = models.CharField("驾驶员姓名", max_length=32)
    driver_phone = models.CharField("驾驶员电话号码", max_length=16)
    enabled = models.BooleanField("启用", default=True, db_index=True)
    create_time = models.DateTimeField("创建时间", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "车辆"
        verbose_name_plural = verbose_name

    def __str__(self):
        return str(self.number_plate)

# 车次
class TransportOut(models.Model):

    class Statuses(models.IntegerChoices):
        Ready = 0, "货物配载"
        OnTheWay = 1, "车次在途"
        Arrived = 2, "车次到车"

    truck = models.ForeignKey(Truck, verbose_name="车辆", on_delete=models.PROTECT)
    driver_name = models.CharField("驾驶员姓名", max_length=32)
    driver_phone = models.CharField("驾驶员电话号码", max_length=16)
    create_time = models.DateTimeField("创建时间", auto_now_add=True, db_index=True)
    start_time = models.DateTimeField("发车时间", null=True, db_index=True)
    end_time = models.DateTimeField("到达时间", null=True)
    src_department = models.ForeignKey(
        Department, verbose_name="发车部门", on_delete=models.PROTECT, related_name="tn_src_department"
    )
    dst_department = models.ForeignKey(
        Department, verbose_name="到达部门", on_delete=models.PROTECT, related_name="tn_dst_department"
    )
    waybills = models.ManyToManyField(Waybill, verbose_name="配载运单")
    status = models.SmallIntegerField(
        "状态", choices=Statuses.choices, default=Statuses.Ready.value, db_index=True
    )

    class Meta:
        verbose_name = "车次"
        verbose_name_plural = verbose_name

    def __str__(self):
        return "%s (%s)" % (self.get_full_id, self.get_status_display())

    @cached_property
    def get_full_id(self) -> str:
        return "SN" + str(self.id).zfill(8)

    get_full_id.admin_order_field = "pk"
    get_full_id.short_description = "车次编号"

    def gen_waybills_info(self):
        """ 统计该车次中配载的运单信息(单数, 件数, 总体积, 总重量) """
        return self.waybills.only(*"pk cargo_num cargo_volume cargo_weight".split()).aggregate(
            total_num=Count("pk"),
            total_cargo_num=Sum("cargo_num"),
            total_cargo_volume=Sum("cargo_volume"),
            total_cargo_weight=Sum("cargo_weight"),
        )

# 运单路由
class WaybillRouting(models.Model):
    waybill = models.ForeignKey(Waybill, verbose_name="运单", on_delete=models.PROTECT)
    time = models.DateTimeField("操作时间")
    operation_type = models.SmallIntegerField("操作类型", choices=Waybill.Statuses.choices)
    operation_dept = models.ForeignKey(Department, verbose_name="操作部门", on_delete=models.PROTECT)
    operation_user = models.ForeignKey(User, verbose_name="操作用户", on_delete=models.PROTECT)
    operation_info = models.JSONField("详细内容", encoder=UnescapedDjangoJSONEncoder, default=dict)

    class Meta:
        verbose_name = "运单路由"
        verbose_name_plural = verbose_name

    def __str__(self):
        return "%s (%s)" % (
            self.waybill.get_full_id, self.get_operation_type_display()
        )

    def _template_context(self) -> dict:
        wr_transport_out = None
        wr_return_waybill = None
        if self.operation_type in (Waybill.Statuses.GoodsYardDeparted, Waybill.Statuses.Departed):
            wr_transport_out_id = self.operation_info.get("transport_out_id")
            if wr_transport_out_id:
                wr_transport_out = TransportOut.objects.get(id=wr_transport_out_id)
        elif self.operation_type == Waybill.Statuses.Returned:
            wr_return_waybill_id = self.operation_info.get("return_waybill_id")
            if wr_return_waybill_id:
                wr_return_waybill = Waybill.objects.get(id=wr_return_waybill_id)
        return {
            "wr": self,
            "WB_STATUSES": Waybill.Statuses,
            "transport_out": wr_transport_out,
            "return_waybill": wr_return_waybill,
        }

    def gen_print_operation_info(self) -> str:
        """ 生成运单路由的详细文本内容 """
        operation_info_string = render_to_string(
            "wuliu/_inclusions/_waybill_routing_operation_info.html",
            self._template_context(),
        )
        return operation_info_string.strip()

    def gen_print_operation_info_text(self) -> str:
        """ 生成运单路由的详细文本内容(移除html标签) """
        return strip_tags(self.gen_print_operation_info())

# 部门回款单
class DepartmentPayment(models.Model):

    class Statuses(models.IntegerChoices):
        Created = 0, "已创建"
        Reviewed = 1, "已审核"
        Paid = 2, "已回款"
        Settled = 3, "已结算"

    create_time = models.DateTimeField("创建时间", auto_now_add=True)
    payment_date = models.DateField("应回款日期")
    settle_accounts_time = models.DateTimeField("结算时间", null=True)
    waybills = models.ManyToManyField(Waybill, verbose_name="运单")
    src_department = models.ForeignKey(
        Department, verbose_name="回款部门",
        on_delete=models.PROTECT, related_name="pm_src_department",
    )
    dst_department = models.ForeignKey(
        Department, verbose_name="收款部门",
        on_delete=models.PROTECT, related_name="pm_dst_department",
    )
    status = models.SmallIntegerField(
        "状态", choices=Statuses.choices, default=Statuses.Created.value, db_index=True
    )
    src_remark = models.CharField("回款部门备注", max_length=256, blank=True)
    dst_remark = models.CharField("收款部门备注", max_length=256, blank=True)

    class Meta:
        verbose_name = "部门回款单"
        verbose_name_plural = verbose_name

    @staticmethod
    def static_gen_waybills(src_department: Department, payment_date: datetime_.date) -> set:
        """ 根据应回款部门和应回款日期, 生成关联的运单id集合 """

        def _date_to_datetime_start(_date):
            return timezone.make_aware(timezone.datetime.combine(_date, datetime_.time()))

        def _date_to_datetime_end(_date):
            return timezone.make_aware(timezone.datetime.combine(_date, datetime_.time(23, 59, 59)))

        transport_out_src = TransportOut.objects.filter(
            src_department=src_department,
            status__gte=TransportOut.Statuses.OnTheWay,
            start_time__gte=_date_to_datetime_start(payment_date),
            start_time__lte=_date_to_datetime_end(payment_date),
        )
        # 当天发车的运单(现付运费)
        waybills_src = [
            wb_id
            for to_obj in transport_out_src
            for wb_id in to_obj.waybills.filter(fee_type=Waybill.FeeTypes.Now).values_list("id", flat=True)
        ]
        # 当天签收的运单
        waybills_dst = list(
            Waybill.objects.filter(
                dst_department=src_department,
                status=Waybill.Statuses.SignedFor,
                sign_for_time__gte=_date_to_datetime_start(payment_date),
                sign_for_time__lte=_date_to_datetime_end(payment_date),
            ).values_list("id", flat=True)
        )
        return set(waybills_src + waybills_dst)

    def set_waybills_auto(self):
        """ 设置关联运单 """
        with transaction.atomic():
            self.waybills.set(self.static_gen_waybills(self.src_department, self.payment_date))

    @staticmethod
    def static_gen_total_fee(waybills, src_department: Department) -> dict:
        """ 计算各项应回款金额 """
        if not isinstance(waybills, QuerySet):
            waybills = Waybill.objects.filter(id__in=waybills)
        # 发货运单: 现付运费
        fee_now = waybills.only("fee").filter(
                src_department=src_department, fee_type=Waybill.FeeTypes.Now,
            ).aggregate(_=models.Sum("fee"))["_"]
        # 签收运单: 提付运费
        fee_sign_for = waybills.only("fee").filter(
                dst_department=src_department, fee_type=Waybill.FeeTypes.SignFor,
            ).aggregate(_=models.Sum("fee"))["_"]
        # 签收运单: 代收货款
        cargo_price = waybills.only("cargo_price").filter(
                dst_department=src_department
            ).aggregate(_=models.Sum("cargo_price"))["_"]
        return {
            "fee_now": fee_now or 0,
            "fee_sign_for": fee_sign_for or 0,
            "cargo_price": cargo_price or 0,
        }

    def gen_total_fee(self) -> dict:
        """ 计算各项应回款金额 """
        return self.static_gen_total_fee(self.waybills.all(), self.src_department)

    def gen_customer_score_change(self) -> list:
        """ 计算客户积分变动 """
        customer_score_ratio = _get_global_settings().customer_score_ratio
        customer_score_change = []
        filtered_waybills_info = self.waybills.filter(src_customer__is_vip=True).values(
            "src_department_id", "dst_department_id", "src_customer_id", "id", "fee", "fee_type",
        )
        for waybill_info in filtered_waybills_info:
            fee_type = waybill_info["fee_type"]
            # 现付运费: 应回款部门应该与运单的发货部门一致
            # 提付或扣付运费: 应回款部门应该与运单的到达部门一致
            if any([
                fee_type == Waybill.FeeTypes.Now and self.src_department_id == waybill_info["src_department_id"],
                fee_type in (Waybill.FeeTypes.Deduction, Waybill.FeeTypes.SignFor) and (
                    self.src_department_id == waybill_info["dst_department_id"]
                ),
            ]):
                customer_score_change.append({
                    "customer_id": waybill_info["src_customer_id"],
                    "waybill_id": waybill_info["id"],
                    "add_score": math.ceil(waybill_info["fee"] * customer_score_ratio),
                })
        return customer_score_change

    def update_customer_score_change(self):
        """ 更新客户积分变动 """
        customer_score_changes = self.gen_customer_score_change()
        # 计算客户的总计增加积分
        customer_add_score_total = defaultdict(int)
        for change in customer_score_changes:
            customer_add_score_total[change["customer_id"]] += change["add_score"]
        with transaction.atomic():
            CustomerScoreLog.objects.bulk_create([
                CustomerScoreLog(
                    customer_id=change["customer_id"],
                    inc_or_dec=True,
                    score=change["add_score"],
                    remark="运单结算",
                    waybill=Waybill.objects.get(id=change["waybill_id"])
                )
                for change in customer_score_changes
            ])
            for customer_id, add_score_total in customer_add_score_total.items():
                customer = Customer.objects.get(id=customer_id)
                customer.score = F("score") + add_score_total
                customer.save(update_fields=["score", ])

    @cached_property
    def get_full_id(self) -> str:
        return "".join([
            self.payment_date.strftime("%Y%m%d"),
            str(self.src_department_id).zfill(3),
            str(self.dst_department_id).zfill(3),
        ])

    def save(self, *args, **kwargs):
        # self.full_clean()
        super().save(*args, **kwargs)
        if self.status == DepartmentPayment.Statuses.Settled:
            self.update_customer_score_change()

    def __str__(self):
        return "%s (%s -> %s)" % (
            self.get_full_id, self.src_department.name, self.dst_department.name
        )

# 客户积分记录
class CustomerScoreLog(models.Model):
    create_time = models.DateTimeField("变动时间", auto_now_add=True, db_index=True)
    customer = models.ForeignKey(Customer, verbose_name="客户", on_delete=models.PROTECT)
    inc_or_dec = models.BooleanField("增或减")
    score = models.PositiveIntegerField("变动积分", validators=[MinValueValidator(1), ])
    remark = models.CharField("变更原因", max_length=256)
    # 每个运单应该只对应一个客户积分增加记录
    waybill = models.OneToOneField(Waybill, verbose_name="关联运单", on_delete=models.PROTECT, null=True)
    user = models.ForeignKey(User, verbose_name="操作人", on_delete=models.PROTECT, null=True)

    class Meta:
        verbose_name = "客户积分记录"
        verbose_name_plural = verbose_name
        # ordering = ["-id", ]
        constraints = [
            models.CheckConstraint(check=Q(score__gte=1), name="check_change_score"),
        ]
