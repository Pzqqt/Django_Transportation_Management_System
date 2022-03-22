from functools import wraps
from collections import namedtuple
import threading
import logging

from django.utils import timezone


_logger = logging.getLogger(__name__)

ValueAndType = namedtuple("ValueAndType", ["value", "type"])

class ExpireLruCache:

    """ 一个简易的支持过期功能的缓存装饰器, 在该项目中, 用于装饰那些频繁访问数据库且不要求时效性的方法
    和标准库中的functools.lru_cache一样, 被装饰的函数不能使用不可哈希的参数
    参数expire_time为过期时间, 必须是datetime.timedelta类型, 默认为3分钟
    参数enable_log为真值时, 则每次取出缓存时记录日志[方法名, 参数, 获取缓存的次数]
    """

    def __init__(self, expire_time=timezone.timedelta(minutes=3), enable_log=False):
        assert isinstance(expire_time, timezone.timedelta), "expire_time参数必须为datetime.timedelta类型"
        self.expire_time = expire_time
        self.enable_log = enable_log
        self._dic = {}
        self._lock = threading.RLock()

    @staticmethod
    def _print_log(func, args, kwargs, count):
        _logger.info("%s: function name: %s, args: (%s), get cache count: %s" % (
            __name__,
            func.__name__,
            ", ".join([*[str(arg) for arg in args], *["%s=%s" % (k, v) for k, v in kwargs]]),
            count,
        ))

    def __call__(self, func):
        @wraps(func)
        def _func(*args, **kwargs):
            # 被装饰函数调用时的每个参数都必须是可哈希的
            hashable_args = tuple((ValueAndType(value=arg, type=type(arg)) for arg in args))
            hashable_kwargs = frozenset((
                (k, ValueAndType(value=v, type=type(v))) for k, v in kwargs.items()
            ))
            key_ = hash((func, hashable_args, hashable_kwargs))
            with self._lock:
                if key_ in self._dic.keys():
                    if self._dic[key_]["latest_update_time"] + self.expire_time > timezone.now():
                        self._dic[key_]["count"] += 1
                        if self.enable_log:
                            self._print_log(func, args, kwargs, self._dic[key_]["count"])
                        return self._dic[key_]["result"]
            result = func(*args, **kwargs)
            with self._lock:
                self._dic[key_] = {
                    "result": result,
                    "latest_update_time": timezone.now(),
                    "count": 0,
                }
            return result
        return _func

'''
# ExpireLruCache的函数装饰器版, 效果与类装饰器版完全一致, 但可读性不及类装饰器版, 仅供参考

def ExpireLruCache(expire_time=timezone.timedelta(minutes=3), enable_log=False):

    _dic = {}
    _lock = threading.RLock()

    def _print_log(func, args, kwargs, count):
        _logger.info("%s: function name: %s, args: (%s), get cache count: %s" % (
            __name__,
            func.__name__,
            ", ".join([*[str(arg) for arg in args], *["%s=%s" % (k, v) for k, v in kwargs]]),
            count,
        ))

    def wrapper(func):
        @wraps(func)
        def _func(*args, **kwargs):
            nonlocal _dic
            hashable_args = tuple((ValueAndType(value=arg, type=type(arg)) for arg in args))
            hashable_kwargs = frozenset((
                (k, ValueAndType(value=v, type=type(v))) for k, v in kwargs.items()
            ))
            key_ = hash((func, hashable_args, hashable_kwargs))
            with _lock:
                if key_ in _dic.keys() and _dic[key_]["latest_update_time"] + expire_time > timezone.now():
                    _dic[key_]["count"] += 1
                    if enable_log:
                        _print_log(func, args, kwargs, _dic[key_]["count"])
                    return _dic[key_]["result"]
            result = func(*args, **kwargs)
            with _lock:
                _dic[key_] = {
                    "result": result,
                    "latest_update_time": timezone.now(),
                    "count": 0,
                }
            return result
        return _func

    return wrapper
'''
