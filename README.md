# 管理学院制度问答与问题处理网站

从零开发的模块化单体网站。手机、电脑通过浏览器使用；无需安装客户端。

当前交付为本地 **Demo**：真实数据库、文件解析、检索、问题处理和持久化预警任务；使用合成制度、模拟模型、模拟邮件，不产生真实外发或付费调用。正式知识库尚未提供。微信真机、DeepSeek、SMTP与正式部署分别验收。

## 快速打开

本机服务运行时：<http://localhost:5173/>；后台：<http://localhost:5173/#/admin>。

管理员账号 `demo-admin`，随机密码保存在 `backend/runtime/demo-access.txt`。该文件和演示数据库均被Git忽略，不上传。学生/家长表单请使用虚构姓名和演示号码，号码只作格式校验，不会联系。

首次启动（Windows PowerShell，从工程根目录执行）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-demo.ps1 -Initialize
```

后续启动去掉 `-Initialize`。初始化拒绝覆盖非空库；**不会每次启动重新填充或删除数据**。已有 `backend/.env` 时启动脚本沿用，不覆盖配置；要更换环境请先确认数据库和存储路径不同。版本：Python 3.14.3、Node 24.13.0、npm 11.6.2、uv 0.11.12。

## 手动启动 / macOS / Linux

以下命令均在 `backend/` 执行；Windows把 `.venv/bin/python` 换成 `.venv/Scripts/python.exe`。

```sh
cp ../.env.demo.example .env
uv sync --locked
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m app.cli demo-init  # 仅首次、仅全新独立demo库
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

另外两个终端分别启动：

```sh
# backend/
.venv/bin/python -m app.alerts
# web/
npm ci --ignore-scripts
npm run dev -- --port 5173 --strictPort
```

所有配置从环境变量或后端工作目录的 `.env` 读取。开发示例 `.env.development.example`，演示示例 `.env.demo.example`，正式示例 `.env.production.example`。测试使用独立临时库，不读取旧项目环境。

## 演示什么

1. 学生登记 → 问“2026年度奖学金申请需要哪些材料？” → 打开引用。
2. 家长填写两个孩子 → 以家长本人提问；没有学生绑定或孩子身份切换。
3. 问“校外住宿补助如何申请？” → 无依据 → 后台问题池和周报。
4. 管理员录入正式答复 → 保存草稿 → 发布 → 复测原题 → 关联资料编号 → 完成处理。
5. 使用明确标注的虚构危险测试文本 → 立即建立事件与邮件任务 → 模拟发送 → 人工处置。

详见 [演示操作](docs/demo.md)、[本次测试结果](docs/verification.md)。

## 测试

```sh
# backend/
uv run --locked pytest -q
uv run --locked ruff check app tests
uv run --locked alembic check
# web/
npm run typecheck
npm run lint
npm run build
npx playwright install chromium
npm run test:e2e
```

浏览器测试需先启动独立Demo（包括模拟邮件worker），只对虚构演示库执行，会新增演示记录。不要指向正式服务。端到端测试结果保存在 `web/test-results/`，演示截图在 `docs/evidence/`。

## 工程结构

```text
backend/app/         API、数据模型、认证、解析、检索、问答、预警与后台
backend/migrations/ Alembic版本化迁移
backend/tests/      新工程自动化测试
web/src/            React网站，用户端和管理端
web/tests/          Playwright浏览器流程
deploy/             单服务器Compose、Dockerfile、Nginx
scripts/            启动、备份与只恢复到新目录的工具
docs/               需求对应、架构、验收、演示和维护文档
```

## 运维和边界

[架构与权限](docs/architecture.md) · [需求与实现对应](docs/requirements.md) · [部署备份维护](docs/operations.md) · [限制与真实接入](docs/limitations.md)

不按手机号查询或恢复敏感数据；每次新登记建立独立资料，会话令牌仅浏览器持有。家长孩子信息不引用学生账号。长期保存资料、问答和处理历史；**预警7天清理仅提供候选预览，不执行删除**。

没有真实模型/邮件/服务器凭据也可演示。未经授权不推送GitHub、不公开发布、不正式部署、不真实发信。
