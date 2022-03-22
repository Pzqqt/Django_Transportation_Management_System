import logging

from django.apps import AppConfig
from django.core.signals import got_request_exception
from django.dispatch import receiver

from utils.common import traceback_and_detail_log


class WuliuConfig(AppConfig):
    name = "wuliu"
    verbose_name = "物流运输管理系统"
    default_auto_field = "django.db.models.AutoField"

_request_exception_logger = logging.getLogger(__name__)

@receiver(got_request_exception)
def _(sender, request, **kwargs):
    traceback_and_detail_log(request, _request_exception_logger)
