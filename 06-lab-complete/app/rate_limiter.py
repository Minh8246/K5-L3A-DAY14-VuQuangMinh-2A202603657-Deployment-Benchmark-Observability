"""
Rate limiting (tiêu chí 7).

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window)
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State lưu trong Redis (sorted set, score = timestamp) nên mọi worker / instance dùng
    chung và vẫn còn sau khi restart agent
"""
import hashlib
import math
import time
import uuid

from fastapi import HTTPException, status

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _bucket(key: str) -> str:
    # Không lưu API key dạng plain text trong Redis
    return "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()[:16]


def check_rate_limit(key: str) -> None:
    limit = settings.rate_limit_per_minute
    bucket = _bucket(key)
    now = time.time()

    # ZREMRANGEBYSCORE → ZADD → ZCARD → EXPIRE trong 1 transaction (MULTI/EXEC):
    # thêm request hiện tại TRƯỚC khi đếm để 2 worker không cùng lọt qua ở request thứ limit+1.
    member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"
    pipe = r.pipeline(transaction=True)
    pipe.zremrangebyscore(bucket, 0, now - WINDOW_SECONDS)
    pipe.zadd(bucket, {member: now})
    pipe.zcard(bucket)
    pipe.expire(bucket, WINDOW_SECONDS + 1)
    _, _, count, _ = pipe.execute()

    if count > limit:
        r.zrem(bucket, member)  # request bị chặn không tính vào cửa sổ
        oldest = r.zrange(bucket, 0, 0, withscores=True)
        retry_after = WINDOW_SECONDS
        if oldest:
            retry_after = max(1, math.ceil(oldest[0][1] + WINDOW_SECONDS - now))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {limit} requests per {WINDOW_SECONDS}s",
            headers={"Retry-After": str(retry_after)},
        )
