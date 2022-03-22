import json

from django.conf import settings
from django.http import HttpResponseBadRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .export_excel import gen_workbook


@require_POST
@csrf_exempt
def export_excel(request):
    """ 导出excel表格
    前端必须用表单"显式"提交(可以用隐藏表单), 不能用ajax, 否则无法触发下载
    """
    table_title = request.POST.get("table_title")
    table_header = request.POST.get("table_header")
    table_rows = request.POST.get("table_rows")
    if not (table_title and table_header and table_rows):
        return HttpResponseBadRequest()
    try:
        table_header = json.loads(table_header)
        table_rows = json.loads(table_rows)
    except json.decoder.JSONDecodeError:
        if settings.DEBUG:
            raise
        return HttpResponseBadRequest()
    # 不能用FileResponse, 也不能用StreamingHttpResponse, 很奇怪
    response = HttpResponse(gen_workbook(table_title, table_header, table_rows))
    response["Content-Type"] = "application/octet-stream"
    # 此处必须编码为ansi, 否则filename有中文的话会乱码
    response["Content-Disposition"] = ('attachment; filename="%s.xlsx"' % table_title).encode("ansi")
    return response
