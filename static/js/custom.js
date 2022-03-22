function alert_dialog(text) {
  new duDialog("错误", text, {okText: "确认"});
}
function confirm_dialog(title, text, callbacks) {
  new duDialog(
    title, text, {
      buttons: duDialog.OK_CANCEL,
      cancelText: "取消",
      okText: "确认",
      callbacks: callbacks,
    },
  );
}
function mdtoast_success(text) {
  mdtoast.success(text);
}
function mdtoast_error(text) {
  mdtoast.error(text);
}
function find_datatable_rows_all(datatable_obj) {
  return datatable_obj.rows().nodes().filter(function(row) {
    return $(row).find("input:checkbox");
  }).map($);
}
function find_datatable_rows_clicked(datatable_obj) {
  return datatable_obj.rows().nodes().filter(function(row) {
    return $(row).find("input:checkbox").is(":checked");
  }).map($);
}
$.extend({
  StandardPost: function(url, args){
    let form = $("<form method='post' hidden></form>");
    let input;
    $(document.body).append(form);
    form.attr({"action": url});
    $.each(args, function(key, value) {
      if ($.isArray(value)) {
        input = $("<select type='hidden' multiple></select>");
        input.attr({"name": key});
        value.forEach(function(value_) {
          input.append("<option value=" + value_ + "></option>")
        });
      } else {
        input = $("<input type='hidden'>");
        input.attr({"name": key});
      }
      input.val(value);
      form.append(input);
    });
    form.append(
      $("<input type='hidden' name='csrfmiddlewaretoken' value='" + $("[name='csrfmiddlewaretoken']").val() + "'>")
    );
    form.submit();
    form.remove();
  }
});
$(document).ready(function() {
  duDatepicker(".md-date-picker", {format: 'yyyy-mm-dd', auto: true, i18n: 'zh', maxDate: 'today'});
  mdtimepicker(".md-time-picker", {is24hour: true});
  $(".md-date-picker, .md-time-picker").removeAttr("readonly");
  $(".select2").select2({
    theme: "bootstrap4",
    dropdownCssClass: "text-sm",  // 与body缩放匹配
    width: "style",  // 解决越界问题
    minimumResultsForSearch: 5,  // 可选项少于5项则禁用搜索框
  });
  $(".multiple-select").multipleSelect({
    placeholder: "未指定",
    formatSelectAll: function() {return "[全选]"},
    formatAllSelected: function() {return "全部"},
    formatCountSelected: function(count, total) {return "已选择" + count + "项"},
    formatNoMatchesFound: function() {return "未选择"},
  });
  $('[data-widget="pushmenu"]').click(function() {
    Cookies.set("ui_enable_sidebar_collapse", Cookies.get("ui_enable_sidebar_collapse") !== "true");
  });
  // 全局禁用input获得焦点时按回车键提交表单, 除非该元素有"data-allow_enter_submit"属性
  $(".content-wrapper form input").keypress(function(e) {
    if (e.keyCode === 13 && $(this).attr("data-allow_enter_submit") === undefined) {
      e.preventDefault();
    }
  });
});
