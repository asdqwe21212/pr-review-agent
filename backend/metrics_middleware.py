"""Prometheus 指标中间件"""
import time
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# 定义指标
REVIEWS_TOTAL = Counter(
    'pr_reviews_total',
    'Total reviews processed',
    ['status', 'repo']
)

REVIEW_DURATION = Histogram(
    'review_duration_seconds',
    'Review processing time',
    ['repo'],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600]
)

ACTIVE_REVIEWS = Gauge(
    'active_reviews',
    'Currently processing reviews'
)

TOKEN_USAGE = Counter(
    'tokens_used_total',
    'Total tokens consumed',
    ['type']
)

QUEUE_SIZE = Gauge(
    'review_queue_size',
    'Pending reviews in queue'
)

API_REQUESTS = Counter(
    'api_requests_total',
    'Total API requests',
    ['method', 'endpoint', 'status']
)

API_LATENCY = Histogram(
    'api_request_duration_seconds',
    'API request latency',
    ['method', 'endpoint']
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Prometheus 指标收集中间件"""
    
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        response = await call_next(request)
        
        duration = time.time() - start_time
        path = request.url.path
        
        # 只记录 API 请求
        if path.startswith("/api/"):
            API_REQUESTS.labels(
                method=request.method,
                endpoint=path,
                status=response.status_code
            ).inc()
            
            API_LATENCY.labels(
                method=request.method,
                endpoint=path
            ).observe(duration)
        
        return response


def get_metrics_response() -> Response:
    """生成 Prometheus 指标响应"""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )