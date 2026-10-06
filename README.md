# 安宁礼仪与公墓运营服务

这是一个供殡仪馆、公墓和合作医疗机构使用的 Python 后端服务，统一管理逝者业务档案、遗体保管交接、送别厅与火化设备预约、服务订单、墓位权属、账单收款和审计时间线。系统把容易产生争议的交接、排程与收费动作保存在本地 SQLite 中，支持在单个 Linux 应用容器内离线运行。

## 运行环境

- Python 3.11
- FastAPI 与 Uvicorn
- SQLite 3，由 Python 标准库提供

## 安装

依次执行 python -m venv .venv、source .venv/bin/activate、python -m pip install -e ".[dev]"。可通过 PEACEFUL_CARE_DATABASE_PATH 指定数据库文件，默认写入项目的 data 目录。

## 初始化与启动

先执行 python -m app.cli init-db 和 python -m app.cli check-db，再用 uvicorn app.main:app --host 0.0.0.0 --port 8432 启动。健康检查为 GET /api/system/health。殡葬业务接口位于 /api/mortuary，涵盖档案、交接、资源、预约、价格目录、服务订单、墓位权属、账单和时间线。

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

## 价格目录版本

- `POST/GET /api/mortuary/price-catalogs`：建立草稿版本（含服务项目、单价、适用条件）或列出全部版本；`GET /price-catalogs/current` 返回下单时刻有效的版本；`PATCH /price-catalogs/{id}` 仅草稿可用；`POST /price-catalogs/{id}/publish` 指定生效时间发布；`POST /price-catalogs/diff` 比较任意两版的新增、移除、价格与条件差异。
- 服务订单不传 `unit_price_cents` 时自动按当前有效目录取价；确认（`POST /service-orders/{id}/confirm`）时冻结项目名称、单价、适用条件、目录版本与冻结时间。
- `POST /service-orders/{id}/adjustments` 登记退款（refund）或补差（surcharge），凭证号去重；原订单冻结金额不改写。账单明细中的 `price_basis` 可回溯到订单确认时有效的目录行。

## 一致性约定

SQLite 连接启用外键、WAL、忙等待和即时事务。业务档案采用外部编号去重，保管交接与预约保留幂等键，服务订单开票后不可再次开票，支付流水不能重复分配。价格目录以版本管理：草稿可改、发布后不可原地覆盖，同一时刻只有一个有效版本，未来生效版本不会提前影响下单；服务订单确认时冻结项目、单价、适用条件与目录版本，退款和补差只追加调整记录关联原订单，账单明细可追溯到当时有效的价格依据。关键状态变化同时写入领域时间线；会话令牌仅保存摘要，审计记录不会保存明文密码或令牌。
