# Django_Transportation_Management_System
基于Django实现的物流管理系统（TMS，Transportation Management System）

前几年工作时忙里偷闲写的练手项目。

## 特点

- 前端基于Bootstrap 4框架和AdminLTE框架。
- 使用MySQL作为数据库后端。
- 实现了运单录入、发车出库、到货签收、客户签收等基本功能。
- 拥有较为完善的报表功能和财务管理功能。
- 可以通过后台界面对各个用户进行权限管理。

## 缺陷

- 由于没有认清时代潮流，所以没有做前后端分离，~~本来是打算当作跳槽的敲门砖的，淦！~~。
- 由于前期纯粹是抱着练手的目的写的，边学边做，所以没有保留历史提交，~~但保留了上百个历史版本的备份~~。
- 由于知识匮乏，所以重复造了很多轮子。
- 由于没有时间~~太懒~~，所以没有编写使用文档。

## TODO

- 实现打印货物标签和提货单的功能。（需要配合打印控件）
- 实现消息功能。

## 依赖

- 要求Python最低版本：v3.9+

- 必要的第三方库
  - django
  - mysqlclient
  - openpyxl （用于实现报表导出功能）
- 可选的第三方库
  - django-debug-toolbar （用于调试）
  - django-extensions （用于增强`manage.py`的功能）

## Usage

- 克隆仓库
- 安装并配置好MySQL，过程不再赘述
- cd到项目所在目录
- 同步AdminLTE-3.0.5：
  - 运行`git submodule init`
  - 运行`git submodule update --depth=1`
- 编辑`PPWuliu/settings.py`，手动配置以下这些项目：
  - SECRET_KEY
  - DATABASES
- 手动创建数据库（数据库名称与`PPWuliu/settings.py`中`DATABASES`所配置的一致）
- 导入测试数据：运行`init_database.sh`（测试数据中的账号密码：见此文件）
> 注意：在Windows环境下运行执行此shell脚本是不可能的（使用Git For Windows自带的mingw64执行也不行，会在`git apply`之后异常退出且没有任何提示），如果你一定要在Windows系统下运行此项目，请阅读`init_database.sh`并手动运行这些命令。
- 运行`manage.py runserver`
- Django的Admin管理后台是默认启用的，请自行创建超级用户

## 预览

![](screenshots/P0.jpg)
![](screenshots/P1.jpg)
![](screenshots/P2.jpg)
![](screenshots/P3.jpg)
![](screenshots/P4.jpg)
![](screenshots/P5.jpg)
