# Linux Ops Agent (headless)

A minimal CLI-first Linux operations agent. It uses the DeepSeek Chat Completions API by default (with optional OpenAI support),
but **does not let the model submit arbitrary shell commands**. Tools are fixed in code:disk, memory, allowlisted systemd status,
bounded journal logs, and an approval-gatedrestart for allowlisted services, plus scoped remote file operations with reviewed edits.

## Windows 本机 + DeepSeek 官方 API

程序在 Windows PowerShell 运行，通过 SSH 管理远程 Linux。Windows 不需要安装 Bash、systemd 或 WSL；这些命令在目标 Linux 上执行。

1. 确认 `python --version`（3.11+）和 `ssh -V` 可用。在本目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

如果已有 `.env`，直接编辑，不要覆盖。无需激活虚拟环境，因此不必修改 PowerShell 执行策略。

2. 在 `.env` 填入自己的配置：

```dotenv
OPS_PROVIDER=deepseek
DEEPSEEK_API_KEY=你的API密钥
DEEPSEEK_MODEL=
OPS_SSH_TARGET=opsbot@你的Linux服务器IP
OPS_SSH_PORT=22
OPS_SSH_IDENTITY=C:/Users/你的用户名/.ssh/密钥
OPS_KNOWN_HOSTS=C:/Users/你的用户名/.ssh/known_hosts
OPS_ALLOWED_SERVICES=nginx,ssh,sshd
```

3. 先确保 SSH 密钥登录可用。若已有专用密钥，直接配置其路径；否则创建一个（不要覆盖已有密钥）：

```powershell
ssh-keygen -t ed25519 -f "$HOME/.ssh/ops_agent"
Get-Content "$HOME/.ssh/ops_agent.pub"
```

将公钥交给服务器管理员，加入目标账号的 `~/.ssh/authorized_keys`。然后手动连接一次，核对服务器指纹后建立 known_hosts：

```powershell
ssh -i "$HOME/.ssh/ops_agent" -p 22 opsbot@你的Linux服务器IP
```

登录成功后输入 `exit` 返回 Windows，再测试程序使用的免交互登录方式：

```powershell
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -i "C:/sshKey/ops_agent" -p 22 opsbot@你的Linux服务器IP "id"
```

如果私钥有口令，需先通过 Windows ssh-agent/ssh-add 加载；程序本身不弹出 SSH 密码或口令提示。首次连接请通过管理员提供的指纹核验主机身份。程序禁止 root SSH 账号。

4. 启动连续对话：

也可双击 `Start.bat` 启动。首次运行会调用 `Initialize-Venv.ps1`，检测兼容的 Python 并补全 `.venv/pyvenv.cfg` 的路径、版本等配置；后续环境检查失败时会重新初始化，初始化失败则停止启动。依赖仍按前面的安装步骤安装。

需要手动指定 Python 时，在本目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Initialize-Venv.ps1 -PythonPath "C:\Python313\python.exe"
```

```powershell
.\.venv\Scripts\python.exe agent.py
```



每次输入一行，回车提交。同一次会话支持追问；`/new` 清空上下文，`/exit` 或 Ctrl+C 退出。关闭程序后不恢复聊天上下文；审计记录会保存在 `audit.sqlite3`。上下文会随对话增长，任务完成后可用 `/new` 开始新任务。

单次执行和查看审计：

```powershell
.\.venv\Scripts\python.exe agent.py "检查磁盘、内存和 nginx 状态"
.\.venv\Scripts\python.exe agent.py --audit
```

若模型请求重启服务，程序会显示目标、服务、理由，并要求手动输入完整的 `RESTART nginx`（服务名随目标变化）。其他输入均拒绝。远端账号还必须有下文所述的有限 sudo 权限。诊断输出和日志会发送给 DeepSeek，并保存到本地审计数据库。

常见问题：`Permission denied (publickey)` 表示密钥或服务器公钥配置不匹配；`Host key verification failed` 表示主机信任未建立或指纹改变；日志无权限需由管理员授予相应读取权限。API 认证、余额或模型错误需检查自己的官方账户与 `.env`。本工具不会自动安装或修复服务器环境。

官方接口参考：https://api-docs.deepseek.com/guides/tool_calls/

## 远程文件操作与编辑

`OPS_FILE_SUDO=false`、`OPS_FILE_BACKUP=false` 为默认值；touch 可操作普通二进制文件或大文件，仅更新已有文件时间戳。

在现有 `.env` 中添加允许访问的 **Linux 服务器目录**，多个目录用英文逗号分隔，然后重启程序：

```dotenv
OPS_FILE_ROOTS=/home/opsagent/app,/srv/myapp
```

启用直接 sudo 时在 `.env` 中设置：

```dotenv
OPS_FILE_SUDO=true
OPS_FILE_BACKUP=false
```

SSH 账号须已有该 Python 命令的免密 sudo 权限，且服务器存在 `/usr/bin/python3`；权限不足会直接报错，不会要求输入 sudo 密码。授权目录限制是本程序的检查，并非 sudo 本身的目录沙箱：允许 sudo 执行 Python 的账号本身具有更广的 root 执行能力，因此本机代码和配置应由可信用户管理。sudo 新建文件归 root 所有。原有重启服务的 sudo 权限配置独立保留。

这些目录必须已存在，SSH 账号需要相应读写权限。未配置时文件工具保持关闭。文件工具需要远程服务器安装 `python3`，无需在服务器部署本项目；固定辅助代码通过 SSH 标准输入临时执行。Windows 本地文件不在此工具的操作范围内。

| 能力 | 用法与限制 |
| --- | --- |
| 查看授权目录 | `file_roots` 返回配置的范围 |
| 列目录 | `list_directory`，最多 200 项 |
| 读取文本 | `read_file`，按行读取，每次最多 200 行，返回文件 SHA256 |
| 搜索内容 | `search_files`，递归按字面文本查找，最多扫描 500 个目录/文件、返回 30 处匹配 |
| 创建或覆盖文件 | `write_file`，展示修改差异并确认 |
| 精确编辑 | `replace_text`，旧文本必须恰好出现一次，其他内容不变 |
| 创建空文件 / 更新时间戳 | `touch_file`，已有普通文件内容保持不变，不限制文件大小和编码 |
| 创建目录 | `make_directory`，创建单层目录，父目录必须存在 |

可以直接这样交互（替换为自己的授权路径）：

```text
你> 看看允许访问哪些目录
你> 列出 /srv/myapp 的文件
你> 读取 /srv/myapp/config.ini 并解释各配置项
你> 在 /srv/myapp 中搜索包含 timeout 的文件
你> 把 /srv/myapp/config.ini 中 timeout=30 改为 timeout=60
你> 在 /srv/myapp 下创建 notes 目录，再写一份部署说明
```

每次写文件或创建目录前，会显示目标及操作预览；在审批提示处输入 `apply` 或 `approve` 才执行（不区分大小写，忽略首尾空白），其他输入或无交互终端会取消。审批通过会显示执行提示；在普通 `你>` 对话中输入这些词不直接授权文件操作。修改已有文件时先检查内容哈希，防止覆盖读取后已变化的文件；然后通过临时文件替换；仅当 `OPS_FILE_BACKUP=true` 时创建同目录的 `.ops-backup-随机标识` 备份（默认不备份）。工具会返回备份路径。恢复时可让助手读取备份，并把内容写回原文件，同样需要预览确认。备份不自动清理。

仅支持 64 KiB 以内的 UTF-8 普通文本文件；拒绝符号链接、多个硬链接、二进制文件及目录越界。普通模式下编辑已有文件要求 SSH 账号拥有该文件；sudo 模式可编辑其他账号拥有的文件并保留原属主和组。保留原文件的普通权限和组，但不保留 ACL、扩展属性、SELinux 标签或时间戳；含这些元数据的配置应通过专门的部署流程修改。新文件默认权限为 0640，备份为 0600，新目录为 0750，实际权限还受 umask 影响。

授权目录及其父目录应由可信账号控制。路径和哈希检查不提供对恶意并发目录替换的完整隔离，也不是跨进程文件锁；编辑时应避免其他程序同时写入。此版本未提供删除、移动、权限修改或任意 Shell 执行工具。文件内容会进入模型上下文和本地审计记录，请只授权与任务有关的目录。

本地验证：`.\.venv\Scripts\python.exe -m unittest -v test_agent test_file_tools`。Windows 文件测试对 Unix 权限接口使用模拟；真实 Linux 权限和 SSH 链路仍需在目标环境验证。



## Prerequisites
- Python 3.11+
- OpenSSH client
- A DeepSeek API key (default), or OpenAI API key with `OPS_PROVIDER=openai`
- A dedicated non-root SSH account on each target host
- Out-of-band access and backups before enabling remediation

## Install on the control host
```bash
sudo useradd --system --create-home --home-dir /opt/ops-agent --shell /usr/sbin/nologin opsagent
sudo install -d -o opsagent -g opsagent -m 0750 /opt/ops-agent/app
sudo install -d -o root -g opsagent -m 0750 /etc/ops-agent
sudo install -d -o opsagent -g opsagent -m 0750 /var/lib/ops-agent
# Copy agent.py, file_tools.py, remote_files.py and requirements.txt into /opt/ops-agent/app
sudo -u opsagent python3 -m venv /opt/ops-agent/venv
sudo -u opsagent /opt/ops-agent/venv/bin/pip install --upgrade pip
sudo -u opsagent /opt/ops-agent/venv/bin/pip install -r /opt/ops-agent/app/requirements.txt
```

Create `/etc/ops-agent/agent.env` (mode 0640, root:opsagent) based on `.env.example`.
Do not commit this file or place the API key in source control.

```bash
sudo chown root:opsagent /etc/ops-agent/agent.env
sudo chmod 0640 /etc/ops-agent/agent.env
```

## SSH trust and identity
Create a dedicated keypair, then install its public key only for the `opsbot` account on
the target. Verify the server host key through a trusted channel before placing it in
`known_hosts`; do not blindly trust `ssh-keyscan` output.

Example, run as the control service account:
```bash
sudo -u opsagent ssh-keygen -t ed25519 -N '' -f /var/lib/ops-agent/id_ed25519
sudo chmod 0600 /var/lib/ops-agent/id_ed25519
sudo install -o opsagent -g opsagent -m 0640 /path/to/verified_known_hosts /etc/ops-agent/known_hosts
```
The key must be readable by the service account. Configure the target SSH account to
disable password login and prohibit root login where feasible. Restrict source IPs in
the firewall. Test connectivity:
```bash
sudo -u opsagent ssh -i /var/lib/ops-agent/id_ed25519 \
  -o UserKnownHostsFile=/etc/ops-agent/known_hosts \
  -o StrictHostKeyChecking=yes opsbot@server.example.com 'id'
```

## Target-side permissions
Start read-only. The target account should not be root and should not have broad sudo.
The initial tools run `df`, `free`, `uptime`, `systemctl status/is-active`, and `journalctl`.
Journal access may require membership in a distribution-appropriate group (for example
`systemd-journal`) or a narrowly reviewed sudo rule. Group membership may reveal sensitive
logs; review your policy first.

If enabling restart, add a narrowly scoped sudoers rule using `visudo`, adjusted for the
target distribution and verified absolute path. Example only:
```
opsbot ALL=(root) NOPASSWD: /usr/bin/systemctl restart nginx, /usr/bin/systemctl restart ssh, /usr/bin/systemctl restart sshd
```
Do not add wildcard commands or allow `bash`, editors, package managers, or arbitrary systemctl arguments. Check actual `systemctl` path with `command -v systemctl`. Service names are additionally limited by `OPS_ALLOWED_SERVICES`.

## Run
```bash
sudo -u opsagent env \
  DEEPSEEK_API_KEY='...' \
  DEEPSEEK_MODEL='deepseek-flash' \
  OPS_SSH_TARGET='opsbot@server.example.com' \
  OPS_SSH_IDENTITY='/var/lib/ops-agent/id_ed25519' \
  OPS_KNOWN_HOSTS='/etc/ops-agent/known_hosts' \
  OPS_ALLOWED_SERVICES='nginx,ssh,sshd' \
  OPS_AGENT_DB='/var/lib/ops-agent/audit.sqlite3' \
  /opt/ops-agent/venv/bin/python /opt/ops-agent/app/agent.py \
  'Check disk usage, memory, nginx status, and recent nginx logs'
```
Prefer loading variables from a protected environment file rather than putting secrets in
shell history. For a simple one-off, use a root-owned `agent.env` with mode 0640 and a
service account group.

To inspect audit events:
```bash
sudo -u opsagent env OPS_AGENT_DB=/var/lib/ops-agent/audit.sqlite3 \
  /opt/ops-agent/venv/bin/python /opt/ops-agent/app/agent.py --audit
```

## Optional systemd oneshot for scheduled read-only checks
Create `/etc/systemd/system/ops-agent.service`:
```ini
[Unit]
Description=Headless Linux Operations Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=opsagent
Group=opsagent
WorkingDirectory=/opt/ops-agent/app
EnvironmentFile=/etc/ops-agent/agent.env
ExecStart=/opt/ops-agent/venv/bin/python /opt/ops-agent/app/agent.py Check disk usage, memory, and nginx status
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/ops-agent
RestrictSUIDSGID=true
LockPersonality=true
```
Then:
```bash
sudo systemctl daemon-reload
sudo systemctl start ops-agent.service
sudo journalctl -u ops-agent.service -n 100 --no-pager
```
**Important:** an unattended systemd task has no interactive terminal, so a high-risk action
cannot receive approval. Do not use this oneshot for tasks that may restart services. The
agent will fail closed if approval input is unavailable. Use interactive CLI mode for
remediation. A production approval workflow should use a separate authenticated approval
channel with expiry, identity, and immutable audit trail.

## Security notes / limitations
- This is a starter implementation, not a fully hardened enterprise system.
- The model can choose only predefined tools; it cannot supply shell commands.
- Keep target host allowlists and service allowlists small. Separate environments and keys.
- Require change windows, backups, monitoring, and a rollback plan for remediation.
- Logs can contain secrets or prompt-injection text; treat all output as untrusted and avoid
  sending unnecessary logs to the model.
- Set API budgets/rate limits and network egress policy. Rotate credentials.
- Add an independent watchdog and alerting before unattended production use.
- Test on a disposable VM first. Do not initially grant write permissions or restart rights.
