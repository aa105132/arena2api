# Arena2API

将 [arena.ai](https://arena.ai) 的 300+ 模型通过 **OpenAI 兼容 API** 代理出来。

## 工作原理

```
Chrome 扩展                      Python 服务器
(在 arena.ai 页面运行)           (本地 :9090)
                                     
┌─────────────────┐             ┌──────────────┐
│ 1. 获取 reCAPTCHA│  push ──>  │ 接收 token   │
│    V3 token     │             │ 接收 cookies │
│ 2. 收集 cookies │             │ 接收 models  │
│ 3. 提取模型列表  │             │              │
└─────────────────┘             │ OpenAI API   │
                                │ /v1/chat/... │
                                │ /v1/models   │
                                └──────┬───────┘
                                       │
                                       ▼
                                  arena.ai API
                                (带真实 token)
```

**核心思路**：Chrome 扩展在用户的真实浏览器中运行，自动获取 reCAPTCHA token（高评分）和 cookies，推送给本地 Python 服务器。服务器将 OpenAI 格式的请求转换为 arena.ai 格式，使用扩展提供的 token 调用 API。

## 快速开始

### 1. 启动服务器

```bash
pip install -r requirements.txt
python server.py
```

服务器默认监听 `http://localhost:9090`。

### 2. 安装 Chrome 扩展

1. 打开 Chrome，进入 `chrome://extensions/`
2. 开启右上角的 **开发者模式**
3. 点击 **加载已解压的扩展程序**
4. 选择 `extension/` 目录

### 3. 打开 Arena.ai

1. 打开 https://arena.ai/?mode=direct
2. 等待页面完全加载（扩展会自动获取 token 和 cookies）
3. 点击扩展图标查看状态，确认 Server 为 Connected

### 4. 使用 API

```bash
# 列出模型
curl http://localhost:9090/v1/models

# 聊天（非流式）
curl http://localhost:9090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "GPT-4o",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'

# 聊天（流式）
curl http://localhost:9090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Claude 3.5 Sonnet",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

### 在 OpenAI SDK 中使用

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:9090/v1",
    api_key="not-needed",
)

response = client.chat.completions.create(
    model="GPT-4o",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=True,
)

for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

## 配置

### 服务器

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `PORT` | `9090` | 服务器端口 |
| `DEBUG` | - | 设置任意值开启调试日志 |

### 扩展

在扩展弹窗中可以修改 Server URL（默认 `http://127.0.0.1:9090`）。

## 文件结构

```
arena2api/
├── server.py              # Python 代理服务器
├── requirements.txt       # Python 依赖
├── extension/             # Chrome 扩展
│   ├── manifest.json      # 扩展清单
│   ├── background.js      # Service Worker（token 管理、推送）
│   ├── content.js         # Content Script（消息桥梁）
│   ├── injector.js        # MAIN World Script（reCAPTCHA 调用）
│   ├── popup.html         # 弹窗 UI
│   ├── popup.js           # 弹窗逻辑
│   └── icons/             # 图标
└── README.md
```

## 注意事项

- **需要保持 arena.ai 标签页打开**：扩展需要在 arena.ai 页面中运行来获取 reCAPTCHA token
- **Token 有效期约 2 分钟**：扩展会自动刷新，但如果长时间没有请求，可能需要等待新 token
- **免费使用**：arena.ai 本身是免费的，本工具只是格式转换
- **模型名称**：使用 arena.ai 的模型名称（如 `GPT-4o`、`Claude 3.5 Sonnet`），通过 `/v1/models` 查看完整列表
