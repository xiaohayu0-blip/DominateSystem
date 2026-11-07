# 部署、备份、恢复和维护

当前只启动本机Demo，不包含正式部署或公开发布授权。以下为单台服务器操作说明，正式执行前核对limitation与真实接入条件。

## 本地开发与Demo

见README。API、网页和worker分别是三个进程。数据库升级必须先执行 `alembic upgrade head`；API不会自动建表或清库。`/api/health` 同时检查数据库连通和迁移版本表。

本地日志由启动终端输出；API关闭默认访问日志，避免URL中的内部记录号泛滥。异常只记录异常类型，不输出问题、手机号、密钥或SMTP完整错误。用户错误响应不包含堆栈。不要自行开启HTTP请求/响应体调试。

后台“配置状态”可以看模型/邮件通道、SMTP就绪、待发数量；“紧急事件”可看失败、重试和投递不确定；“操作审计”记录重要操作。当前未实现worker心跳，值守需额外监控进程和队列积压。

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

Compose顺序：数据库健康 → 一次性迁移成功 → API/worker → 网站。密码在终端隐藏输入，不放命令行参数或源码。web默认仅映射服务器 `127.0.0.1:8080`，由服务器外层HTTPS反代转发到此端口。不要把数据库端口公开。api与worker必须使用相同.env、相同数据库。API保持单进程；加副本前先实现共享限流/预算。

正式环境配置校验强制HTTPS、Secure cookie和PostgreSQL。邮件默认 `ALLOW_REAL_EMAIL=false`，只有取得明确授权后再开启；QQ收件地址仅允许配置指定QQ邮箱。真实模型模式缺密钥会拒绝启动，不能静默改成模拟。

容器镜像使用固定Python/Node/uv版本；应用依赖由uv.lock和package-lock.json锁定。操作系统基础镜像需按安全维护窗口审核并更新，正式上线建议额外锁镜像digest。OCR不在默认镜像内；需要时由运维添加Poppler、Tesseract及中文语言包后单独验收。

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
