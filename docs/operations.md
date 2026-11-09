# 部署、备份、恢复和维护

以下为学院单台服务器的部署步骤。正式开放前需要提供服务器、HTTPS 域名、数据库口令和模型凭据，并在目标服务器完成验收。

## 本地开发与Demo

见README。API、网页和worker分别是三个进程。数据库升级必须先执行 `alembic upgrade head`；API不会自动建表或清库。`/api/health` 同时检查数据库连通和迁移版本表。

本地日志由启动终端输出；API关闭默认访问日志，避免URL中的内部记录号泛滥。异常只记录异常类型，不输出问题、手机号、密钥或SMTP完整错误。用户错误响应不包含堆栈。不要自行开启HTTP请求/响应体调试。

网站顶部显示当前模型和检索方式。工作台可查看紧急事件和邮件状态。审计记录保存在数据库。运维需监控工作进程和队列积压。

## Docker Compose（单机）

从工程根目录准备 `.env`，复制 `.env.production.example`，设置独立PostgreSQL口令、URL、HTTPS域名和服务凭据。示例值是占位符，不能直接上线。URL密码需URL编码。数据库与原文件存储分别持久化到Docker卷。

```sh
docker compose --env-file .env -f deploy/compose.yaml config --quiet
docker compose --env-file .env -f deploy/compose.yaml build
docker compose --env-file .env -f deploy/compose.yaml up -d
docker compose --env-file .env -f deploy/compose.yaml ps
docker compose --env-file .env -f deploy/compose.yaml logs --tail=100 api worker
docker compose --env-file .env -f deploy/compose.yaml exec api python -m app.cli create-admin --username college-admin
```

Compose 启动顺序为数据库 → 迁移 → BGE 缓存与索引准备 → API → 网站。邮件工作进程在迁移完成后启动。首次使用 BGE 时需要能下载模型，后续从持久卷读取。管理员密码在终端隐藏输入。网页端默认监听服务器 `127.0.0.1:8080`，由服务器外层 HTTPS 反代转发。API 与工作进程使用同一配置和数据库。API 保持单进程，加副本前先实现共享限流和预算。

正式环境要求 HTTPS、Secure Cookie 和 PostgreSQL。邮件配置保持 `MAIL_MODE=mock` 和 `ALLOW_REAL_EMAIL=false`。使用云端 DeepSeek 时，必须在服务器环境配置 `DEEPSEEK_API_KEY`，相关资料片段会随模型请求外发。BGE 和 OCR 由服务器本地执行，无需 API Key。

容器镜像使用固定 Python、Node 和 uv 版本，应用依赖由锁文件管理。镜像内包含 LibreOffice Impress、Poppler、Tesseract 和简体中文识别包。正式配置开启 OCR 后，含图 PPTX 按实际画面渲染，扫描 PDF 按页识别。OCR 结果保留页码并等待管理员核对，发布后进入问答。此前上传的含图 PPT 可先停用，再重新解析、核对和发布。

## 数据库和原文件备份

备份含大量长期个人资料，只允许授权人员访问；文件应放加密磁盘或受限备份存储，异机保留，定期恢复演练。不要提交到Git。预警7天清理功能不会处理备份，因为当前根本不删除。

本地SQLite（在backend目录执行，程序读取此目录.env）：

```sh
.venv/bin/python ../scripts/backup.py backup --destination ../backups/demo-2026-11-06.zip
.venv/bin/python ../scripts/backup.py restore --archive ../backups/demo-2026-11-06.zip --destination ../backups/restored-2026-11-06
.venv/bin/python ../scripts/verify-restored.py ../backups/restored-2026-11-06
```

Windows将Python路径替换为 `.venv/Scripts/python.exe`。目标文件和恢复目录必须不存在，脚本拒绝覆盖。备份使用SQLite备份API，包含数据库和不可变原文件，清单保存SHA256；恢复逐文件校验、限制路径、检查数据库完整性。恢复演练只创建新目录，不连接或覆盖现用数据库。

正式PostgreSQL建议先停止写入进程，保留数据库容器：

```sh
docker compose --env-file .env -f deploy/compose.yaml stop api worker web
docker compose --env-file .env -f deploy/compose.yaml exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-acl' > database.dump
docker compose --env-file .env -f deploy/compose.yaml cp api:/data/files ./original-files
docker compose --env-file .env -f deploy/compose.yaml start api worker web
```

重定向dump示例在Linux bash使用，避免旧Windows PowerShell把二进制当文本改写。也可在运维主机安装匹配版本pg_dump，配置同一数据库URL，运行backup.py；PostgreSQL模式通过环境变量传密码，不输出到命令行。运行目录要能读取正确.env。备份后记录迁移版本、应用版本、文件哈希与时间。

## 恢复到独立PostgreSQL

1. 先用 `backup.py restore` 仅解压至新目录并核对哈希，得到database.dump与files。
2. 在独立测试数据库实例创建**全新空数据库**，不要在现用库执行drop或clean。
3. `pg_restore --no-owner --no-acl --exit-on-error -d NEW_EMPTY_DATABASE database.dump`，连接密码用PGPASSWORD/受限pgpass配置；不把真实连接串贴进报告。
4. 复制恢复出的files到新存储目录，运行独立API，设置不同端口与PUBLIC_ORIGIN，检查档案、问答、待办、资料原文、邮件状态和审计。演练时强制模拟邮件，避免恢复待发任务后真实外发。
5. 验证迁移版本、原文件哈希、业务行数及外键，授权后再安排正式切换。原环境保持完整，不能通过删库重建来掩盖升级问题。

本次SQLite恢复有实际记录；PostgreSQL恢复没有执行，不能当成已验收。

## 升级与回滚

1. 记录当前代码版本、镜像ID、迁移版本与配置版本，先备份数据库+原文件，并完成独立恢复检查。
2. 在隔离环境用同等数据量验证新代码和迁移。新增字段/表采用兼容方式，避免部署期间旧代码不能读取。
3. 维护窗口停止api/worker/web写入，更新镜像，执行 `alembic upgrade head`，启动，核对健康、登录、问答、后台周报与邮件队列。
4. 应用回滚优先切回保留的旧镜像，前提是旧版兼容迁移后的数据库。不可兼容时，从备份恢复到新的数据库与原文件目录，验证后切换配置，保留旧库供审计；切换期间新增记录需专门制定补偿方案。
5. 不在生产直接执行 `alembic downgrade base`；初始迁移的downgrade会删除所有业务表，只供全新测试库验证。任何不可逆生产数据操作均需另行授权。

## 日常维护

- 每天看健康接口、进程状态、紧急事件、失败/不确定任务及积压；邮件成功不能替代人工处置。
- 定期检查管理员权限、会话、备份可恢复性、磁盘容量、依赖漏洞和证书有效期。
- 原文件不可手工移动或重命名；版本修改通过上传新资料和发布/停用。更新派生向量可在资料详情点重建索引。
- 留存命令 `python -m app.cli retention-preview` 只预览，没有删除选项；后台同样只预览。
- 停止Demo时结束网站、API和worker进程；数据库和文件保留。未授权不推送GitHub或对外部署。
