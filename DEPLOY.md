# Arena2API v2.0 - 部署指南

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    Linux VPS (Docker)                        │
│                                                             │
│   ┌─────────────────────────────────────────────────┐       │
│   │           server.py (FastAPI)                    │       │
│   │                                                  │       │
│   │  ┌──────────┐ ┌──────────┐ ┌──────────┐        │       │
│   │  │Profile A │ │Profile B │ │Profile C │ ...     │       │
│   │  │tokens:25 │ │tokens:18 │ │tokens:30 │        │       │
│   │  │cookies   │ │cookies   │ │cookies   │        │       │
│   │  └──────────┘ └──────────┘ └──────────┘        │       │
│   │                                                  │       │
│   │  GET /v1/chat/completions ← 用户请求             │       │
│   │  POST /v1/extension/push  ← 扩展推送             │       │
│   └──────────────────────────────────────────────────┘       │
│                         ▲                                    │
└─────────────────────────│────────────────────────────────────┘
                          │ HTTP Push (tokens + cookies + models)
                          │
┌─────────────────────────│────────────────────────────────────┐
│              Windows VPS (浏览器节点)                         │
│                         │                                    │
│   ┌─────────┐  ┌─────────┐  ┌─────────┐                    │
│   │Firefox 1│  │Firefox 2│  │Firefox 3│  ...                │
│   │Profile A│  │Profile B│  │Profile C│                     │
│   │+Extension│ │+Extension│ │+Extension│                    │
│   │账号 A    │  │账号 B    │  │账号 C    │                    │
│   └─────────┘  └─────────┘  └─────────┘                    │
│                                                             │
│   每个 Firefox 实例:                                         │
│   - 独立 Profile 目录                                        │
│   - 已登录 arena.ai 账号                                     │
│   - 安装了推送扩展                                            │
│   - 自动 mint reCAPTCHA token 并推送到服务器                  │
└─────────────────────────────────────────────────────────────┘
```

## 第一部分：Linux 服务器部署 (Docker)

### 前置要求

- Docker Engine 20.10+
- Docker Compose v2+
- 最低配置：1C1G（纯 Python 服务，资源占用极低）

### 快速部署

```bash
# 1. 克隆项目
git clone <repo-url> arena2api
cd arena2api

# 2. 创建配置文件
cp .env.example .env

# 3. 编辑配置
nano .env
```

编辑 `.env` 文件，至少配置以下项：

```ini
# 必填：API Key（多个用逗号分隔）
API_KEYS=sk-your-key-1,sk-your-key-2

# 推荐：扩展推送密钥（防止未授权的扩展推送数据）
EXTENSION_SECRET=your-secret-here

# 可选：调整 token 池大小
TOKEN_POOL_MAX=30
```

```bash
# 4. 启动服务
docker compose up -d

# 5. 查看日志
docker compose logs -f

# 6. 验证运行
curl http://localhost:9090/health
```

### 防火墙配置

服务器默认监听 9090 端口（可通过 `PORT` 环境变量修改），需要在防火墙中开放该端口：

```bash
# 开放 API 端口（供用户和扩展访问）
ufw allow 9090/tcp
ufw allow 22/tcp    # SSH
```

启动后直接通过 `http://<服务器IP>:9090` 访问即可。

### （可选）反向代理 (Nginx)

如果你希望使用域名 + HTTPS 访问，可以配置 Nginx 反向代理：

```nginx
server {
    listen 443 ssl http2;
    server_name api.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:9090;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 支持（关键！）
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding off;
        proxy_read_timeout 300s;
    }
}
```

> **重要**：使用反代时必须关闭 `proxy_buffering` 和设置长超时，否则 SSE 流式响应会被截断。
> 配置反代后，防火墙可以只开放 443 端口而关闭 9090 端口。

### 管理命令

```bash
# 查看服务状态
docker compose ps

# 重启服务
docker compose restart

# 停止服务
docker compose down

# 查看实时日志
docker compose logs -f --tail=100

# 更新部署
git pull
docker compose build --no-cache
docker compose up -d
```

### 管理 API

```bash
# 查看所有 Profile 状态
curl http://localhost:9090/admin/status

# 查看指定 Profile 详情
curl http://localhost:9090/admin/profile/fp_abc12345

# 健康检查
curl http://localhost:9090/health
```

响应示例：
```json
{
  "total_profiles": 3,
  "active_profiles": 3,
  "total_valid_tokens": 75,
  "profiles": {
    "fp_abc12345": {
      "valid_tokens": 28,
      "total_tokens": 30,
      "has_cookies": true,
      "has_auth_token": true,
      "model_count": 12,
      "last_push_ago_s": 15.2,
      "active": true,
      "health_score": 92.5
    }
  }
}
```

---

## 第二部分：Windows VPS 浏览器节点部署

### 前置要求

- Windows 10/11 或 Windows Server 2019+
- Firefox 浏览器（推荐 ESR 版本）
- 2C4G 配置可运行 8-10 个 Firefox 实例

### 步骤 1：安装 Firefox

下载 Firefox ESR：https://www.mozilla.org/firefox/enterprise/

### 步骤 2：创建 Profile 目录

```cmd
# 编辑 scripts\setup_profiles.bat 中的实例数量（默认 8 个）
# 然后运行：
scripts\setup_profiles.bat
```

这会在 `C:\arena2api-profiles\` 下创建 8 个独立的 Firefox Profile 目录，每个都配置了内存优化参数。

### 步骤 3：安装扩展

需要手动为每个 Profile 安装扩展。首次运行时：

1. 启动单个 Firefox 实例：
   ```cmd
   "C:\Program Files\Mozilla Firefox\firefox.exe" -profile "C:\arena2api-profiles\profile-1" -no-remote -new-instance
   ```

2. 打开 `about:debugging#/runtime/this-firefox`

3. 点击 "Load Temporary Add-on..."

4. 选择 `extension-firefox\manifest.json`

5. 扩展图标会出现在工具栏

6. 点击扩展图标，配置：
   - **Server URL**：填写 Linux 服务器地址，如 `https://api.yourdomain.com`
   - **Profile ID**：每个实例填不同的 ID（或留空自动生成）
   - **Extension Secret**：填写与服务器 `.env` 中相同的 `EXTENSION_SECRET`

7. 导航到 `https://lmarena.ai/` 并登录账号

8. 扩展会自动开始 mint token 并推送到服务器

9. 关闭浏览器，对下一个 Profile 重复步骤 1-8

> **提示**：Firefox 临时扩展在浏览器重启后会丢失。如需永久安装，请使用 `about:config` 设置 `xpinstall.signatures.required` 为 `false`，然后通过 `about:addons` 从文件安装（需要先将扩展打包为 .xpi）。

### 步骤 4：打包扩展为 XPI（推荐）

为了让扩展在浏览器重启后仍然存在：

```cmd
cd extension-firefox
# 使用 7-Zip 或其他工具将所有文件打包为 .xpi（实际是 .zip）
# 确保 manifest.json 在压缩包根目录
```

然后在每个 Profile 中：
1. `about:config` → `xpinstall.signatures.required` → `false`
2. `about:addons` → 齿轮图标 → "Install Add-on From File..." → 选择 .xpi 文件

### 步骤 5：批量启动

```cmd
# 编辑 scripts\start_browsers.bat 中的配置
# 确保 FIREFOX_PATH 和 PROFILE_BASE 正确
# 然后运行：
scripts\start_browsers.bat
```

所有 Firefox 实例会依次启动（间隔 8 秒），每个实例都会自动：
- 加载独立的 Profile
- 恢复上次的标签页（lmarena.ai）
- 扩展自动开始工作

### 步骤 6：验证

在 Linux 服务器上检查 Profile 状态：

```bash
curl http://localhost:9090/admin/status
```

应该能看到所有 Windows 端的 Firefox Profile 都显示为 active。

### 停止所有浏览器

```cmd
scripts\stop_browsers.bat
```

---

## 第三部分：用户使用

### API 兼容性

Arena2API 提供标准 OpenAI API 格式，可直接在以下工具中使用：

- **ChatGPT-Next-Web** / **LobeChat** 等 Web UI
- **Cursor** / **Continue** 等 IDE 插件
- 任何支持 OpenAI API 的应用

### 配置示例

服务器默认监听 `PORT` 端口（默认 9090），直接通过 `IP:端口` 访问：

```
API Base URL: http://<你的服务器IP>:9090/v1
API Key: sk-your-key-1
```

> 如果配置了反向代理（见第一部分 Nginx 配置），也可以使用域名：
> `https://api.yourdomain.com/v1`

### 请求示例

```bash
# 直接通过 IP + 端口访问
curl http://<你的服务器IP>:9090/v1/chat/completions \
  -H "Authorization: Bearer sk-your-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

### 查看可用模型

```bash
curl http://<你的服务器IP>:9090/v1/models \
  -H "Authorization: Bearer sk-your-key-1"
```

---

## 第四部分：运维指南

### 内存优化（Windows VPS）

每个 Firefox 实例的 `user.js` 已经配置了以下优化（由 `setup_profiles.bat` 自动设置）：

| 配置项 | 值 | 说明 |
|--------|-----|------|
| `browser.cache.disk.enable` | false | 禁用磁盘缓存 |
| `browser.cache.memory.capacity` | 16384 KB | 限制内存缓存 16MB |
| `browser.sessionhistory.max_entries` | 3 | 限制历史记录条目 |
| `dom.ipc.processCount` | 1 | 限制内容进程数 |
| `gfx.webrender.all` | false | 禁用 GPU 渲染 |
| `media.autoplay.enabled` | false | 禁止自动播放 |
| `permissions.default.image` | 2 | 禁止加载图片 |

### 2C4G VPS 容量参考

| 浏览器 | 每实例内存 | 推荐实例数 | 可用 Token 池 |
|--------|-----------|-----------|--------------|
| Firefox（优化后） | ~300MB | 8-10 | 240-300 |
| Chrome | ~500MB | 5-6 | 150-180 |

### 常见问题排查

#### 1. 扩展无法推送到服务器

- 检查 Server URL 是否正确（包含 `https://`）
- 检查服务器防火墙是否开放端口
- 检查 EXTENSION_SECRET 是否匹配
- 查看浏览器控制台（F12）是否有错误信息

#### 2. Token 池总是为空

- 确认 Firefox 已登录 lmarena.ai
- 确认页面上 reCAPTCHA 能正常加载
- 检查扩展 popup 中的统计信息（Minted / Pushed 计数）
- 如果 Minted 为 0，可能是 reCAPTCHA 未正确加载

#### 3. 请求返回 503

- 所有 Profile 的 token 都已用完
- 检查 `/admin/status` 认有活跃的 Profile
- 等待扩展推送新的 token

#### 4. 请求返回 401

- API Key 不正确
- 检查 `.env` 中的 `API_KEYS` 配置

#### 5. SSE 流式响应被截断

- Nginx 需要关闭 `proxy_buffering`
- 确认 `proxy_read_timeout` 设置足够长（至少 300s）

### 监控建议

建议设置定时健康检查：

```bash
# crontab 每 5 分钟检查一次
*/5 * * * * curl -sf http://localhost:9090/health > /dev/null || echo "arena2api down" | mail -s "Alert" admin@example.com
```

或使用 Uptime Kuma 等监控工具监控 `/health` 端点。