# Day 14 Lab - Mission Answers

> Môi trường chạy CP1–CP4: Windows 11, Python 3.13 (venv riêng), Docker Desktop 29.4.3 / Compose v5.1.3.
> Exercise 5.2 (SIGTERM) chạy trong container Linux `python:3.11-slim` vì Python Windows không nhận SIGTERM.
> Mọi output bên dưới là output thật của lệnh đã chạy ngày 2026-11-25; token/key đã che.

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found

Đọc `01-localhost-vs-production/develop/app.py`:

| # | Anti-pattern | Dòng | Hậu quả |
|---|---|---|---|
| 1 | **Hardcode secret**: `OPENAI_API_KEY = "sk-hardcoded-..."`, `DATABASE_URL = "postgresql://admin:password123@..."` | 17–18 | Push lên GitHub là lộ key/mật khẩu DB; muốn đổi key phải sửa code và deploy lại |
| 2 | **Log ra secret**: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | 34 | Key nằm trong log, ai đọc được log (log aggregator, đồng nghiệp) đều thấy. Đã xác nhận ở Exercise 1.2: log in ra `Using key: sk-hardcoded-fake-key-never-do-this` |
| 3 | **`print()` thay vì logging có cấu trúc** | 33, 38 | Không có level, timestamp, không parse/lọc được trong Datadog/Loki; log cả nội dung câu hỏi/câu trả lời (có thể chứa dữ liệu cá nhân) |
| 4 | **Config cứng trong code**: `DEBUG = True`, `MAX_TOKENS = 500` | 21–22 | Không đổi được giữa dev/staging/prod mà không sửa code; debug bật cả trên production |
| 5 | **Không có health check** (`/health`, `/ready`) | 42–43 | Platform không biết agent chết hay treo để restart; load balancer không biết khi nào route traffic. Đã xác nhận: `GET /health` → `404 Not Found` |
| 6 | **Bind `host="localhost"`** | 51 | Trong container/cloud, traffic từ ngoài vào không tới được process (chỉ nhận kết nối loopback). Phải bind `0.0.0.0` |
| 7 | **Port cứng `port=8000`** | 52 | Railway/Render inject `PORT` qua env; app không đọc nên platform không route được |
| 8 | **`reload=True` trên production** | 53 | Reloader theo dõi file, tốn tài nguyên, tạo thêm process; không phải chế độ chạy production |
| 9 | **Không xử lý shutdown** | — | Khi platform gửi SIGTERM (deploy mới, scale down), request đang chạy bị cắt ngang |
| 10 | **Input qua query string, không validate** (`def ask_agent(question: str)`) | 31 | Câu hỏi nằm trên URL → bị ghi vào access log/proxy log; không giới hạn độ dài |

### Exercise 1.2: Chạy basic version

```bash
cd 01-localhost-vs-production/develop
pip install -r requirements.txt
python app.py
```

```text
$ curl -X POST "http://localhost:8000/ask?question=Hello"
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}

$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
{"detail":[{"type":"missing","loc":["query","question"],"msg":"Field required","input":null}]}     # 422: bản develop chỉ nhận query

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found
{"detail":"Not Found"}
```

Log của server (bằng chứng secret bị log ra):

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [19140] using WatchFiles
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.
INFO:     127.0.0.1:53151 - "POST /ask?question=Hello HTTP/1.1" 200 OK
INFO:     127.0.0.1:53155 - "GET /health HTTP/1.1" 404 Not Found
```

**Quan sát:** chạy được, nhưng chưa production-ready: lộ secret trong log, không có health check, chỉ nghe trên `localhost`, chạy kèm reloader.

### Exercise 1.3: Comparison table

Bản production (`cp .env.example .env`, `python app.py`) — kết quả test:

```text
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":0.6,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:46:35.836772+00:00"}
$ curl http://localhost:8000/ready
{"ready":true}
$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic.","model":"gpt-4o-mini"}
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{}'
HTTP/1.1 422 Unprocessable Content
{"detail":"question field is required"}
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong `app.py` (`DEBUG=True`, key, DB URL) | `config.py` đọc env (`os.getenv`), có `.env.example`, `validate()` fail-fast khi thiếu `AGENT_API_KEY` ở production | Cùng một image chạy được ở dev/staging/prod chỉ bằng đổi env (12-Factor III); secret không nằm trong git; thiếu config quan trọng thì app dừng ngay lúc khởi động thay vì lỗi lúc đang phục vụ user |
| Secrets | Hardcode + in ra log | Đọc từ env, **không log secret** (chỉ log `question_length`, `client_ip`) | Secret lộ trong git/log là phải revoke; xoá file không xoá được khỏi lịch sử git |
| Health check | Không có (`/health` → 404) | `/health` (liveness: uptime, version) + `/ready` (readiness: 503 khi chưa sẵn sàng) | Platform dùng liveness để restart process treo; load balancer dùng readiness để không gửi traffic vào instance đang khởi động/đang tắt |
| Logging | `print()` text tự do | `logging` format JSON (`time`, `level`, `msg`) | Log JSON parse/lọc/đếm được trong log aggregator (vd đếm request lỗi theo `event`); có level để tắt DEBUG trên production |
| Shutdown | Đột ngột, không có lifespan | `lifespan` (startup/shutdown) + handler SIGTERM, set `is_ready=False` khi tắt | Khi deploy mới/scale down, request đang chạy được làm xong thay vì bị cắt; `/ready` báo 503 để LB ngừng gửi traffic mới |
| Host / Port | `localhost:8000` cứng | `HOST` (mặc định `0.0.0.0`) + `PORT` từ env | Container/cloud cần bind `0.0.0.0`; Railway/Render inject `PORT` |
| Reload / Debug | `reload=True` luôn bật | `reload=settings.debug` (chỉ bật khi `DEBUG=true`) | Reloader chỉ dành cho dev |
| Input | Query string, không validate | JSON body, thiếu `question` → 422 rõ ràng | Không để câu hỏi nằm trên URL/access log; lỗi trả về có nghĩa |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ frontend được phép mới gọi từ browser |

**Phát hiện thêm khi chạy bản production:** log JSON của app (`startup`, `agent_request`) **không xuất hiện**. Nguyên nhân: `config.py` gọi `logging.warning("OPENAI_API_KEY not set...")` lúc import, trước khi `app.py` gọi `logging.basicConfig(...)`. Lời gọi `logging.warning` đầu tiên tự cấu hình root logger (level WARNING, format mặc định), nên `basicConfig` sau đó không có tác dụng và mọi log INFO bị bỏ. Cách sửa: gọi `logging.basicConfig(..., force=True)` hoặc cấu hình logging trước khi import `config`. Bài học cho Part 6: phải chạy thử và nhìn log thật, không chỉ đọc code.

---

## Part 2: Docker

### Exercise 2.1: Dockerfile questions (`02-docker/develop/Dockerfile`)

1. **Base image:** `python:3.11` — bản Python **đầy đủ** (Debian + compiler, header, tool build), không phải bản slim. Vì vậy image develop rất to (đo được 1.67 GB ở 2.2).
2. **Working directory:** `/app` (`WORKDIR /app`) — mọi lệnh `COPY`/`RUN`/`CMD` sau đó chạy tương đối với `/app`, thư mục được tạo nếu chưa có.
3. **Tại sao COPY `requirements.txt` trước:** tận dụng **layer cache**. Mỗi lệnh trong Dockerfile là một layer; Docker chỉ build lại từ layer đầu tiên có input thay đổi. `requirements.txt` ít đổi, còn code đổi liên tục. Copy requirements → `pip install` → rồi mới copy code, thì khi chỉ sửa code, layer `pip install` (chậm nhất) được dùng lại từ cache. Nếu `COPY . .` trước, mỗi lần sửa một dòng code là cài lại toàn bộ dependencies.
4. **CMD vs ENTRYPOINT:** `CMD ["python", "app.py"]` là lệnh/tham số **mặc định**, bị ghi đè hoàn toàn khi chạy `docker run <image> <lệnh khác>`. `ENTRYPOINT` là phần **cố định** của lệnh khởi động; tham số của `docker run` được nối thêm vào sau ENTRYPOINT (muốn đổi phải dùng `--entrypoint`). Hay dùng kết hợp: `ENTRYPOINT ["uvicorn"]` + `CMD ["app:app", "--port", "8000"]`. Dùng dạng exec (mảng JSON) để process là PID 1 và nhận được SIGTERM trực tiếp.

### Exercise 2.2: Build và run

```bash
# chạy từ gốc repo
docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
docker run -p 8000:8000 my-agent:develop
```

```text
$ curl -X POST "http://localhost:8000/ask?question=What%20is%20Docker?"
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":1.2,"container":true}
$ docker exec p2-develop whoami
root
```

Container develop chạy bằng user **root** — nếu app bị khai thác, kẻ tấn công có quyền root trong container.

### Exercise 2.3: Multi-stage build (`02-docker/production/Dockerfile`)

- **Stage 1 (`builder`, `python:3.11-slim`):** cài build tools (`gcc`, `libpq-dev`), rồi `pip install --user -r requirements.txt` vào `/root/.local`. Stage này chỉ dùng để build, không được deploy.
- **Stage 2 (`runtime`, `python:3.11-slim`):** image mới sạch; tạo user `appuser` (non-root); chỉ `COPY --from=builder /root/.local` (các package đã cài) + `main.py` + `utils/mock_llm.py`; `USER appuser`; có `HEALTHCHECK` gọi `/health`; `CMD uvicorn ... --workers 2`.
- **Tại sao image nhỏ hơn:** (1) base `slim` thay vì bản full; (2) `gcc`, header, apt cache, pip cache nằm ở stage builder và **bị bỏ lại** — image cuối chỉ chứa layer của stage cuối; (3) chỉ copy đúng file cần chạy thay vì cả thư mục.

```bash
docker build -f 02-docker/production/Dockerfile -t my-agent:advanced .
docker images my-agent
```

```text
IMAGE               ID             DISK USAGE   CONTENT SIZE
my-agent:advanced   b3300ea02f81        240MB         57.4MB
my-agent:develop    95731d74d47b       1.67GB          426MB

$ docker image inspect my-agent:develop  --format '{{.Size}}'   → 426320122
$ docker image inspect my-agent:advanced --format '{{.Size}}'   → 57409074
```

- **Develop:** 1.67 GB (disk usage) / 426 MB (content size, nén)
- **Production:** 240 MB (disk usage) / 57.4 MB (content size, nén)
- **Chênh lệch:** (1670 − 240) / 1670 = **giảm 85.6 %** theo disk usage; (426.3 − 57.4) / 426.3 = **giảm 86.5 %** theo content size — image production nhỏ khoảng **7 lần**.

Kiểm tra container production:

```text
$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"What is Docker?"}'
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
$ docker exec p2-adv whoami
appuser
```

### Exercise 2.4: Docker Compose stack (`02-docker/production/docker-compose.yml`)

```bash
cd 02-docker/production
touch .env.local                                   # file env_file bắt buộc, đã nằm trong .gitignore
docker compose -p k5-day14-docker up -d --build
```

**Services được start:** `agent` (build từ Dockerfile, target `runtime`), `redis` (`redis:7-alpine`), `qdrant` (`qdrant/qdrant:v1.9.0`), `nginx` (`nginx:alpine`).

```text
NAME                       IMAGE                   STATUS                       PORTS
k5-day14-docker-agent-1    k5-day14-docker-agent   Up (health: starting)        8000/tcp
k5-day14-docker-nginx-1    nginx:alpine            Up                           0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
k5-day14-docker-qdrant-1   qdrant/qdrant:v1.9.0    Up 11 seconds (healthy)      6333-6334/tcp
k5-day14-docker-redis-1    redis:7-alpine          Up 11 seconds (healthy)      6379/tcp
```

**Architecture:**

```text
                 ┌──────────── host ────────────┐
  Client ──HTTP──▶ :80  (cổng duy nhất mở ra ngoài)
                 └──────────────┬───────────────┘
                                │
   ┌────────────── network "internal" (bridge) ──────────────┐
   │                            ▼                            │
   │                  ┌───────────────────┐                  │
   │                  │ nginx             │  reverse proxy,  │
   │                  │ limit 10 r/s/IP   │  security header │
   │                  └─────────┬─────────┘                  │
   │                proxy_pass http://agent:8000             │
   │                            ▼                            │
   │                  ┌───────────────────┐                  │
   │                  │ agent (FastAPI)   │  non-root,       │
   │                  │ uvicorn :8000     │  HEALTHCHECK     │
   │                  └────┬─────────┬────┘                  │
   │    redis://redis:6379 │         │ http://qdrant:6333    │
   │                       ▼         ▼                       │
   │             ┌────────────┐   ┌────────────┐             │
   │             │ redis      │   │ qdrant     │             │
   │             │ cache/rate │   │ vector DB  │             │
   │             │ volume     │   │ volume     │             │
   │             └────────────┘   └────────────┘             │
   └─────────────────────────────────────────────────────────┘
```

**Chúng giao tiếp thế nào:** cùng network bridge `internal`, gọi nhau bằng **tên service** (Docker DNS: `agent`, `redis`, `qdrant`). Chỉ `nginx` publish cổng ra host; `agent` không publish cổng (thử `curl localhost:8000/health` → không kết nối được, HTTP `000`). `depends_on: condition: service_healthy` đảm bảo agent chỉ start khi redis và qdrant đã healthy. Redis và qdrant có volume để dữ liệu còn sau khi restart.

Test qua Nginx:

```text
$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":4.8,"version":"2.0.0","timestamp":"2026-11-25T08:49:25.970477"}

$ curl -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question":"Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
...
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}

$ docker compose -p k5-day14-docker logs agent nginx --tail 4
agent-1  | {"time":"2026-11-25 08:49:26,018","level":"INFO","msg":"{"event": "request", "q_len": 21}"}
agent-1  | INFO:     172.25.0.5:54936 - "POST /ask HTTP/1.1" 200 OK
nginx-1  | 172.25.0.1 - - [25/Nov/2026:08:49:26 +0000] "POST /ask HTTP/1.1" 200 126 "-" "curl/8.19.0"
```

Header bảo mật (`X-Frame-Options`, …) do Nginx thêm; request đi Client → nginx (172.25.0.1 là host) → agent (nguồn 172.25.0.5 là nginx).
Nhận xét: dòng log của agent **không phải JSON hợp lệ** — `msg` chứa một chuỗi JSON khác mà không escape dấu `"`. Part 6 cần log mỗi dòng là một JSON object hợp lệ (dùng `json.dumps` cho cả dòng).

Dọn: `docker compose -p k5-day14-docker down -v`.

---

## Part 3: Cloud Deployment

> Làm ở CP6: deploy chính agent Part 6 (`06-lab-complete/`), không deploy app mẫu `03-cloud-deployment/`.

### Exercise 3.1 / 3.2: Deploy agent Part 6

- **Public URL:** _(chưa deploy — điền sau, chi tiết trong [DEPLOYMENT.md](DEPLOYMENT.md))_
- Image deploy là image đã đạt `PASS 12/12` (multi-stage, 63 MB); `CMD` đọc `${PORT:-8000}` nên chạy được với PORT do platform cấp.
- Biến môi trường đặt trên platform, không có trong code: `AGENT_API_KEY`, `REDIS_URL` (trỏ tới Redis tạo trên platform), `ENVIRONMENT=production`, `RATE_LIMIT_PER_MINUTE`, `DAILY_BUDGET_USD`, `MONTHLY_BUDGET_USD`.

### So sánh `railway.toml` với `render.yaml` (cả hai đã viết trong `06-lab-complete/`)

| | `railway.toml` | `render.yaml` (Blueprint) | Vì sao khác |
|---|---|---|---|
| Phạm vi | Chỉ cấu hình build + deploy của **một service** (`[build] builder = "DOCKERFILE"`, `[deploy] healthcheckPath`, restart policy) | Mô tả **cả hạ tầng**: service `web` (Docker) **và** service `keyvalue` (Redis), cùng biến môi trường | Railway coi project/service/plugin (Redis) là thứ tạo bằng CLI/dashboard, file chỉ là config của service. Render Blueprint là Infrastructure-as-Code: một file dựng toàn bộ stack |
| Redis / `REDIS_URL` | Tạo Redis bằng `railway add` (dashboard), rồi tham chiếu biến `${{Redis.REDIS_URL}}` khi đặt variable | `fromService: {type: keyvalue, name: day14-redis, property: connectionString}`: Render tự nối URL | Railway nối service qua *reference variables*, Render nối ngay trong Blueprint |
| Secret | Không nằm trong file; đặt bằng `railway variables --set AGENT_API_KEY=...` | `generateValue: true` (Render tự sinh key ngẫu nhiên) hoặc `sync: false` (nhập tay trên dashboard), không bao giờ ghi giá trị | Cả hai đều tách secret khỏi repo; Render cần cú pháp riêng vì file Blueprint có mô tả biến môi trường |
| Thư mục | `railway up` chạy trong `06-lab-complete/`, CLI upload đúng thư mục đó | Phải khai `rootDir: 06-lab-complete` vì Render build từ repo GitHub (gốc repo có nhiều Part) | Railway CLI deploy từ máy local, Render deploy từ Git |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout`: Railway chỉ dùng lúc deploy để quyết định chuyển traffic sang bản mới | `healthCheckPath: /health`: Render dùng cả lúc deploy (zero-downtime) lẫn khi chạy (instance fail sẽ bị restart) | Khác cách platform dùng probe |
| Định dạng / khác | TOML; `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries` | YAML; `plan: free`, `region: singapore`, `autoDeploy: true` (push lên Git là tự deploy) | Render free web service ngủ sau ~15 phút không có traffic (cold start ~30–60 s); Railway tính theo credit |

### Exercise 3.3: Pipeline Cloud Run (`03-cloud-deployment/production-cloud-run/`)

`cloudbuild.yaml` là pipeline CI/CD 4 bước, chạy tuần tự nhờ `waitFor`, được trigger khi push lên `main`: **(1) test**: trong `python:3.11-slim` cài requirements rồi chạy `pytest`; test fail thì dừng luôn, không build. **(2) build**: `docker build` với hai tag, `$COMMIT_SHA` (bất biến, để rollback/truy vết) và `latest`, dùng `--cache-from latest` để tận dụng layer cache. **(3) push**: đẩy cả hai tag lên Container Registry `gcr.io/$PROJECT_ID/ai-agent`. **(4) deploy**: `gcloud run deploy` image theo đúng `$COMMIT_SHA` lên region `asia-southeast1`, với `min-instances=1` (tránh cold start), `max-instances=10`, 1 CPU / 512Mi, timeout 60 s; secret lấy từ Secret Manager bằng `--set-secrets`, không có trong image.
`service.yaml` là bản khai báo (IaC, Knative) của cùng service đó: autoscale 1–10 instance, `containerConcurrency: 80`, `AGENT_API_KEY`/`OPENAI_API_KEY` lấy từ `secretKeyRef`, `livenessProbe` gọi `/health` và `startupProbe` gọi `/ready`. Có file này thì cấu hình service được review trong Git và áp dụng lại bằng `gcloud run services replace service.yaml`.

---

## Part 4: API Security

### Exercise 4.1: API Key authentication (`04-api-gateway/develop/app.py`)

- **Key được check ở đâu:** hàm dependency `verify_api_key` (dòng 39–54) đọc header `X-API-Key` qua `APIKeyHeader(name="X-API-Key", auto_error=False)`; endpoint `/ask` gắn `_key: str = Depends(verify_api_key)` (dòng 70) nên mọi request vào `/ask` phải qua hàm này trước. `/` và `/health` để public (platform cần gọi health check không có key).
- **Sai key thì sao:** thiếu header → **401** `Missing API key`; có header nhưng sai → **403** `Invalid API key`. (Lưu ý cho Part 6: hợp đồng chấm yêu cầu **thiếu hoặc sai đều trả 401**.)
- **Rotate key thế nào:** key đọc từ env `AGENT_API_KEY`, nên rotate = đặt giá trị mới trên platform/secret manager rồi restart/redeploy, không sửa code. Để không gián đoạn client, cho phép **2 key song song** trong thời gian chuyển (vd `AGENT_API_KEYS=old,new`), báo client đổi sang key mới, rồi gỡ key cũ. Lưu ý: code có giá trị mặc định `"demo-key-change-in-production"` khi thiếu env — production nên fail-fast thay vì chạy với key mặc định.

Chạy: `AGENT_API_KEY=secret-key-123 python app.py`

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 401 Unauthorized
content-type: application/json

{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 403 Forbidden
content-type: application/json

{"detail":"Invalid API key."}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: ***" -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 200 OK
content-type: application/json

{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

### Exercise 4.2: JWT authentication (`04-api-gateway/production/auth.py`)

**JWT flow:**
1. Client `POST /auth/token` với username/password → `authenticate_user` so với `DEMO_USERS` → sai thì 401.
2. `create_token` ký payload `{sub, role, iat, exp = now + 60 phút}` bằng HS256 với `JWT_SECRET` → trả `access_token`.
3. Các request sau gửi `Authorization: Bearer <token>`. `verify_token` kiểm chữ ký + hạn: thiếu token → 401, hết hạn → 401, chữ ký sai/token giả → 403. Hợp lệ thì lấy `username`, `role` từ payload — **không cần tra DB mỗi request** (stateless), nên instance nào cũng verify được miễn là chung `JWT_SECRET`.
4. `role` trong token quyết định tier rate limit (user/admin).

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
content-type: application/json
x-content-type-options: nosniff
x-frame-options: DENY
x-xss-protection: 1; mode=block
referrer-policy: strict-origin-when-cross-origin

{"access_token":"<TOKEN_ĐÃ_CHE>","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer <TOKEN_ĐÃ_CHE>"}

# sai mật khẩu
HTTP/1.1 401 Unauthorized
{"detail":"Invalid credentials"}

# gọi /ask không có token
HTTP/1.1 401 Unauthorized
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

# token giả "abc.def.ghi"
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 200 OK
content-type: application/json
x-content-type-options: nosniff
x-frame-options: DENY

{"question":"Explain JWT","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","usage":{"requests_remaining":9,"budget_remaining_usd":1.6e-05}}
```

### Exercise 4.3: Rate limiting (`04-api-gateway/production/rate_limiter.py`)

- **Algorithm:** **sliding window** — mỗi user một `deque` timestamp; mỗi request xoá timestamp cũ hơn 60 giây, nếu số còn lại ≥ limit thì 429, ngược lại thêm timestamp hiện tại. (Docstring gọi là "Sliding Window Counter" nhưng thực chất là *sliding window log*: lưu từng timestamp, chính xác nhưng tốn bộ nhớ O(limit) mỗi user.)
- **Limit:** user thường (`student`) **10 request / 60 giây**; admin (`teacher`) **100 request / 60 giây**.
- **Bypass cho admin:** `app.py` chọn `rate_limiter_admin if role == "admin" else rate_limiter_user` theo `role` trong JWT — admin không bị bỏ qua hẳn mà dùng tier cao hơn (100/phút). Muốn bỏ hẳn thì không gọi `limiter.check()` khi `role == "admin"` (không khuyến khích: token admin bị lộ thì không còn gì chặn).
- **Hạn chế:** state nằm trong RAM của process → chạy nhiều instance/worker thì mỗi instance đếm riêng (limit thực tế nhân lên N lần) và mất khi restart. Part 6 phải lưu trong Redis.

Vòng lặp 20 request với cùng token (request "Explain JWT" ở 4.2 đã dùng 1 lượt trong cửa sổ 60 giây):

```text
for i in {1..20}; do curl -s -o /dev/null -w "Request $i -> %{http_code}\n" -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"question\": \"Test $i\"}"; done
Request 1 -> 200
Request 2 -> 200
Request 3 -> 200
Request 4 -> 200
Request 5 -> 200
Request 6 -> 200
Request 7 -> 200
Request 8 -> 200
Request 9 -> 200
Request 10 -> 429
Request 11 -> 429
...
Request 20 -> 429
```

Chi tiết response khi bị chặn:

```text
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795596671
retry-after: 53
content-type: application/json

{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":53}}
```

`GET /me/usage` sau đó: `{"user_id":"student","date":"2026-11-25","requests":10,"input_tokens":40,"output_tokens":304,"cost_usd":0.000188,"budget_usd":1.0,"budget_remaining_usd":0.999812,"budget_used_pct":0.0}` — đúng 10 request thành công được tính tiền, các request 429 không tốn tiền vì bị chặn **trước** khi gọi LLM.

### Exercise 4.4: Cost guard implementation

**Cách `cost_guard.py` có sẵn hoạt động:**
- Trước khi gọi LLM, `check_budget(user)` kiểm tra: tổng chi phí toàn hệ thống ≥ $10/ngày → **503**; chi phí user hôm nay ≥ $1/ngày → **402 Payment Required**; ≥ 80 % thì log warning.
- Sau khi gọi LLM, `record_usage(user, input_tokens, output_tokens)` cộng token, quy ra USD theo giá GPT-4o-mini ($0.15/1M input, $0.60/1M output).
- Record theo ngày (`day = YYYY-MM-DD`), sang ngày mới tự reset.

Mỗi request mock tốn khoảng $0.00002, nên muốn chạm $1/ngày cần khoảng 50.000 request — không làm được bằng curl khi rate limit chỉ cho 10/phút. Vì vậy tôi chứng minh 402 bằng cách dùng chính class `CostGuard` với budget thu nhỏ `$0.0001`:

```text
=== (a) CostGuard của 04-api-gateway/production, budget thu nhỏ còn $0.0001/ngày
Request 1: OK, đã dùng $0.000025
Request 2: OK, đã dùng $0.000050
Request 3: OK, đã dùng $0.000076
Request 4: OK, đã dùng $0.000101
Request 5: HTTP 402 -> {'error': 'Daily budget exceeded', 'used_usd': 0.000101, 'budget_usd': 0.0001, 'resets_at': 'midnight UTC'}
```

**Implement theo đề (Redis, $10/tháng mỗi user, reset đầu tháng):**

```python
import redis
from datetime import datetime

r = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
MONTHLY_BUDGET_USD = 10.0

def check_budget(user_id: str, estimated_cost: float) -> bool:
    month_key = datetime.now().strftime("%Y-%m")      # key đổi theo tháng → tự "reset" đầu tháng
    key = f"budget:{user_id}:{month_key}"
    current = float(r.get(key) or 0)
    if current + estimated_cost > MONTHLY_BUDGET_USD:  # vượt → caller trả 402
        return False
    r.incrbyfloat(key, estimated_cost)
    r.expire(key, 32 * 24 * 3600)                      # 32 ngày: key tháng cũ tự xoá
    return True
```

Chạy thử với Redis thật (`docker run -p 6390:6379 redis:7-alpine`), mỗi lần chi $3:

```text
Lần 1: chi $3.00 -> cho phép; Redis budget:alice:2026-11 = 3, TTL = 2764800s
Lần 2: chi $3.00 -> cho phép; Redis budget:alice:2026-11 = 6, TTL = 2764800s
Lần 3: chi $3.00 -> cho phép; Redis budget:alice:2026-11 = 9, TTL = 2764800s
Lần 4: chi $3.00 -> TỪ CHỐI (402); Redis budget:alice:2026-11 = 9, TTL = 2764800s
Lần 5: chi $3.00 -> TỪ CHỐI (402); Redis budget:alice:2026-11 = 9, TTL = 2764800s
```

**Vì sao lưu trong Redis:** state chung cho mọi instance và còn sau khi restart; key theo tháng + TTL nên không cần cron reset.
**Điểm cần cải thiện khi đưa vào Part 6:**
- `GET` rồi `INCRBYFLOAT` là 2 lệnh riêng → 2 request đồng thời có thể cùng đọc `current` cũ và cùng vượt budget (race condition). Sửa: `INCRBYFLOAT` trước, nếu kết quả > budget thì trừ lại và từ chối; hoặc gói trong Lua script / `MULTI`.
- Bản in-memory kiểm tra **trước** khi biết chi phí thật, nên request cuối có thể vượt nhẹ (thấy ở lần 4: $0.000101 > $0.0001). Chấp nhận được, hoặc ước lượng chi phí trước rồi điều chỉnh sau.
- Bug nhỏ trong `app.py`: trường `budget_remaining_usd` trả về `usage.total_cost_usd` (số **đã dùng**), không phải số còn lại.
- **Rate limit vs cost guard:** rate limit bảo vệ **tốc độ** (chống spam/DoS, chia tài nguyên công bằng), cost guard bảo vệ **tiền** (chặn tổng chi tiêu). Cần cả hai: 10 req/phút vẫn có thể đốt nhiều tiền nếu mỗi request dùng prompt rất dài; ngược lại budget lớn không chặn được một client gửi 1000 req/giây.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop/app.py`)

- `/health` — **liveness**: "process còn sống không?". Trả 200 kèm `status` (`ok`/`degraded`), uptime, version, check RAM. Platform restart container nếu endpoint này fail/timeout.
- `/ready` — **readiness**: "có nên gửi traffic vào không?". Trả 503 khi đang khởi động, đang shutdown, hoặc dependency (Redis/DB) chưa kết nối; load balancer bỏ instance khỏi pool nhưng **không** restart nó.

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"degraded","uptime_seconds":0.8,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:50:54.467100+00:00","checks":{"memory":{"status":"degraded","used_percent":96.7}}}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}

$ curl -X POST "http://localhost:8000/ask?question=Hello"
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

**Nhận xét:** máy đang dùng 96.7 % RAM nên `status` là `degraded` nhưng vẫn trả **200** — đúng thiết kế: liveness chỉ nên fail khi process thực sự hỏng, nếu không platform sẽ restart liên tục chỉ vì máy bận. `in_flight_requests: 1` là chính request `/ready` đang được middleware đếm.
**Vì sao `/health` phải 200 khi Redis chết còn `/ready` phải 503:** Redis chết thì restart agent cũng không chữa được (liveness fail → restart loop vô ích); việc đúng là ngừng gửi traffic vào instance đó (readiness 503) cho tới khi Redis trở lại.

### Exercise 5.2: Graceful shutdown

Cơ chế trong code: uvicorn bắt SIGTERM → ngừng nhận kết nối mới → chờ request đang chạy xong (`timeout_graceful_shutdown=30`) → chạy phần shutdown của `lifespan` (`_is_ready=False`, chờ `_in_flight_requests` về 0, tối đa 30 giây) → thoát exit code 0.

Windows không gửi được SIGTERM nên tôi chạy app trong container Linux. Mock LLM mất khoảng 3 giây mỗi câu; gửi request rồi 1 giây sau gửi SIGTERM:

```bash
docker run -d --name p5-shutdown -p 8000:8000 -v "$PWD:/src:ro" -w /src python:3.11-slim \
  sh -c "pip install -q -r requirements.txt && exec python app.py"
curl -X POST "http://localhost:8000/ask?question=Long%20task" &
sleep 1
docker kill --signal=SIGTERM p5-shutdown
```

```text
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
[client] HTTP 200, total 3.029998s
[container] exit code: 0

----- docker logs p5-shutdown
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Shutting down
INFO:     172.17.0.1:48618 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-25 08:51:28,994 INFO 🔄 Graceful shutdown initiated...
2026-11-25 08:51:28,994 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [1]
2026-11-25 08:51:28,994 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

**Kết quả:** SIGTERM đến khi request đang chạy dở (`Shutting down` in ra trước), nhưng request vẫn hoàn thành với **200** sau 3.03 giây, sau đó app chạy shutdown hook và thoát **exit code 0**.

Đối chứng bằng SIGKILL (giống tắt đột ngột / hết thời gian chờ của platform):

```text
$ docker kill --signal=SIGKILL p5-kill
[client] HTTP 000, total 1.216907s
[client] curl exit code: 52          # Empty reply from server — request bị cắt ngang
[container] exit code: 137           # 128 + 9 (SIGKILL)
```

**Nhận xét:**
- `exec python app.py` để Python là PID 1 và nhận SIGTERM trực tiếp; nếu `sh` là PID 1 thì signal không được chuyển tiếp và Docker phải SIGKILL sau 10 giây.
- Dòng `Received signal 15` của `handle_sigterm` in ra **cuối cùng**: uvicorn tạm thay signal handler của nó trong lúc chạy, xong mới gọi lại handler gốc của app. Logic shutdown thật nên đặt trong `lifespan`, không đặt trong `signal.signal`.

### Exercise 5.3: Stateless design (`05-scaling-reliability/production/app.py`)

- **Anti-pattern:** `conversation_history = {}` trong RAM. Khi chạy 3 instance, mỗi instance có RAM riêng: turn 1 vào instance A lưu history ở A, turn 2 bị LB đưa sang B → B không thấy history. Restart/deploy mới là mất toàn bộ. Rate limit/cost guard trong RAM cũng bị đếm riêng từng instance.
- **Cách bản production làm:** mọi state hội thoại nằm trong Redis — `save_session` dùng `SETEX session:<id>` (TTL 3600 giây), `load_session` dùng `GET`, `append_to_history` đọc → thêm message → giữ tối đa 20 message → ghi lại. Client giữ `session_id` và gửi kèm mỗi request; instance nào cũng đọc được cùng session. Process agent không giữ gì quan trọng → có thể tắt/thêm instance bất kỳ lúc nào.
- **Điểm cần lưu ý:**
  - Code có fallback `_memory_store` khi không kết nối được Redis lúc start — chạy 1 instance thì ổn nhưng khi scale sẽ âm thầm sai. Production nên để `/ready` trả 503 thay vì lặng lẽ chuyển sang RAM (đúng yêu cầu Part 6).
  - `append_to_history` là read-modify-write không atomic: 2 request cùng session đến 2 instance cùng lúc có thể ghi đè nhau. Dùng Redis list (`RPUSH` + `LTRIM`) sẽ an toàn hơn.
  - Trường `turn` bị lệch 1 (request đầu tiên trả `turn=2`) vì đếm sau khi đã thêm câu hỏi hiện tại rồi còn `+1`.

### Exercise 5.4: Load balancing

```bash
cd 05-scaling-reliability/production
touch .env.local
docker compose -p k5-day14-scaling up -d --build --scale agent=3
```

```text
NAME                       STATUS                       PORTS
k5-day14-scaling-agent-1   Up (health: starting)        8000/tcp
k5-day14-scaling-agent-2   Up (health: starting)        8000/tcp
k5-day14-scaling-agent-3   Up (health: starting)        8000/tcp
k5-day14-scaling-nginx-1   Up                           0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 11 seconds (healthy)      6379/tcp
```

10 request qua Nginx (cổng 8080, `POST /chat`); `served_by` lấy từ body, `X-Served-By` là header Nginx thêm (`$upstream_addr`):

```text
Request 1 -> served_by=instance-7ddf40  X-Served-By=172.25.0.5:8000  storage=redis
Request 2 -> served_by=instance-acf14f  X-Served-By=172.25.0.4:8000  storage=redis
Request 3 -> served_by=instance-5b7275  X-Served-By=172.25.0.3:8000  storage=redis
Request 4 -> served_by=instance-7ddf40  X-Served-By=172.25.0.5:8000  storage=redis
Request 5 -> served_by=instance-acf14f  X-Served-By=172.25.0.4:8000  storage=redis
Request 6 -> served_by=instance-5b7275  X-Served-By=172.25.0.3:8000  storage=redis
Request 7 -> served_by=instance-7ddf40  X-Served-By=172.25.0.5:8000  storage=redis
Request 8 -> served_by=instance-acf14f  X-Served-By=172.25.0.4:8000  storage=redis
Request 9 -> served_by=instance-5b7275  X-Served-By=172.25.0.3:8000  storage=redis
Request 10 -> served_by=instance-7ddf40  X-Served-By=172.25.0.5:8000  storage=redis

$ docker compose -p k5-day14-scaling logs agent | grep "POST /chat" | awk '{print $1}' | sort | uniq -c
      7 agent-1
      6 agent-2
      7 agent-3
```

(agent-1 = `instance-7ddf40`, agent-2 = `instance-acf14f`, agent-3 = `instance-5b7275`.) 20 request qua 2 lượt chia 7/6/7.

**Giải thích:** `server agent:8000` trong `nginx.conf` — Docker DNS phân giải tên `agent` ra IP của cả 3 container; Nginx **round-robin** lần lượt qua từng IP (thấy rõ chu kỳ 7ddf40 → acf14f → 5b7275). Agent không publish cổng, mọi traffic đi qua Nginx. `proxy_next_upstream error timeout http_503` cho phép Nginx thử instance khác khi một instance lỗi.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
============================================================
Stateless Scaling Demo
============================================================

Session ID: d33d3e93-ba66-4ac7-987e-9bc02f4b9803

Request 1: [instance-acf14f]
  Q: What is Docker?
Request 2: [instance-5b7275]
  Q: Why do we need containers?
Request 3: [instance-7ddf40]
  Q: What is Kubernetes?
Request 4: [instance-acf14f]
  Q: How does load balancing work?
Request 5: [instance-5b7275]
  Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-acf14f', 'instance-7ddf40', 'instance-5b7275'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  [user]: Why do we need containers?...
  ...
  [user]: What is Redis used for?...
  [assistant]: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đ...

✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu của **cùng một session** được 3 instance khác nhau phục vụ (round-robin), nhưng history đủ **10 message** (5 user + 5 assistant) đúng thứ tự. Nếu history nằm trong RAM, mỗi instance chỉ thấy phần của mình (khoảng 3–4 message) và `GET /history` trả kết quả khác nhau tuỳ instance nhận request.

**Thử thêm — tắt 1 instance giữa cuộc hội thoại** (script không tự làm bước này):

```text
$ docker stop k5-day14-scaling-agent-1        # tắt instance-7ddf40
Turn 6 -> 200 served_by=instance-acf14f
Turn 7 -> 200 served_by=instance-5b7275
Turn 8 -> 200 served_by=instance-acf14f
Turn 9 -> 200 served_by=instance-5b7275
$ curl http://localhost:8080/chat/d33d3e93-ba66-4ac7-987e-9bc02f4b9803/history
count = 18                                    # 10 cũ + 4 × 2 mới, không mất gì

$ docker compose -p k5-day14-scaling logs nginx
[error] upstream timed out (110: Operation timed out) while connecting to upstream, ... upstream: "http://172.25.0.5:8000/chat"
```

Instance chết không làm mất session vì state nằm ở Redis; 2 instance còn lại tiếp tục phục vụ. Log Nginx cho thấy nó vẫn thử gửi tới IP của instance đã tắt (172.25.0.5), bị timeout rồi `proxy_next_upstream` chuyển sang instance khác — client vẫn nhận 200 nhưng request đó chậm hơn. Muốn tránh hẳn cần health check chủ động ở LB (hoặc platform như K8s tự rút instance khỏi pool dựa trên readiness).

Dọn: `docker compose -p k5-day14-scaling down`.

---

## Part 6: Final Project

Code trong `06-lab-complete/` (sửa hết TODO, làm ngay trong thư mục đó).

**Cách chạy script chấm:** máy là Windows, Python Windows không gửi được SIGTERM nên tiêu chí 11 luôn ⚠️. Trên máy cũng không có distro WSL nào ngoài `docker-desktop`. Vì vậy mình chạy `check_production_ready.py` **không sửa gì** trong một container Linux `python:3.11-slim` có Docker CLI, mount `/var/run/docker.sock` và chạy với `--network host`, để script vẫn build/chạy image thật bằng Docker Desktop. Script được copy cùng `06-lab-complete/` và `.gitignore` gốc vào container rồi chạy `python check_production_ready.py` (chế độ đầy đủ). Trước đó chạy `--skip-docker` trên Windows: 38/38 check đạt, chỉ còn 2 mục ⚠️ cần Linux/Docker.

Output (phần cuối):

```text
✅ 1. Dockerfile (multi-stage, < 500 MB)
     ✅ Docker: build image thành công — 21s
     ✅ Docker: image < 500 MB — 63 MB
     ✅ Docker: container chạy được, GET /health → 200
✅ 7. Rate limiting
     ✅ Khi chạy: vượt giới hạn → 429 — 429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)
     ✅ Khi chạy: state nằm trong Redis — restart agent vẫn bị 429
✅ 11. Graceful shutdown
     ✅ Code xử lý SIGTERM / lifespan shutdown
     ✅ Khi chạy: SIGTERM lúc đang có request → request vẫn 200, process tự thoát — exit code 0
     ✅ Docker: `docker stop` tắt êm (exit code 0, < 10s) — exit code 0, 0.7s

================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

**Tóm tắt cách làm từng phần:**

| Tiêu chí | File | Cách làm |
|---|---|---|
| Config (9) | `app/config.py` | `dataclass` đọc mọi giá trị bằng `os.getenv` (có default cho local, `python-dotenv` nạp `.env` nếu có). `AGENT_API_KEY` không có default: chưa đặt thì `/ask` luôn 401 (fail closed) |
| Log JSON (10) | `app/main.py` | `JsonFormatter` dùng `json.dumps` cho cả dòng; middleware ghi mỗi request (`request_id`, `path`, `status`, `ms`); `propagate=False` để root logger không in lại dạng text |
| `/health` (4) / `/ready` (5) | `app/main.py`, `app/redis_client.py` | `/health` không chạm Redis. `/ready` gọi `redis.ping()` với `socket_connect_timeout=1`: mất Redis → 503, đang tắt → 503. `redis.from_url` không mở kết nối lúc import, nên app khởi động được khi chưa có Redis |
| API key (6) | `app/auth.py` | `APIKeyHeader("X-API-Key", auto_error=False)`, vì để mặc định FastAPI sẽ trả 403. So sánh bằng `hmac.compare_digest`; thiếu/sai → 401 |
| Rate limit (7) | `app/rate_limiter.py` | Sliding window bằng sorted set trong Redis: `ZREMRANGEBYSCORE → ZADD → ZCARD → EXPIRE` trong một `MULTI/EXEC`. Thêm request trước rồi mới đếm, nên 2 worker không cùng lọt qua ở request thứ N+1 (lỗi race đã thấy ở Part 4). Vượt → 429 + `Retry-After` tính từ request cũ nhất. Key Redis là hash của API key, không lưu key thô |
| Cost guard (8) | `app/cost_guard.py` | `check_budget()` trước khi gọi LLM, `record_cost()` sau đó bằng `INCRBYFLOAT` (atomic) vào `cost:daily:YYYY-MM-DD` và `cost:monthly:YYYY-MM` (có TTL). Vượt ngày hoặc tháng → 402 |
| Xử lý lỗi | `app/main.py` | 422 có danh sách field lỗi (không trả nguyên object của Pydantic); Redis lỗi → 503 + `Retry-After`; mọi exception khác → 500 `{"detail": "Internal server error"}`. Stack trace chỉ ghi vào log, không trả ra client |
| Graceful shutdown (11) | `app/main.py`, `Dockerfile` | Trong `lifespan`, cài hook SIGTERM: đặt cờ để `/ready` trả 503 (LB ngừng gửi request mới) rồi **gọi lại handler cũ của uvicorn**. Uvicorn ngừng nhận kết nối, chờ request đang chạy xong rồi thoát với code 0. `CMD ["sh","-c","exec uvicorn ... --timeout-graceful-shutdown 8"]`: `exec` làm uvicorn thành PID 1 nên nhận SIGTERM trực tiếp, và 8 s < 10 s grace của `docker stop` |
| Docker (1) | `Dockerfile`, `.dockerignore` | Builder cài vào venv `/opt/venv`, runtime `python:3.11-slim` chỉ copy venv + `app/` + `utils/`, user `app` non-root, `HEALTHCHECK` bằng Python (image slim không có curl). `.dockerignore` loại `.env*`, `__pycache__`, `venv`, `.git`, `benchmark-observability/` |
| Compose (2) | `docker-compose.yml` | `agent` (build) + `redis:7-alpine` (healthcheck, AOF, volume). Agent có `REDIS_URL=redis://redis:6379/0`, `depends_on: condition: service_healthy`, đọc `.env` với `required: false` |
| Deploy config (12) | `railway.toml`, `render.yaml` | Xem Part 3 |

Kiểm tra thêm bằng `docker compose -p k5-day14-final up` (ngoài script chấm):

```text
ok:            {"question":"What is docker?","answer":"Container là cách đóng gói app ...","cost_usd":9.45e-06} [200]
empty q:       {"detail":"Invalid request body","errors":[{"field":"question","msg":"String should have at least 1 character"}]} [422]
missing field: {"detail":"Invalid request body","errors":[{"field":"question","msg":"Field required"}]} [422]
10 request:    200 200 200 200 200 200 200 200 200 429      (RATE_LIMIT_PER_MINUTE=10, 1 request trước đó)
HTTP/1.1 429 Too Many Requests / retry-after: 58
redis down  -> health 200, ready {"status":"not_ready","redis":"unreachable"} [503], ask {"detail":"Service temporarily unavailable"} [503]
```

---

## AI log

| # | Hỏi AI gì | Nhận được gì | Tôi tự sửa / tự kiểm tra thế nào |
|---|---|---|---|
| 1 | (Claude Code) Giới thiệu lab, mục đích, hướng dẫn theo checkpoint; vì sao cần WSL2 | Tóm tắt README/CHECKPOINTS/RUBRIC; giải thích SIGTERM không có trên Windows nên Graceful shutdown luôn ⚠️; các bước cài Ubuntu WSL2 | _(tự điền)_ |
| 2 | (Claude Code) Chạy CP1–CP4 và soạn `MISSION_ANSWERS.md` Part 1, 2, 4, 5 | AI chạy các lệnh trên máy tôi (Python venv + Docker Desktop), thu output thật, đo kích thước image, viết câu trả lời + phát hiện: lỗi log JSON bị nuốt ở Part 1, log không phải JSON hợp lệ ở Part 2, 403 vs 401 ở Part 4, race condition cost guard, test SIGTERM/SIGKILL trong container, tắt 1 instance ở Part 5 | _(tự điền: tôi đã chạy lại lệnh nào, tự đọc lại code nào, sửa câu nào)_ |
| 3 | (Claude Code) "Làm nốt hoàn thiện bài lab": Part 6, Part 7, Part 3 (phần không cần tài khoản cloud) | AI viết code các TODO của `06-lab-complete/`, chạy script chấm trong container Linux (PASS 12/12), chạy load test/eval/Locust, sửa `KNOWLEDGE_BASE`, viết `.github/workflows/eval.yml`, bật stack observability, làm 5 thí nghiệm alert, chụp ảnh bằng Chrome headless (Playwright), viết `BENCHMARK_REPORT.md`. Các phát hiện: `localhost` trên Windows làm sai p95 nên chạy lại bằng `127.0.0.1`; lỗi `ModuleNotFoundError: uvicorn` trong `production/Dockerfile` của Part 7 (sửa bằng `PYTHONUSERBASE`); `LLMCostBurnRateHigh` firing ngay ở baseline | _(tự điền: tôi đã đọc lại code nào, chạy lại lệnh nào để kiểm tra)_ |
