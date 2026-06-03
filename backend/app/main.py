from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import os

from app.api.routes import router
from app.config import settings

app = FastAPI(
    title="UnipusHelper Pro",
    description="U校园AI自动答题服务",
    version="1.0.0",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # HSTS 只在实际通过 HTTPS 访问时添加（检查反向代理传递的协议头）
        proto = request.headers.get("X-Forwarded-Proto", "").lower()
        if settings.USE_HTTPS and proto == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """基于内存的简单速率限制中间件（按客户端IP）"""

    def __init__(self, app, limit: int = 60, window: int = 60):
        super().__init__(app)
        self.limit = limit
        self.window = window
        self._store = {}

    async def dispatch(self, request: Request, call_next):
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)

        path = request.url.path
        # 对敏感端点应用更严格的限制
        strict_paths = {
            "/api/auth/login": (10, 60),
            "/api/auth/register": (5, 3600),
            "/api/tasks": (10, 60),
        }
        limit, window = strict_paths.get(path, (self.limit, self.window))

        client_ip = request.headers.get("X-Forwarded-For", request.client.host).split(",")[0].strip()
        key = f"{client_ip}:{path}"
        now = __import__("time").time()

        # 清理过期记录
        self._store = {k: v for k, v in self._store.items() if v["reset_at"] > now}

        bucket = self._store.get(key, {"count": 0, "reset_at": now + window})
        if bucket["count"] >= limit:
            return JSONResponse(
                status_code=429,
                content={"detail": f"请求过于频繁，请 {int(bucket['reset_at'] - now)} 秒后再试"},
                headers={"Retry-After": str(int(bucket["reset_at"] - now))},
            )
        bucket["count"] += 1
        self._store[key] = bucket

        return await call_next(request)


# Security headers
app.add_middleware(SecurityHeadersMiddleware)

# Rate limiting
app.add_middleware(RateLimitMiddleware, limit=60, window=60)

# Trusted host
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

# CORS — 限制为明确配置的来源
_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
if not _origins:
    _origins = ["*"] if settings.DEBUG else []

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)

# API 路由
app.include_router(router, prefix="/api")

# 静态文件
frontend_path = os.path.join(os.path.dirname(__file__), "../frontend")
if os.path.exists(frontend_path):
    app.mount("/static", StaticFiles(directory=frontend_path), name="static")


@app.get("/")
def root():
    index_path = os.path.join(frontend_path, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "UnipusHelper Pro API", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok"}
