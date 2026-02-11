"""
arena2api - Arena.ai to OpenAI API Proxy (Multi-Account Edition)
================================================================

多账号架构：每个浏览器 Profile 独立维护 token 池和 cookies，
服务器自动轮询选择最优 Profile 处理请求。

支持：
  - 多 Firefox/Chrome Profile 同时推送 token
  - API Key 认证（多用户共享）
  - 自动负载均衡（Round-Robin + 健康检查）
  - Docker 部署

使用方式：
  1. pip install -r requirements.txt
  2. python server.py
  3. 在各浏览器 Profile 中安装扩展，打开 arena.ai
  4. 在 OpenAI 客户端中配置 http://your-server:9090/v1
"""

import asyncio
import json
import logging
import os
import re
import secrets
import time
import uuid
import hashlib
from typing import Optional, Dict, List
from collections import OrderedDict

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.responses import StreamingResponse, JSONResponse

# ============================================================
# 日志
# ============================================================
logging.basicConfig(
    level=logging.DEBUG if os.environ.get("DEBUG") else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("arena2api")

# ============================================================
# 配置
# ============================================================
PORT = int(os.environ.get("PORT", "9090"))
ARENA_BASE = "https://arena.ai"
ARENA_CREATE_EVAL = f"{ARENA_BASE}/nextjs-api/stream/create-evaluation"
ARENA_POST_EVAL = f"{ARENA_BASE}/nextjs-api/stream/post-to-evaluation"

# API Key 认证
# 设置 API_KEYS 环境变量，逗号分隔多个 key
# 留空则不需要认证
API_KEYS_RAW = os.environ.get("API_KEYS", "").strip()
API_KEYS: set = set()
if API_KEYS_RAW:
    API_KEYS = {k.strip() for k in API_KEYS_RAW.split(",") if k.strip()}

# 扩展推送密钥（可选，防止未授权扩展推送）
EXTENSION_SECRET = os.environ.get("EXTENSION_SECRET", "").strip()

# Token 池配置
TOKEN_POOL_MAX = int(os.environ.get("TOKEN_POOL_MAX", "30"))  # 每个 Profile 最多缓存 token
TOKEN_EXPIRY_MS = int(os.environ.get("TOKEN_EXPIRY_MS", "110000"))  # token 过期时间 ms
PROFILE_TIMEOUT_S = int(os.environ.get("PROFILE_TIMEOUT_S", "120"))  # Profile 不活跃超时

# Prompt 调试
PROMPT_DEBUG = os.environ.get("PROMPT_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
PROMPT_PREVIEW_CHARS = int(os.environ.get("PROMPT_PREVIEW_CHARS", "1200"))
PROMPT_DEBUG_TOKEN = os.environ.get("PROMPT_DEBUG_TOKEN", "").strip()

# reCAPTCHA
RECAPTCHA_V3_SITEKEY = "6Led_uYrAAAAAKjxDIF58fgFtX3t8loNAK85bW9I"

# ============================================================
# UUIDv7
# ============================================================
def uuid7() -> str:
    ts = int(time.time() * 1000)
    ra = secrets.randbits(12)
    rb = secrets.randbits(62)
    u = ts << 80 | (0x7000 | ra) << 64 | (0x8000000000000000 | rb)
    h = f"{u:032x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


# ============================================================
# Token / Cookie Store（每个 Profile 一个实例）
# ============================================================
class ProfileStore:
    """单个浏览器 Profile 的状态存储"""

    def __init__(self, profile_id: str):
        self.profile_id = profile_id
        self.cookies: dict = {}
        self.auth_token: str = ""
        self.cf_clearance: str = ""
        self.v3_tokens: list = []  # [{token, action, ts}]
        self.v2_token: Optional[dict] = None
        self.last_push: float = 0
        self.models: list = []
        self.text_models: dict = {}  # publicName -> id
        self.image_models: dict = {}
        self.vision_models: list = []
        self.next_actions: dict = {}
        self.total_tokens_served: int = 0  # 已消耗的 token 总数
        self.total_tokens_received: int = 0  # 收到的 token 总数
        self.last_token_mint: float = 0  # 上次收到 token 的时间
        self.consecutive_empty: int = 0  # 连续无 token 的请求次数

    @property
    def active(self) -> bool:
        return self.last_push > 0 and (time.time() - self.last_push < PROFILE_TIMEOUT_S)

    @property
    def token_count(self) -> int:
        """当前可用 token 数量（不含过期的）"""
        now = time.time()
        return len([t for t in self.v3_tokens if (now * 1000 - t["ts"]) < TOKEN_EXPIRY_MS])

    @property
    def health_score(self) -> float:
        """
        Profile 健康评分 (0-100)
        用于选择最优 Profile 处理请求
        """
        if not self.active:
            return 0.0

        score = 0.0
        # 有 token 加分（最重要）
        tc = self.token_count
        score += min(tc * 15, 45)  # 最多 45 分

        # 有 auth token 加分
        if self.auth_token:
            score += 20

        # 有 cf_clearance 加分
        if self.cf_clearance:
            score += 10

        # 有 models 加分
        if self.text_models:
            score += 10

        # 最近推送加分（越新越好）
        age = time.time() - self.last_push
        if age < 30:
            score += 15
        elif age < 60:
            score += 10
        elif age < 90:
            score += 5

        return min(score, 100.0)

    def push(self, data: dict):
        self.last_push = time.time()
        if data.get("cookies"):
            self.cookies = data["cookies"]
        if data.get("auth_token"):
            self.auth_token = data["auth_token"]
        if data.get("cf_clearance"):
            self.cf_clearance = data["cf_clearance"]

        # V3 tokens
        if data.get("v3_tokens"):
            for t in data["v3_tokens"]:
                tok = t.get("token", "")
                if not tok or len(tok) < 20:
                    continue
                age = t.get("age_ms", 0)
                if age > TOKEN_EXPIRY_MS:
                    continue
                if any(x["token"] == tok for x in self.v3_tokens):
                    continue
                self.v3_tokens.append({
                    "token": tok,
                    "action": t.get("action", "chat_submit"),
                    "ts": time.time() * 1000 - age,  # 绝对时间戳 ms
                })
                self.total_tokens_received += 1
                self.last_token_mint = time.time()
            # 按时间排序，保留最新的
            self.v3_tokens.sort(key=lambda x: x["ts"])
            while len(self.v3_tokens) > TOKEN_POOL_MAX:
                self.v3_tokens.pop(0)

        # V2 token
        if data.get("v2_token"):
            v2 = data["v2_token"]
            if v2.get("token") and v2.get("age_ms", 0) < TOKEN_EXPIRY_MS:
                self.v2_token = {
                    "token": v2["token"],
                    "ts": time.time() - v2.get("age_ms", 0) / 1000,
                }

        # Models
        if data.get("models"):
            self._update_models(data["models"])

        # Next actions
        if data.get("next_actions"):
            self.next_actions.update(data["next_actions"])

    def _update_models(self, models: list):
        self.models = models
        self.text_models = {}
        self.image_models = {}
        self.vision_models = []
        for m in models:
            name = m.get("publicName", "")
            mid = m.get("id", "")
            caps = m.get("capabilities", {})
            out_caps = caps.get("outputCapabilities", [])
            in_caps = caps.get("inputCapabilities", [])
            if "text" in out_caps:
                self.text_models[name] = mid
            if "image" in out_caps:
                self.image_models[name] = mid
            if "image" in in_caps:
                self.vision_models.append(name)

    def pop_v3_token(self) -> Optional[str]:
        now = time.time() * 1000
        self.v3_tokens = [t for t in self.v3_tokens if now - t["ts"] < TOKEN_EXPIRY_MS]
        if not self.v3_tokens:
            self.consecutive_empty += 1
            return None
        self.consecutive_empty = 0
        self.total_tokens_served += 1
        return self.v3_tokens.pop(0)["token"]

    def pop_v2_token(self) -> Optional[str]:
        if not self.v2_token:
            return None
        if time.time() - self.v2_token["ts"] > TOKEN_EXPIRY_MS / 1000:
            self.v2_token = None
            return None
        tok = self.v2_token["token"]
        self.v2_token = None
        return tok

    def build_cookie_header(self) -> str:
        parts = []
        for k, v in self.cookies.items():
            parts.append(f"{k}={v}")
        return "; ".join(parts)

    def clean_expired_tokens(self):
        """清理过期 token"""
        now = time.time() * 1000
        before = len(self.v3_tokens)
        self.v3_tokens = [t for t in self.v3_tokens if now - t["ts"] < TOKEN_EXPIRY_MS]
        cleaned = before - len(self.v3_tokens)
        if cleaned > 0:
            log.debug(f"Profile {self.profile_id}: cleaned {cleaned} expired tokens")

    def status(self) -> dict:
        now = time.time()
        self.clean_expired_tokens()
        return {
            "profile_id": self.profile_id,
            "active": self.active,
            "health_score": round(self.health_score, 1),
            "last_push_ago": round(now - self.last_push, 1) if self.last_push else None,
            "v3_tokens": self.token_count,
            "has_v2": bool(self.v2_token and now - self.v2_token["ts"] < TOKEN_EXPIRY_MS / 1000),
            "has_auth": bool(self.auth_token),
            "has_cf": bool(self.cf_clearance),
            "text_models": len(self.text_models),
            "image_models": len(self.image_models),
            "total_tokens_served": self.total_tokens_served,
            "total_tokens_received": self.total_tokens_received,
            "cookies": list(self.cookies.keys()),
        }


# ============================================================
# Multi-Store Manager
# ============================================================
class StoreManager:
    """管理多个 Profile Store，提供轮询和最优选择"""

    def __init__(self):
        self.stores: Dict[str, ProfileStore] = OrderedDict()
        self._robin_index: int = 0
        self._lock = asyncio.Lock()

    def get_or_create(self, profile_id: str) -> ProfileStore:
        if profile_id not in self.stores:
            self.stores[profile_id] = ProfileStore(profile_id)
            log.info(f"New profile registered: {profile_id}")
        return self.stores[profile_id]

    def get_active_profiles(self) -> List[ProfileStore]:
        """获取所有活跃的 Profile"""
        return [s for s in self.stores.values() if s.active]

    def get_all_models(self) -> dict:
        """合并所有 Profile 的模型列表"""
        text_models = {}
        image_models = {}
        for s in self.stores.values():
            if s.active:
                text_models.update(s.text_models)
                image_models.update(s.image_models)
        return {"text": text_models, "image": image_models}

    async def select_best_profile(self, model_name: str = "") -> Optional[ProfileStore]:
        """
        选择最优 Profile 处理请求
        策略：
        1. 优先选有 token 的
        2. 其次按 health_score 排序
        3. 相同分数时 Round-Robin
        """
        active = self.get_active_profiles()
        if not active:
            return None

        # 如果指定了模型，过滤支持该模型的 Profile
        if model_name:
            candidates = [
                s for s in active
                if model_name in s.text_models or model_name in s.image_models
            ]
            if not candidates:
                candidates = active  # 回退到所有活跃 Profile

        else:
            candidates = active

        # 按 health_score 排序
        candidates.sort(key=lambda s: s.health_score, reverse=True)

        # 有 token 的优先
        with_tokens = [s for s in candidates if s.token_count > 0]
        if with_tokens:
            # Round-Robin 在有 token 的 Profile 中选择
            async with self._lock:
                self._robin_index = (self._robin_index + 1) % len(with_tokens)
                return with_tokens[self._robin_index % len(with_tokens)]

        # 没有 token 的，选 health_score 最高的
        return candidates[0] if candidates else None

    def resolve_model(self, model_name: str) -> tuple:
        """
        解析模型名，返回 (model_id, model_name, is_image)
        会在所有活跃 Profile 中查找
        """
        all_models = self.get_all_models()
        all_text = all_models["text"]
        all_image = all_models["image"]

        # 精确匹配
        if model_name in all_text:
            return all_text[model_name], model_name, False
        if model_name in all_image:
            return all_image[model_name], model_name, True

        # 模糊匹配
        combined = {**all_text, **all_image}
        for name, mid in combined.items():
            if model_name.lower() in name.lower() or name.lower() in model_name.lower():
                is_img = name in all_image
                return mid, name, is_img

        return None, model_name, False

    def global_status(self) -> dict:
        active = self.get_active_profiles()
        total_tokens = sum(s.token_count for s in active)
        total_served = sum(s.total_tokens_served for s in self.stores.values())
        total_received = sum(s.total_tokens_received for s in self.stores.values())
        all_models = self.get_all_models()
        return {
            "total_profiles": len(self.stores),
            "active_profiles": len(active),
            "total_tokens_available": total_tokens,
            "total_tokens_served": total_served,
            "total_tokens_received": total_received,
            "text_models": len(all_models["text"]),
            "image_models": len(all_models["image"]),
            "profiles": [s.status() for s in self.stores.values()],
        }

    def clean_all(self):
        """清理所有 Profile 的过期 token"""
        for s in self.stores.values():
            s.clean_expired_tokens()


manager = StoreManager()
last_prompt_debug: dict = {}


# ============================================================
# API Key 认证
# ============================================================
security = HTTPBearer(auto_error=False)


async def verify_api_key(request: Request):
    """验证 API Key（如果配置了的话）"""
    if not API_KEYS:
        return True  # 未配置则不需要认证

    # 从 Authorization header 提取
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        key = auth[7:].strip()
        if key in API_KEYS:
            return True

    # 从 query parameter 提取
    key = request.query_params.get("api_key", "")
    if key and key in API_KEYS:
        return True

    raise HTTPException(status_code=401, detail="Invalid API key")


def verify_extension_secret(request: Request):
    """验证扩展推送密钥（如果配置了的话）"""
    if not EXTENSION_SECRET:
        return True
    secret = request.headers.get("X-Extension-Secret", "")
    if secret == EXTENSION_SECRET:
        return True
    raise HTTPException(status_code=401, detail="Invalid extension secret")


# ============================================================
# FastAPI
# ============================================================
app = FastAPI(title="arena2api", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 扩展端点
# ============================================================
@app.post("/v1/extension/push")
async def extension_push(request: Request):
    """接收扩展推送的 token、cookies、models"""
    verify_extension_secret(request)
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    # 从请求中提取 profile_id（扩展需要在推送时带上）
    profile_id = str(data.get("profile_id", "")).strip()
    if not profile_id:
        # 兼容旧版扩展：用 auth_token 的 hash 作为 profile_id
        auth = str(data.get("auth_token", "")).strip()
        if auth:
            profile_id = "auto_" + hashlib.md5(auth.encode()).hexdigest()[:8]
        else:
            profile_id = "default"

    store = manager.get_or_create(profile_id)
    store.push(data)

    # 计算是否需要更多 token
    need = store.token_count < (TOKEN_POOL_MAX // 2)

    return {
        "status": "ok",
        "profile_id": profile_id,
        "need_tokens": need,
        "v3_count": store.token_count,
        "pool_max": TOKEN_POOL_MAX,
    }


@app.get("/v1/extension/status")
@app.get("/api/v1/extension/status")
async def extension_status():
    return manager.global_status()


@app.get("/v1/extension/profiles")
@app.get("/api/v1/extension/profiles")
async def extension_profiles():
    """列出所有 Profile 的详细状态"""
    return {
        "profiles": [s.status() for s in manager.stores.values()],
        "total": len(manager.stores),
        "active": len(manager.get_active_profiles()),
    }


# ============================================================
# OpenAI 兼容端点
# ============================================================
@app.get("/v1/models")
@app.get("/api/v1/models")
async def list_models(request: Request):
    """列出可用模型"""
    if API_KEYS:
        await verify_api_key(request)

    all_models = manager.get_all_models()
    combined = {}
    combined.update(all_models["text"])
    combined.update(all_models["image"])

    data = []
    for name in sorted(combined.keys()):
        data.append({
            "id": name,
            "object": "model",
            "created": 0,
            "owned_by": "arena.ai",
        })

    if not data:
        data.append({
            "id": "waiting-for-extension",
            "object": "model",
            "created": 0,
            "owned_by": "arena.ai",
        })

    return {"object": "list", "data": data}


def detect_client(request: Request) -> str:
    """检测客户端类型"""
    ua = request.headers.get("user-agent", "").lower()
    if "claude" in ua or "anthropic" in ua:
        return "claude"
    if "gemini" in ua or "google" in ua:
        return "gemini"
    if "codex" in ua:
        return "codex"
    if "opencode" in ua:
        return "opencode"
    return "openai"


def preview_text(text: str, limit: int = 200) -> str:
    clean = str(text or "").replace("\r", "\\r").replace("\n", "\\n")
    if len(clean) <= limit:
        return clean
    return clean[:limit] + "...(truncated)"


def extract_message_text(content) -> str:
    """提取 OpenAI message 的文本内容"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text.strip()
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                if item.strip():
                    parts.append(item.strip())
                continue
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in {"text", "input_text", "output_text"}:
                text = item.get("text", "")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
                continue
            if item_type in {"image_url", "input_image"}:
                image_obj = item.get("image_url") or item.get("url")
                if isinstance(image_obj, dict):
                    image_url = image_obj.get("url", "")
                else:
                    image_url = str(image_obj or "")
                if image_url:
                    parts.append(f"[image] {image_url}")
                continue
            if item_type:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts).strip()
    return str(content).strip()


def build_conversation_prompt(messages: list) -> str:
    """把 OpenAI messages 打包为 arena.ai 可接受的单文本上下文"""
    packed = []
    single_user_text = ""
    single_user_only = len(messages) == 1 and messages[0].get("role") == "user"

    for msg in messages:
        role = str(msg.get("role", "user")).lower()
        content = extract_message_text(msg.get("content", ""))
        if not content:
            continue
        if single_user_only:
            single_user_text = content
            continue
        if role not in {"system", "developer", "user", "assistant", "tool"}:
            role = "user"
        packed.append(f"<|{role}|>\n{content}")

    if single_user_only:
        return single_user_text

    return "\n\n".join(packed).strip()


@app.get("/v1/debug/last-prompt")
@app.get("/api/v1/debug/last-prompt")
async def debug_last_prompt(request: Request):
    if not PROMPT_DEBUG:
        raise HTTPException(404, "Prompt debug disabled")
    if PROMPT_DEBUG_TOKEN:
        token = request.headers.get("x-debug-token", "")
        if token != PROMPT_DEBUG_TOKEN:
            raise HTTPException(401, "Invalid debug token")
    if not last_prompt_debug:
        return {"status": "empty"}
    return last_prompt_debug


@app.post("/v1/chat/completions")
@app.post("/api/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI 兼容的聊天补全"""
    if API_KEYS:
        await verify_api_key(request)

    log.info(f"[Request] {request.method} {request.url.path}")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    client_type = detect_client(request)
    model_name = body.get("model", "")
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    if not messages:
        raise HTTPException(400, "messages is required")

    # 检查是否有活跃 Profile
    active_profiles = manager.get_active_profiles()
    if not active_profiles:
        raise HTTPException(503, "No browser profiles connected. Install the extension and open arena.ai.")

    # 解析模型
    model_id, model_name, is_image = manager.resolve_model(model_name)
    if not model_id:
        all_models = manager.get_all_models()
        available = sorted(list(all_models["text"].keys()) + list(all_models["image"].keys()))
        raise HTTPException(404, f"Model '{model_name}' not found. Available: {available[:30]}")

    # 选择最优 Profile
    selected = await manager.select_best_profile(model_name)
    if not selected:
        raise HTTPException(503, "No available profile with tokens. Please wait for token refresh.")

    log.info(f"[Route] model={model_name}, profile={selected.profile_id}, "
             f"tokens={selected.token_count}, score={selected.health_score:.1f}")

    # 构建 prompt
    message_debug = []
    for idx, msg in enumerate(messages):
        role = str(msg.get("role", "user")).lower()
        content_text = extract_message_text(msg.get("content", ""))
        message_debug.append({
            "index": idx,
            "role": role,
            "chars": len(content_text),
            "preview": preview_text(content_text, PROMPT_PREVIEW_CHARS),
        })

    if PROMPT_DEBUG:
        roles = [item["role"] for item in message_debug]
        log.info(f"[PromptDebug] client={client_type}, model={model_name}, "
                 f"stream={stream}, messages={len(messages)}, roles={roles}")

    prompt = build_conversation_prompt(messages)
    if not prompt:
        raise HTTPException(400, "messages content is empty")

    global last_prompt_debug
    last_prompt_debug = {
        "ts": int(time.time()),
        "client_type": client_type,
        "model": model_name,
        "profile": selected.profile_id,
        "stream": bool(stream),
        "messages_count": len(messages),
        "messages": message_debug,
        "prompt_chars": len(prompt),
        "prompt_preview": preview_text(prompt, PROMPT_PREVIEW_CHARS),
    }

    # 获取 reCAPTCHA token
    v3_token = selected.pop_v3_token()
    v2_token = selected.pop_v2_token() if not v3_token else None

    # 如果选中的 Profile 没有 token，尝试从其他 Profile 借
    if not v3_token and not v2_token:
        for other in manager.get_active_profiles():
            if other.profile_id == selected.profile_id:
                continue
            v3_token = other.pop_v3_token()
            if v3_token:
                log.info(f"[TokenBorrow] Borrowed token from profile {other.profile_id}")
                break

    modality = "image" if is_image else "chat"

    # 构建 arena.ai 请求
    eval_id = uuid7()
    user_msg_id = uuid7()
    model_a_msg_id = uuid7()

    user_id = selected.cookies.get("arena-user-id", "")
    if not user_id:
        for key, value in selected.cookies.items():
            if "user" in key.lower() and len(value) > 20:
                user_id = value
                break

    arena_payload = {
        "id": eval_id,
        "mode": "direct",
        "modelAId": model_id,
        "userMessageId": user_msg_id,
        "modelAMessageId": model_a_msg_id,
        "userMessage": {
            "content": prompt,
            "experimental_attachments": [],
            "metadata": {},
        },
        "modality": modality,
    }

    if user_id:
        arena_payload["userId"] = user_id

    if v2_token:
        arena_payload["recaptchaV2Token"] = v2_token
        arena_payload["recaptchaV3Token"] = None
    elif v3_token:
        arena_payload["recaptchaV3Token"] = v3_token
    else:
        log.warning(f"[NoToken] profile={selected.profile_id}, sending without reCAPTCHA token")

    # 构建 headers
    headers = {
        "accept": "*/*",
        "content-type": "application/json",
        "origin": ARENA_BASE,
        "referer": f"{ARENA_BASE}/?mode=direct",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "cookie": selected.build_cookie_header(),
    }

    if selected.auth_token:
        headers["authorization"] = f"Bearer {selected.auth_token}"

    url = ARENA_CREATE_EVAL
    log.info(f"[Arena] model={model_name}, eval_id={eval_id}, profile={selected.profile_id}, "
             f"has_v3={bool(v3_token)}, has_v2={bool(v2_token)}")

    if stream:
        return StreamingResponse(
            stream_response(url, arena_payload, headers, model_name, eval_id, client_type, selected.profile_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        return await non_stream_response(url, arena_payload, headers, model_name, eval_id, client_type, selected.profile_id)


async def stream_response(url, payload, headers, model_name, eval_id, client_type="openai", profile_id=""):
    """流式响应生成器"""
    chat_id = f"chatcmpl-{eval_id}"
    created = int(time.time())

    try:
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    log.error(f"[ArenaError] {resp.status_code} profile={profile_id} body={body[:500]}")
                    error_chunk = {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model_name,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": f"[Error: Arena API returned {resp.status_code}]"},
                            "finish_reason": "stop",
                        }],
                    }
                    yield f"data: {json.dumps(error_chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue

                    content = None
                    reasoning = None
                    finish = None

                    if line.startswith("a0:"):
                        try:
                            content = json.loads(line[3:])
                            if content == "hasArenaError":
                                content = "[Arena Error]"
                                finish = "stop"
                        except json.JSONDecodeError:
                            continue
                    elif line.startswith("ag:"):
                        try:
                            reasoning = json.loads(line[3:])
                        except json.JSONDecodeError:
                            continue
                    elif line.startswith("ad:"):
                        finish = "stop"
                        try:
                            data = json.loads(line[3:])
                            if data.get("finishReason"):
                                finish = data["finishReason"]
                        except json.JSONDecodeError:
                            pass
                    elif line.startswith("a2:"):
                        if "heartbeat" in line:
                            continue
                        try:
                            data = json.loads(line[3:])
                            images = [img.get("image") for img in data if img.get("image")]
                            if images:
                                content = "\n".join(f"![image]({url})" for url in images)
                        except json.JSONDecodeError:
                            continue
                    elif line.startswith("a3:"):
                        try:
                            content = f"[Error: {json.loads(line[3:])}]"
                        except Exception:
                            content = f"[Error: {line[3:]}]"
                        finish = "stop"
                    else:
                        continue

                    if content is not None:
                        chunk = {
                            "id": chat_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model_name,
                            "choices": [{
                                "index": 0,
                                "delta": {"content": content},
                                "finish_reason": None,
                            }],
                        }
                        if client_type == "claude":
                            chunk["type"] = "content_block_delta"
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

                    if reasoning is not None:
                        chunk = {
                            "id": chat_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model_name,
                            "choices": [{
                                "index": 0,
                                "delta": {"reasoning_content": reasoning},
                                "finish_reason": None,
                            }],
                        }
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

                    if finish:
                        chunk = {
                            "id": chat_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model_name,
                            "choices": [{
                                "index": 0,
                                "delta": {},
                                "finish_reason": finish if finish != "stop" else "stop",
                            }],
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"
                        yield "data: [DONE]\n\n"
                        return

    except Exception as e:
        log.error(f"[StreamError] profile={profile_id}: {e}")
        error_chunk = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{
                "index": 0,
                "delta": {"content": f"[Stream Error: {e}]"},
                "finish_reason": "stop",
            }],
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
        yield "data: [DONE]\n\n"


async def non_stream_response(url, payload, headers, model_name, eval_id, client_type="openai", profile_id=""):
    """非流式响应"""
    content_parts = []
    reasoning_parts = []
    finish_reason = "stop"
    usage = {}

    try:
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    log.error(f"[ArenaError] {resp.status_code} profile={profile_id} body={body[:500]}")
                    raise HTTPException(resp.status_code, f"Arena API error: {body[:200]}")

                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    if line.startswith("a0:"):
                        try:
                            text = json.loads(line[3:])
                            if isinstance(text, str) and text != "hasArenaError":
                                content_parts.append(text)
                        except json.JSONDecodeError:
                            pass
                    elif line.startswith("ag:"):
                        try:
                            text = json.loads(line[3:])
                            if isinstance(text, str):
                                reasoning_parts.append(text)
                        except json.JSONDecodeError:
                            pass
                    elif line.startswith("ad:"):
                        try:
                            data = json.loads(line[3:])
                            if data.get("finishReason"):
                                finish_reason = data["finishReason"]
                            if data.get("usage"):
                                usage = data["usage"]
                        except json.JSONDecodeError:
                            pass
                    elif line.startswith("a2:"):
                        if "heartbeat" in line:
                            continue
                        try:
                            data = json.loads(line[3:])
                            images = [img.get("image") for img in data if img.get("image")]
                            for img_url in images:
                                content_parts.append(f"![image]({img_url})")
                        except json.JSONDecodeError:
                            pass
                    elif line.startswith("a3:"):
                        try:
                            content_parts.append(f"[Error: {json.loads(line[3:])}]")
                        except Exception:
                            content_parts.append(f"[Error: {line[3:]}]")

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[NonStreamError] profile={profile_id}: {e}")
        raise HTTPException(500, str(e))

    full_content = "".join(content_parts)
    full_reasoning = "".join(reasoning_parts)

    message = {"role": "assistant", "content": full_content}
    if full_reasoning:
        message["reasoning_content"] = full_reasoning

    response = {
        "id": f"chatcmpl-{eval_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
        }],
        "usage": usage or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }

    if client_type == "claude":
        response["type"] = "message"
        response["role"] = "assistant"
        response["content"] = [{"type": "text", "text": full_content}]

    return response


# ============================================================
# 管理端点
# ============================================================
@app.get("/admin/status")
async def admin_status():
    """管理面板 - 全局状态"""
    return manager.global_status()


@app.delete("/admin/profile/{profile_id}")
async def admin_delete_profile(profile_id: str):
    """管理面板 - 删除 Profile"""
    if profile_id in manager.stores:
        del manager.stores[profile_id]
        return {"status": "deleted", "profile_id": profile_id}
    raise HTTPException(404, f"Profile '{profile_id}' not found")


# ============================================================
# 健康检查
# ============================================================
@app.get("/health")
@app.get("/")
async def health():
    active = manager.get_active_profiles()
    total_tokens = sum(s.token_count for s in active)
    return {
        "status": "ok",
        "version": "2.0.0",
        "profiles": len(manager.stores),
        "active_profiles": len(active),
        "total_tokens": total_tokens,
        "api_key_required": bool(API_KEYS),
    }


# ============================================================
# 定期清理
# ============================================================
@app.on_event("startup")
async def startup_cleanup():
    async def _cleanup_loop():
        while True:
            await asyncio.sleep(30)
            manager.clean_all()
    asyncio.create_task(_cleanup_loop())


# ============================================================
# 启动
# ============================================================
if __name__ == "__main__":
    log.info(f"Starting arena2api v2.0.0 (Multi-Account) on port {PORT}")
    log.info(f"OpenAI API: http://localhost:{PORT}/v1")
    log.info(f"API Key required: {bool(API_KEYS)}")
    log.info(f"Extension secret required: {bool(EXTENSION_SECRET)}")
    log.info(f"Token pool max per profile: {TOKEN_POOL_MAX}")
    log.info("Waiting for browser extensions to connect...")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
