# 安宁礼仪与公墓运营服务

这是一个供殡仪馆、公墓和合作医疗机构使用的 Python 后端服务，统一管理逝者业务档案、遗体保管交接、送别厅与火化设备预约、服务订单、墓位权属、账单收款和审计时间线。系统把容易产生争议的交接、排程与收费动作保存在本地 SQLite 中，支持在单个 Linux 应用容器内离线运行。

## 运行环境

- Python 3.11
- FastAPI 与 Uvicorn
- SQLite 3，由 Python 标准库提供

## 安装

依次执行 python -m venv .venv、source .venv/bin/activate、python -m pip install -e ".[dev]"。可通过 PEACEFUL_CARE_DATABASE_PATH 指定数据库文件，默认写入项目的 data 目录。

## 初始化与启动

先执行 python -m app.cli init-db 和 python -m app.cli check-db，再用 uvicorn app.main:app --host 0.0.0.0 --port 8432 启动。健康检查为 GET /api/system/health。殡葬业务接口位于 /api/mortuary，涵盖档案、交接、资源、预约、服务订单、墓位权属、账单和时间线。

## 测试与编译检查

测试命令：python -m pytest

编译命令：python -m compileall -q app tests

API 与 CLI 冒烟命令：python -m app.cli smoke、python -m app.cli mortuary-demo

## 目录结构

- app/mortuary：档案、保管交接、资源排程、权属和账单领域
- app/api：登录、角色、审计及系统管理接口
- app/core：时钟、安全、异常、隐私与分页能力
- app/repositories：通用身份和审计数据访问
- app/services：会话、权限、后台任务及维护服务
- tests：领域、接口、异常路径和身份回归测试

## 一致性约定

SQLite 连接启用外键、WAL、忙等待和即时事务。业务档案采用外部编号去重，保管交接与预约保留幂等键，服务订单开票后不可再次开票，支付流水不能重复分配。关键状态变化同时写入领域时间线；会话令牌仅保存摘要，审计记录不会保存明文密码或令牌。

## 价格目录版本

- 价格以 `price_catalogs`/`price_items` 形式按版本管理：新建为草稿（可改名、改生效日、整体替换项目、在草稿内停用项目），发布后内容不可原地覆盖，只能新建下一版；已发布版本可停用。
- 发布采用部分唯一索引约束同一生效日只能有一个已发布版本（配合 `BEGIN IMMEDIATE` 串行化并发发布）；版本可预约未来生效，下单与确认只取“已发布且生效日不晚于今天”的最近一版，未来版本不会提前影响价格。
- 服务订单在确认时冻结项目名称、单价、计量单位、适用条件（`applicability`）与目录版本（`catalog_version_no` 等字段）。调价、项目停用或目录停用都不改变历史订单与已开账单；账单每行都带 `price_basis`，可直接追溯到当时有效的目录版本。
- 补差与退款写入独立的 `order_price_adjustments`，通过外键关联原订单，支持引用调价所依据的目录版本，并以幂等键防重复；账单视图汇总补差/退款金额，但不重写账单本金。
- `GET /api/mortuary/price-catalogs/diff/{from_version}/{to_version}` 比较任意两版的新增、移除与单价/名称/状态变化。

接口前缀均为 `/api/mortuary`：目录 `POST/GET /price-catalogs`、`GET /price-catalogs/effective`、`PATCH /price-catalogs/{id}`、`POST /price-catalogs/{id}/publish|retire`、`POST /price-catalogs/{id}/items/{item_id}/discontinue`；下单 `POST /service-orders`（不再由调用方传单价）、试算 `POST /service-orders/quote`、确认 `POST /service-orders/{id}/confirm`、调整 `POST /service-orders/{id}/adjustments`。
