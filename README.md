# Linux Ops Agent

通过自然语言调用模型生成 Shell 命令，经 SSH 在远程 Linux 执行。支持服务诊断、Docker、文件编辑以及远端账号能够执行的其他操作。默认使用账户密码登录，允许 root。

## 快速开始（Windows）

安装 Python 3.11+。远端需要 SSH 服务和 Bash，以及任务使用的命令（例如 Docker）。在项目目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Initialize-Venv.ps1
Copy-Item .env.example .env
notepad .env
```

已有 `.env` 时直接编辑，不要覆盖。初始化脚本创建或修复虚拟环境，初始化 pip 并安装 requirements.txt。无需激活虚拟环境。填写：

```dotenv
OPS_PROVIDER=deepseek
DEEPSEEK_API_KEY=填写自己的模型密钥
DEEPSEEK_MODEL=deepseek-flash
OPS_SSH_TARGET=root@你的服务器地址
OPS_SSH_PORT=22
OPS_SSH_AUTH=password
OPS_SSH_PASSWORD='填写自己的SSH密码'
```

密码含特殊字符时按 dotenv 格式引用；不要提交 `.env`。服务器必须允许所选账户和认证方式登录，程序不会修改服务器的 SSH 配置。

双击 `Start.bat`，或执行：

```powershell
.\.venv\Scripts\python.exe agent.py
.\.venv\Scripts\python.exe agent.py "检查磁盘和内存使用情况"
```

Start.bat 会检测虚拟环境和依赖；缺少 Paramiko、OpenAI SDK 或 dotenv 时重新初始化。

## 模型来源

使用支持 Chat Completions 工具调用的模型；以下模型名是项目默认值，可改为账户实际支持的名称。

| OPS_PROVIDER | 地址 | 密钥变量 | 模型变量及默认值 |
| --- | --- | --- | --- |
| deepseek | https://api.deepseek.com | DEEPSEEK_API_KEY | DEEPSEEK_MODEL=deepseek-flash |
| openai | https://api.openai.com/v1 | OPENAI_API_KEY | OPENAI_MODEL=gpt-6 |
| qianwen | https://maas.qianwenaiapi.com/compatible-mode/v1 | QIANWEN_API_KEY | QIANWEN_MODEL，必须填写 |

Qianwen 是本项目配置的兼容接口来源。切换来源时设置相应密钥和模型名称。

## SSH 与配置

密钥登录示例：

```dotenv
OPS_SSH_AUTH=key
OPS_SSH_TARGET=ops@你的服务器地址
OPS_SSH_IDENTITY=C:/Users/你的用户名/.ssh/ops_agent
```

密钥模式直接读取指定私钥，不搜索 SSH agent 或其他密钥。当前不提供加密私钥口令配置。

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| OPS_PROVIDER | deepseek | 模型来源，见上表 |
| OPS_SSH_TARGET | 无 | 必填 user@host，允许 root，IPv6 可写 user@[地址] |
| OPS_SSH_PORT | 22 | SSH 端口 |
| OPS_SSH_AUTH | password | password 或 key |
| OPS_SSH_PASSWORD | 无 | 密码模式必填，不自动去除空白 |
| OPS_SSH_IDENTITY | 用户目录/.ssh/ops_agent | 仅密钥模式使用 |
| OPS_COMMAND_TIMEOUT | 600 | 每条远程命令等待秒数，0 不限；连接和认证分别仍有 8 秒超时设置 |
| OPS_COMMAND_OUTPUT_LIMIT | 48000 | stdout、stderr 各自最多保留的字符数，0 不截断 |
| OPS_MAX_STEPS | 128 | 每次用户任务的工具调用轮数，0 不限；每轮可调用多个命令，达到上限后请求最终回复 |
| OPS_AGENT_DB | 项目目录/audit.sqlite3 | SQLite 审计文件路径 |

系统环境变量优先于 `.env`；修改后重启程序。限制参数要求非负整数。

## 使用方式

可以直接输入：

- “检查 nginx 状态，查看最近错误日志并定位原因。”
- “查看 Docker 容器，重启 web 并检查运行状态。”
- “修改 /opt/app/config.json 中的端口为 8080，再检查文件内容。”

模型仅获得 `shell_command` 工具：

```json
{"command":"printf 'hello\n' | tee /tmp/example.txt; cat /tmp/example.txt"}
```

`command` 支持 Bash 管道、重定向和多行脚本，可选字符串 `stdin` 提供 UTF-8 标准输入。所有路径均属于远程服务器。没有交互终端，持续任务应以后台方式启动；交互式密码提示不会由程序自动回答。

返回字段为 `exit_code`、`stdout`、`stderr`、`timed_out`、`truncated`，发生异常时附带 `error`。未取得退出码时为 null。输出截断后仍继续读取并丢弃超出部分，避免阻塞；不截断时可能占用较多内存和模型上下文。

`/new` 清空上下文，`/exit` 或 `/quit` 退出，Ctrl+C 中断程序。清空上下文或退出不会撤销已经执行的操作。

## 执行权限与审计

命令直接执行，无终端审批、命令白名单或文件目录限制，权限由 SSH 登录账号决定。程序不自动添加 sudo，模型可显式使用 sudo。root 登录可操作整个服务器。

程序不读取、保存或校验主机密钥，未知和已变化的服务器密钥均接受，因此不会验证所连接服务器的身份。SSH 传输仍使用加密连接。

超时或连接关闭不代表远端命令已停止；程序不会自动重试 Shell 命令。应先查询状态再决定后续操作。

审计记录任务、工具名、命令参数（含 stdin）、结果及错误，使用 UTC 时间戳。SSH 登录密码不作为工具参数提供，配置密码的原文会从新写入审计及工具结果中替换；这不是通用秘密检测，其他凭据或编码后的内容仍可能被记录。截断后的输出才写入审计。模型上下文会包含命令输出和所读取文件的内容。

```powershell
.\.venv\Scripts\python.exe agent.py --audit
```

该命令查看最近 50 条记录，不要求连接 SSH 或配置有效模型密钥。原有 SQLite 记录保留。

## 旧版本迁移

默认 SSH 认证由 key 改为 password；原有 `.env` 显式设置 key 的仍使用密钥。新版本不改写现有 `.env`。

新通用超时、输出配置缺失时，分别兼容读取 OPS_DOCKER_TIMEOUT 和 OPS_DOCKER_OUTPUT_LIMIT。新配置显式设为 0 时优先使用 0。

OPS_KNOWN_HOSTS、OPS_ALLOWED_SERVICES、OPS_FILE_ROOTS、OPS_FILE_SUDO、OPS_FILE_BACKUP、OPS_DOCKER_SUDO 和 OPS_DOCKER_CONTAINERS 不再生效。旧文件工具的自动备份功能已移除。所有专用工具替换为 shell_command，旧工具调用代码需要同步迁移。

## 排错与测试

- 缺少依赖：重新运行 Initialize-Venv.ps1，或使用虚拟环境 Python 执行 `-m ensurepip --upgrade` 后执行 `-m pip install -r requirements.txt`。
- 认证失败：检查账户、密码、认证方式以及服务器是否允许密码或 root 登录；密钥模式检查私钥和服务器 authorized_keys。
- 无法连接：检查地址、端口、防火墙及 SSH 服务。程序不会自动调整服务器配置。
- 命令不存在或权限不足：检查远端软件安装和账户权限；没有自动 sudo 提权。
- 超时：先检查远端状态，再调整 OPS_COMMAND_TIMEOUT；构建、拉取镜像等操作可能持续较久。
- 模型认证或模型不存在：检查所选来源对应的密钥、模型名称和账户额度。

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -v
```

测试使用模拟 SSH，不连接实际服务器或执行真实远程变更。
