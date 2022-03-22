from functools import partial
from itertools import chain
from collections import UserList
import logging
import traceback

from django import forms
from django.db.models import Model
from django.core.validators import validate_comma_separated_integer_list
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models.fields.related import ForeignKey
from django.http import JsonResponse
from django.utils import timezone


class UnescapedDjangoJSONEncoder(DjangoJSONEncoder):

    """ 自定义的JSON编码器, 强制ensure_ascii为False, 避免中文字符被编码为乱码 """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 强制ensure_ascii为False
        self.ensure_ascii = False

UnescapedJsonResponse = partial(JsonResponse, encoder=UnescapedDjangoJSONEncoder)

class SortableModelChoiceField(forms.ModelChoiceField):

    """
    为ModelChoiceField的choices进行排序是件很麻烦的事
    尽管我们可以对queryset属性使用`order_by`进行排序
    但是还需要考虑对数据库的优化(尽可能避免explain中出现`using filesort`)

    因此, 我们在ModelChoiceIterator中添加一个额外可选的属性, 以允许在遍历choices时对其进行排序
    这是在应用层的排序, 意在减少数据库的压力
    """

    class _ModelChoiceIterator(forms.models.ModelChoiceIterator):

        class _FakeQuerySet(UserList):
            _prefetch_related_lookups = ()

            def iterator(self):
                yield from self

        def __iter__(self):
            sort_key = self.field.sort_key
            if sort_key is not None:
                # sorted之后(立即执行数据库查询), _prefetch_related_lookups就没有意义了
                self.queryset = self._FakeQuerySet(sorted(self.queryset, key=sort_key))
            return super().__iter__()

    iterator = _ModelChoiceIterator

    def __init__(self, queryset, **kwargs):
        super().__init__(queryset, **kwargs)
        self.sort_key = kwargs.get("sort_key", None)

def multi_lines_log(logger: logging.Logger, string: str, level=logging.INFO):
    """ 记录多行日志 """
    for line in string.splitlines():
        logger.log(level, line)

def traceback_log(logger: logging.Logger, level=logging.ERROR):
    """ 记录异常栈 """
    multi_lines_log(logger=logger, string=traceback.format_exc(), level=level)

def traceback_and_detail_log(request, logger: logging.Logger, level=logging.ERROR):
    """ 记录异常栈和其他一些详细信息 """
    logger.log(level, "=" * 100)
    logger.log(level, "Exception:")
    logger.log(level, "Time: %s" % timezone.make_naive(timezone.now()).strftime("%Y-%m-%d %H:%M:%S"))
    logger.log(level, "Url: %s" % request.path)
    logger.log(level, "Method: %s" % request.method)
    logger.log(level, "Cookies: %s" % request.COOKIES)
    logger.log(level, "Session: %s" % dict(request.session.items()))
    if request.method == "POST":
        logger.log(level, "Post data: %s" % request.POST.dict())
    logger.log(level, "")
    traceback_log(logger=logger, level=level)
    logger.log(level, "=" * 100)

def validate_comma_separated_integer_list_and_split(string: str, auto_strip=True) -> list:
    """ 判断字符串是否是一个以逗号分隔的数字列表
    如果是, 则自动进行分割并返回列表; 如果不是, 则抛出ValidationError异常
    :param string: 要解析的字符串
    :param auto_strip: 为True时则提前对string进行strip(默认)
    :return: list
    """
    if auto_strip:
        string = string.strip()
    validate_comma_separated_integer_list(string)
    return [int(x) for x in string.split(',')]

def model_to_dict_(instance: Model) -> dict:
    """ Django有一个内置的django.forms.models.model_to_dict方法(以下简称原model_to_dict方法)
    可以方便地把模型转为字典, 但是有一个坑, 被标记为不可编辑(editable为False)的字段不会包含在输出的字典中
    原model_to_dict方法仅在初始化ModelForm时被使用, 为了安全起见, 这样做无可厚非
    但是我们想要的"模型转为字典"的方法应该包含模型的所有字段
    所以我们参考原model_to_dict方法编写了新的model_to_dict_方法
    比起原model_to_dict方法缺少了fields和exclude参数, 因为我们暂时不需要
    """
    opts = instance._meta
    data = {}
    for f in chain(opts.concrete_fields, opts.private_fields, opts.many_to_many):
        # 对于一对一和多对一外键, 返回外键模型对象 (多对多外键会在else子句中合适地处理)
        # 注: 由于ForeignKey的attname属性值为"字段名_id", 所以调用value_from_object方法的话, 返回的是外键对象的id
        if isinstance(f, ForeignKey):
            data[f.name] = getattr(instance, f.name, None)
        else:
            data[f.name] = f.value_from_object(instance)
    return data

def del_session_item(request, *items):
    """ 从request会话中删除键值 """
    for item in items:
        request.session.pop(item, None)
