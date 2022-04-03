#!/usr/bin/sh

# 测试数据账号密码如下:

# 用户名		密码		用户类型
# csshd		666666		收货点
# csfgs		666666		分公司（到货点）
# kj_1		666666		会计（财务部）
# zxg_1		666666		货物装卸工1（货台）
# zxg_2		666666		货物装卸工2（货台）
# pzqqt		88888888	管理员

set -e

cat <<EOF | git apply
diff --git a/wuliu/common.py b/wuliu/common.py
index f29db03..56fa551 100644
--- a/wuliu/common.py
+++ b/wuliu/common.py
@@ -46,7 +46,8 @@ def is_logged_user_is_goods_yard(request) -> bool:
     """ 判断已登录的用户是否属于货场 """
     return get_logged_user_type(request) == User.Types.GoodsYard
 
-def _gen_permission_tree_list(root_pg_=PermissionGroup.objects.get(father__isnull=True)) -> list:
+def _gen_permission_tree_list(root_pg_) -> list:
     """ 根据所有的权限组和权限的层级结构生成列表, 用于前端渲染 """
     tree_list = []
     for pg in PermissionGroup.objects.filter(father=root_pg_):
@@ -59,7 +60,8 @@ def _gen_permission_tree_list(root_pg_=PermissionGroup.objects.get(father__isnul
         })
     return tree_list
 
-PERMISSION_TREE_LIST = _gen_permission_tree_list()
+PERMISSION_TREE_LIST = []
 
 def login_required(raise_404=False):
     """ 自定义装饰器, 用于装饰路由方法
diff --git a/wuliu/urls.py b/wuliu/urls.py
index 92406c3..796b2da 100644
--- a/wuliu/urls.py
+++ b/wuliu/urls.py
@@ -1,6 +1,6 @@
 from django.urls import path, include
 
-from . import views, apis
 
 # Unused
 def easy_path(view_func):
@@ -8,6 +8,8 @@ def easy_path(view_func):
     return path(view_func.__name__, view_func, name=view_func.__name__)
 
 app_name = "wuliu"
+urlpatterns = []
+'''
 urlpatterns = [
     # 登录
     path("login", views.login, name="login"),
@@ -136,3 +138,4 @@ urlpatterns = [
         ])),
     ])),
 ]
+'''
EOF

python3 ./manage.py migrate
python3 ./manage.py loaddata init_data.json

git checkout -- ./wuliu

echo "Done!"