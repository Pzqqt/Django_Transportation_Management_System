from django.contrib import admin

from . import models


class PermissionGroupAdmin(admin.ModelAdmin):
    list_display = ["print_name", "name", "tree_str"]
    list_filter = ["father", ]
    fields = ["name", "print_name", "father"]

class PermissionAdmin(admin.ModelAdmin):
    list_display = ["print_name", "name", "tree_str"]
    list_filter = ["father", ]
    fields = ["name", "print_name", "father"]

class DepartmentAdmin(admin.ModelAdmin):
    list_display = [
        "name", "tree_str", "unit_price", "is_branch_group", "is_branch",
        "enable_src", "enable_dst", "enable_cargo_price"
    ]
    list_filter = ["enable_src", "enable_dst", "enable_cargo_price"]

class UserAdmin(admin.ModelAdmin):
    readonly_fields = ["create_time"]
    list_display = ["name", "department", "create_time", "administrator", "enabled"]
    list_filter = ["enabled"]

class CustomerAdmin(admin.ModelAdmin):
    readonly_fields = ["score", "create_time"]
    list_display = ["name", "phone", "bank_name", "score", "is_vip", "enabled"]
    list_filter = ["is_vip", "enabled", "create_time"]

class WaybillAdmin(admin.ModelAdmin):
    fieldsets = [
        ("基本信息", {"fields": (
            "src_department", "dst_department", "status", "cargo_price_status",
            "create_time", "arrival_time", "sign_for_time",
        )}),
        ("发货人信息", {"fields": (
            "src_customer", "src_customer_name", "src_customer_phone",
            "src_customer_credential_num", "src_customer_address",
        )}),
        ("收货人信息", {"fields": (
            "dst_customer", "dst_customer_name", "dst_customer_phone",
            "dst_customer_credential_num", "dst_customer_address",
        )}),
        ("货物信息", {"fields": (
            "cargo_name", "cargo_num",
            "cargo_volume", "cargo_weight", "cargo_price", "cargo_handling_fee",
        )}),
        ("运费", {"fields": ("fee", "fee_type")}),
        ("其他", {"fields": (
            "customer_remark", "company_remark", "drop_reason",
        )}),
    ]
    readonly_fields = [
        "src_department", "dst_department",  "cargo_price_status",
        "create_time", "arrival_time", "sign_for_time",
        "src_customer", "dst_customer",
        "cargo_num", "status", "drop_reason"
    ]
    list_display = [
        "get_full_id", "src_department", "dst_department", "fee", "fee_type",
        "create_time", "status", "cargo_price_status"
    ]
    list_filter = ["create_time"]

class TruckAdmin(admin.ModelAdmin):
    readonly_fields = ["create_time"]
    list_display = ["number_plate", "driver_name", "driver_phone", "create_time", "enabled"]
    list_filter = ["enabled", "create_time"]

class TransportOutAdmin(admin.ModelAdmin):
    readonly_fields = [
        "create_time", "start_time", "end_time",
        "src_department", "dst_department", "status", "waybills"
    ]
    list_display = [
        "get_full_id", "truck", "driver_name", "src_department", "dst_department",
        "start_time", "end_time", "status"
    ]
    list_filter = ["create_time", "start_time", "end_time"]

admin.site.register(models.Settings)
admin.site.register(models.Department, DepartmentAdmin)
admin.site.register(models.User, UserAdmin)
admin.site.register(models.Customer, CustomerAdmin)
admin.site.register(models.Waybill, WaybillAdmin)
# admin.site.register(models.WaybillRouting)
admin.site.register(models.Truck, TruckAdmin)
admin.site.register(models.TransportOut, TransportOutAdmin)
admin.site.register(models.Permission, PermissionAdmin)
admin.site.register(models.PermissionGroup, PermissionGroupAdmin)
