from django import template
from django.utils.safestring import mark_safe

register = template.Library()

# Unused
@register.simple_tag()
def csrfmiddlewaretoken_js():
    """ 用于在ajax时添加csrf_token数据, 注意结尾不再需要加逗号
    示例:
    $.post(
      "post/url",
      {
        "key": "value",
        // "csrfmiddlewaretoken": $("[name='csrfmiddlewaretoken']").val(),
        // 替换为:
        {% csrfmiddlewaretoken_js %}
      },
      ...
    );
    """
    return mark_safe('"csrfmiddlewaretoken": $("[name=\'csrfmiddlewaretoken\']").val(),')

register.filter(name="max")(lambda value: max(value))
register.filter(name="abs")(lambda value: abs(value))
register.filter(name="sum")(lambda value: sum(value))
register.filter(name="subtract")(lambda value, arg: int(value) - int(arg))
register.filter(name="bool")(lambda value: value and 1 or 0)
