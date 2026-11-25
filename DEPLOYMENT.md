# Deployment Information

## Public URL

https://day14-production-agent-sqtk.onrender.com

Agent Part 6 (`06-lab-complete/`, image đã đạt `PASS 12/12`), deploy từ commit `43ec167`.

> Gói free của Render tự ngủ sau ~15 phút không có request. Lần gọi đầu sau khi ngủ mất 30–60 giây để khởi động lại, sau đó trả lời bình thường.

## Platform

**Render**, deploy bằng Blueprint [`06-lab-complete/render.yaml`](06-lab-complete/render.yaml) (Blueprint Path = `06-lab-complete/render.yaml`). Blueprint tạo 2 tài nguyên:

| Tài nguyên | Loại | Ghi chú |
|---|---|---|
| `day14-production-agent` | Web Service, runtime Docker, `rootDir: 06-lab-complete`, region Singapore, plan free | Build từ `Dockerfile` multi-stage; `healthCheckPath: /health`; `CMD` lắng nghe theo `PORT` do Render cấp |
| `day14-redis` | Key Value (Redis), plan free | Chỉ cho kết nối nội bộ (`ipAllowList: []`); dùng cho rate limit và cost guard |

Railway (`06-lab-complete/railway.toml`) cũng đã cấu hình xong, nhưng tài khoản Railway hết bản dùng thử ("Your trial has expired") nên không tạo được project. So sánh hai file cấu hình nằm ở `MISSION_ANSWERS.md`, mục Part 3.

## Test Commands

Đặt `BASE=https://day14-production-agent-sqtk.onrender.com` và `AGENT_API_KEY` bằng giá trị trong tab Environment của service trên Render. Key không được ghi trong repo.

### Health check

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS "$BASE/health"
# {"status":"ok","environment":"production"}
```

### Readiness (Redis trên Render)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS "$BASE/ready"
# {"status":"ready","redis":"ok"}
```

### API test (with authentication)

```bash
# Không có key → 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -d '{"question":"Hello"}'                              # 401

# Có key → 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200

curl -sS -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" \
  -d '{"user_id":"test","question":"What is deployment?"}'
# {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","cost_usd":1.005e-05}
```

### Rate limit (10 request / 60 giây cho mỗi key, state trong Redis)

```bash
for i in $(seq 1 12); do curl -sS -o /dev/null -w '%{http_code} ' -X POST "$BASE/ask" \
  -H "X-API-Key: $AGENT_API_KEY" -H 'Content-Type: application/json' -d '{"question":"test"}'; done
# 200 200 200 200 200 200 200 429 429 429 429 429     (3 request trước đó cũng nằm trong cùng cửa sổ 60 s)
# HTTP/1.1 429 Too Many Requests
# retry-after: 42
# {"detail":"Rate limit exceeded: 10 requests per 60s"}
```

### Kết quả chạy ngày 2026-11-25 12:19 UTC

| Kiểm tra | Mong đợi | Thực tế |
|---|---|---|
| `GET /health` | 200 | ✅ 200 |
| `GET /ready` | 200 | ✅ 200 (`redis: ok`) |
| `POST /ask` không có key | 401 | ✅ 401 |
| `POST /ask` có key | 200 | ✅ 200 |
| Vượt rate limit | 429 + `Retry-After` | ✅ 429, `retry-after: 42` |
| `python check_production_ready.py --public-url $BASE` | ✅ | ✅ `Public URL: GET .../health → 200` |

## Environment Variables Set

Đặt trên Render, không có trong code hay trong repo:

| Biến | Giá trị / nguồn |
|---|---|
| `AGENT_API_KEY` | `generateValue: true`: Render tự sinh chuỗi ngẫu nhiên, xem trong tab Environment |
| `REDIS_URL` | `fromService` → `connectionString` của Key Value `day14-redis` (URL nội bộ) |
| `ENVIRONMENT` | `production` |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `MONTHLY_BUDGET_USD` | `10.0` |
| `PORT` | Render tự cấp; `CMD` trong Dockerfile dùng `${PORT:-8000}` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png): log deploy `==> Your service is live`, URL chính, các dòng log JSON của agent
- [Test results](screenshots/test.png): 4 lệnh CP6 và vòng lặp rate limit (chỉ in mã HTTP, không lộ key)
