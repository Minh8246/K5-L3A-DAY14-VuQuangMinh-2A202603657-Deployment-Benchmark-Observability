# Benchmark & Observability Report — Day 14

**Họ tên:** Vũ Quang Minh — 2A202603657
**Ngày chạy:** 2026-11-25 (giờ trong log/Prometheus là UTC; ảnh Grafana/Jaeger hiển thị giờ máy GMT+7)
**Môi trường:** Laptop Windows 11 Pro, Intel Core Ultra 5 125H (14 nhân / 18 luồng), RAM 15 GB. Bước 1 (`develop/`): Python 3.13, agent và `load_test.py` chạy trên cùng máy. Bước 2 (`production/`): Docker Desktop 29.4.3 (VM 18 CPU / 7.4 GB), mỗi container agent bị giới hạn `cpus: 0.5`, `memory: 256M`.

> **Nguồn số liệu:** bảng 2.x lấy từ `reports/load_v2_*.json`; bảng 3 từ `reports/eval_*.json`; bảng 5 từ `benchmark-results/scale_*_stats.csv` (dòng `POST /ask`); mục 4 từ Prometheus API, Jaeger và log container, đo lúc chạy.
>
> **Lưu ý cách đo:** load test gọi `http://127.0.0.1:8002` thay vì `http://localhost:8002`. Trên Windows, `localhost` được phân giải ra `::1` (IPv6) trước, mà agent chỉ bind IPv4 `0.0.0.0`, nên mỗi kết nối mới mất thêm vài trăm ms (đo thử: `socket.create_connection(("localhost", 8002))` mất 2045 ms, `127.0.0.1` mất 0.9 ms). Lần chạy đầu bằng `localhost` cho p95 ≈ 460 ms ngay cả khi `MOCK_SLOW_RATE=0` — lỗi đo chứ không phải agent chậm. Mình chạy lại toàn bộ 7 load test bằng `127.0.0.1`; các file trong `reports/` là bản chạy lại.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Mock LLM mất khoảng 120–180 ms, cộng retrieval ~20 ms, nên p95 lúc bình thường khoảng 240 ms (bảng 2.2, hàng `0`). 500 ms cho dư khoảng 2 lần để chịu được jitter và tải tăng, mà người dùng chat vẫn thấy phản hồi gần như tức thì. Đặt theo p95 vì người dùng cảm nhận theo request chậm, không theo trung bình. Không đặt theo p99: mock cố tình làm ~3% request chậm thêm 1.5 s (bảng 2.2), nên p99 luôn ≈ 1.7 s. |
| Error rate `/ask` | < 2 % | Mock LLM giả lập lỗi upstream 1% (`MOCK_ERROR_RATE=0.01`). 2% là gấp đôi mức lỗi nền đó, tức error budget 2%. Alert đặt lỏng hơn (5% trong 1 phút) để không báo động vì spike ngắn. |
| Accuracy trên golden dataset | ≥ 85 % và không có câu nào regression so với bản trước | Trùng ngưỡng của job CI `.github/workflows/eval.yml`. Golden set chỉ có 23 câu nên mỗi câu là ~4.3 điểm %, vì vậy điều kiện "không regression" quan trọng ngang ngưỡng accuracy. Trước khi release thì yêu cầu chặt hơn: 23/23, như lệnh eval cuối ở CP7 (`--min-accuracy 1.0`). |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

300 request mỗi mức, `MOCK_SLOW_RATE=0.03` (mặc định). File: `reports/load_v2_c{1,10,50,100}.json`.

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.33 | 205.0 | 244.6 | 1682.3 | 1.33 % (4 × 503) | ✅ PASS |
| 10 | 34.5 | 207.4 | 249.7 | 1726.4 | 2.33 % (7 × 503) | ❌ FAIL (error rate) |
| 50 | 103.07 | 206.3 | 282.2 | 1710.2 | 1.00 % (3 × 503) | ✅ PASS |
| 100 | 71.73 | 463.6 | 3181.4 | 3538.5 | 0.67 % (2 × 503) | ❌ FAIL (p95) |

**Nhận xét:** RPS chỉ tăng gần tuyến tính ở đoạn đầu: từ c=1 lên c=10, RPS tăng 8 lần (4.33 → 34.5) trong khi p50 gần như không đổi. Lý do: mỗi request chủ yếu chờ `asyncio.sleep` của mock LLM, mà agent async nên một process vẫn chồng được nhiều request đang chờ. Theo định luật Little, RPS ≈ concurrency / latency = 10 / 0.26 s ≈ 38. Từ c=10 lên c=50, RPS chỉ tăng 3 lần (lẽ ra 5 lần), và tới c=100 thì RPS **giảm** còn 71.7 trong khi p50 tăng gấp đôi (463 ms) và p95 lên 3.2 s. Agent lúc này đã **bão hoà**: chỉ có 1 process uvicorn, 1 event loop, còn phần việc CPU của mỗi request (parse JSON, Pydantic, ghi log JSON, cập nhật metric) thì chạy tuần tự. Thêm concurrency sau điểm bão hoà chỉ làm hàng đợi dài ra. Load generator cũng chạy trên cùng máy nên giành CPU với agent. Mình chạy thêm 1500 request (không lưu file) để chắc đây không phải hiệu ứng khởi động: c=100 cho 58.7 req/s, p50 960 ms; c=200 cho 50.3 req/s, p50 3.2 s. Vậy điểm bão hoà của 1 process nằm quanh c=50 (~100 req/s). Muốn hơn nữa thì phải scale ngang (thêm worker/instance), không tăng concurrency.
Riêng c=10 fail SLO error rate (7/300 = 2.33%) là do ngẫu nhiên: lỗi được bơm với xác suất 1% mỗi request, 300 mẫu nên dao động 1–2.3% giữa các lần chạy (các mức khác chỉ 0.67–1.33%). Tức là với 300 request, error-rate SLO 2% không đủ ổn định để kết luận. Cần mẫu lớn hơn, hoặc đánh giá theo cửa sổ thời gian như alert.

### 2.2 Tail latency

300 request, concurrency 20, restart agent v2 với từng giá trị. File: `reports/load_v2_slow_{0,0.03,0.1}.json`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 202.2 | 236.0 | 244.7 |
| 0.03 (mặc định) | 255.8 | 341.9 | 1733.1 |
| 0.1 | 331.3 | 1690.8 | 1717.2 |

(p50 gần như không đổi: 202.2 / 210.2 / 206.9 ms. RPS giảm 96.2 → 66.7 → 50.1 vì vài request 1.7 s kéo dài tổng thời gian chạy.)

**Nhận xét:** Từ 0 lên 0.03, **p99 nhạy nhất**: tăng 7 lần (245 → 1733 ms), trong khi avg chỉ tăng 27% (202 → 256 ms). Lên 0.1 thì tới lượt **p95 nhảy vọt** (342 → 1691 ms, gấp 5 lần), còn avg vẫn chỉ 331 ms. Lý do: percentile q bằng giá trị của request ở vị trí q. Khi tỉ lệ request chậm vượt 1 − q (vượt 1% thì p99 dính, vượt 5% thì p95 dính), percentile đó nhảy thẳng sang nhóm chậm (~1.7 s). Average thì chỉ cộng thêm `tỉ lệ chậm × 1.5 s`, bị 97% request nhanh pha loãng. Nếu SLO đặt theo average (vd < 300 ms) thì cấu hình 0.03 vẫn "đạt" dù cứ 100 người có ~3 người chờ gần 2 giây. Vì vậy SLO phải đặt theo p95/p99, là các chỉ số mô tả trải nghiệm của nhóm người dùng tệ nhất.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3 % (1/23) | 91.3 % (21/23) | 100 % (23/23) |
| Avg tokens / câu | 57.2 | 108.8 | 115.9 |
| Tổng cost (USD) | 0.000406 | 0.000758 | 0.000810 |
| Cost / câu đúng (USD) | 0.00040575 | 0.00003607 | 0.00003521 |
| Quality gate | FAIL (accuracy < 80%) | PASS (≥ 80%, 0 regression so với v1) | PASS (≥ 100%, 0 regression so với v2) |

File: `reports/eval_v1.json`, `reports/eval_v2.json` (baseline = v1), `reports/eval_v2_fixed.json` (baseline = v2, `--min-accuracy 1.0`).

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `scaling-03` — "Kubernetes HPA scale dựa trên những chỉ số nào?" | `cpu`, `memory`, `custom metrics` | KB không có tài liệu nào chứa từ khoá của câu hỏi nên retrieval trả rỗng, agent rơi về câu trả lời chung chung ("Tôi là AI agent được deploy lên cloud…"). Thêm entry kích hoạt bởi `hpa` / `autoscal` / `kubernetes`, nội dung nói HPA scale theo CPU, memory hoặc custom metrics. |
| `security-02` — "JWT khác gì API key trong xác thực?" | `token`, `hết hạn`, `chữ ký` | Cùng nguyên nhân: không có tài liệu nào match `jwt`. Thêm entry kích hoạt bởi `jwt`, nội dung so sánh API key (chuỗi cố định, không hết hạn) với JWT (token có chữ ký số, có thời điểm hết hạn `exp`). |

Sửa trong `06-lab-complete/benchmark-observability/develop/utils/mock_llm.py`, chép cùng nội dung sang `production/utils/mock_llm.py` để stack Docker cũng trả lời đúng. Trước khi chạy eval, mình kiểm tra từ khoá kích hoạt mới không trùng câu hỏi khác trong golden set (`jwt`, `hpa`, `kubernetes` chỉ xuất hiện ở 2 câu này). Kết quả eval cuối: 2 câu được cải thiện, **0 regression**, `Quality gate (accuracy ≥ 100%, không regression): ✅ PASS`, exit code 0.

**Chặn merge bằng exit code:** `run_eval.py` trả exit code 1 khi accuracy < `--min-accuracy` hoặc có câu regression so với `--baseline`. Chạy v1 với `--baseline reports/eval_v2.json` thì in `⚠️ Câu bị regression : 20` và `❌ FAIL`, exit code 1. Job `.github/workflows/eval.yml` (chạy ở mỗi pull request) bật agent v2 với `MOCK_ERROR_RATE=0` (để gate không fail ngẫu nhiên vì lỗi giả lập), rồi chạy `run_eval.py --min-accuracy 0.85 --baseline reports/eval_v2.json`. Exit code 1 làm step fail, step fail làm job fail. Khi bật branch protection "Require status checks to pass" cho job `quality-gate/eval` thì nút Merge bị khoá cho tới khi eval pass.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**
v2 tốn gấp **1.9 lần token** mỗi câu (57.2 → 108.8), vì prompt có thêm tài liệu retrieve được. Tổng cost vì vậy cũng gấp 1.9 lần. Latency p50 của eval tăng khoảng 42 ms (171.7 → 214.2 ms) do thêm bước retrieval (~20 ms) và prompt dài hơn (mock cộng 0.2 ms/token). Đổi lại, accuracy từ 4.3% lên 91.3%. Tính trên mỗi câu trả lời đúng thì v2 **rẻ hơn 11 lần** (0.000406 → 0.0000361 USD/câu đúng). "Cost / câu" chỉ cho thấy v2 đắt gấp đôi và dễ dẫn tới quyết định sai; "cost / câu đúng" tính cả việc v1 trả lời sai gần hết, tức tiền của v1 gần như mất trắng. Sửa KB tăng thêm 7 token/câu và gần như không đổi cost/câu đúng (0.0000361 → 0.0000352), nên rất đáng làm.

---

## 4. Observability stack (production)

Stack bật bằng `docker compose -p k5-day14-observability up -d --build`. Tải nền trong lúc chụp ảnh và làm thí nghiệm: Locust headless 30 user (`-u 30 -r 5`).

**Lỗi tìm thấy và đã sửa trong `production/Dockerfile`:** container agent crash liên tục với `ModuleNotFoundError: No module named 'uvicorn'`. Nguyên nhân: stage builder cài thư viện bằng `pip install --user` vào `/root/.local`, rồi copy sang `/home/agent/.local`. Nhưng user `agent` được tạo với `useradd -d /app`, tức HOME=/app, nên Python tìm user site-packages ở `/app/.local` và không thấy thư viện. Sửa bằng một dòng `ENV PYTHONUSERBASE=/home/agent/.local`.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải: [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png). 30 user, 18.6 req/s, error 0.59%, p95 296 ms, 2 instance; thấy cả 2 đợt benchmark 100 user ở 18:27–18:31.
- [x] Một trace chậm trong Jaeger: [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png). Trace 1.8 s, span `chat mock-llm-small` chiếm 1.8 s.
- [x] Một trace lỗi trong Jaeger, có event `exception`: [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png). `POST /ask` trả 503; span `chat mock-llm-small` có `error.type = upstream_unavailable` và event `exception` với message `Upstream LLM error: 503 Service Unavailable (mock)`.
- [x] Trang Alerts của Prometheus với ≥ 2 alert đang **firing** cùng lúc: [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png). `AgentHighErrorRate` (value 0.185) và `AgentHighLatencyP95` (value 1.90 s), chụp lúc thí nghiệm A + B cùng bật.
- Ảnh thêm: [`screenshots/grafana-chaos-AB.png`](screenshots/grafana-chaos-AB.png) (dashboard trong thí nghiệm A + B), [`screenshots/grafana-exp-E-v1.png`](screenshots/grafana-exp-E-v1.png) (sau thí nghiệm E: bảng version = v1, hit ratio mất dữ liệu).

Unit test alert: `promtool test rules /p/alert_rules_test.yml` cho kết quả `SUCCESS`.

### 4.2 Thí nghiệm "phá hệ thống"

Cách đo: sửa `.env`, chạy `docker compose -p k5-day14-observability up -d agent`, rồi ghi giờ ngay khi lệnh xong (mốc 0). Một script hỏi `GET /api/v1/alerts` của Prometheus mỗi 3 s và ghi thời điểm alert chuyển sang `pending` và `firing`. Prometheus scrape và evaluate mỗi 5 s.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` | **93 s** (pending sau 33 s + `for: 1m`). Đổi lúc 11:35:36, firing lúc 11:37:10 UTC | *Error rate* chuyển đỏ (~20.8%); *Requests/s theo status code* có đường 503 dày lên; *LLM calls theo kết quả* (error) |
| B | `MOCK_SLOW_RATE=0.3` (giữ A) | `AgentHighLatencyP95` | **148 s** (pending sau 27 s + `for: 2m`). 11:37:20 → 11:39:48 UTC | *Latency p50/p95/p99*: p95 1.90 s, p99 1.98 s, trong khi avg 0.59 s và p50 0.196 s gần như không đổi; *LLM latency p95* 1.92 s |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` | **145 s** (pending sau 24 s + `for: 2m`). 11:41:00 → 11:43:25 UTC | *Cost burn rate*: từ $1.6/giờ lên $16.75/giờ lúc firing, rồi $18.07/giờ khi cửa sổ 5 phút đầy; *LLM latency p95 theo model* (series mới `mock-llm-large`, 0.50 s); p95 `/ask` lên 0.68 s |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` | **42 s** (pending sau 12 s + `for: 30s`). 11:44:05 → 11:44:48 UTC | *Instances up* 2 → 1; *Requests/s theo instance* (một đường tụt về 0). *Error rate* vẫn xanh (0.9%) dù log Nginx có **29 lần 504** trong lúc pause |
| E | `AGENT_VERSION=v1` | Không có | — | Bảng *Phiên bản đang chạy* đổi sang `v1`; *Tokens/s* input giảm 1450 → ~677/s ở cùng ~19 req/s; *Retrieval hit ratio* thành "No data" sau ~5 phút (cửa sổ `rate[5m]`); *Cost burn rate* giảm ~$3.9 → $1.1/giờ |

Ghi chú:
- **Thời gian tới lúc firing** = thời gian để biểu thức thành đúng + thời gian `for`. Biểu thức cần vài lần scrape (5 s/lần) để `rate(...[1m])` có đủ dữ liệu mới, và quantile/tỉ lệ phải vượt ngưỡng. Phần này mất 12–33 s. Sau đó alert ở `pending` trọn thời gian `for` (30 s, 1 phút, 2 phút) rồi mới `firing`. Muốn báo nhanh hơn thì giảm `for`, đổi lại dễ báo động giả.
- **`LLMCostBurnRateHigh` đã firing ngay ở baseline.** Hai đợt benchmark 100 user đẩy cost lên ~$6/giờ, và với 30 user (~19 req/s) model nhỏ vẫn tốn ~$3.4–3.9/giờ, cao hơn ngưỡng $2/giờ. Alert chỉ resolve trong thí nghiệm A + B vì 20% request lỗi và RPS giảm kéo cost xuống $1.6/giờ. Thí nghiệm C bắt đầu từ trạng thái inactive đó nên thời gian 145 s đo được là hợp lệ. Bài học: ngưỡng cost phải tính từ traffic dự kiến (cost/request × RPS), nếu không alert sẽ kêu suốt và mọi người sẽ bỏ qua nó (alert fatigue).
- **D — vì sao 504 không lên dashboard:** request được Nginx gửi tới instance bị pause phải chờ hết `proxy_read_timeout 10s` rồi nhận 504. Agent không bao giờ xử lý xong request đó nên metric của agent không đếm. Muốn thấy loại lỗi này cần metric ở tầng load balancer (vd nginx exporter) hoặc synthetic check gọi từ bên ngoài.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**
Đây là lỗi **chất lượng**: request vẫn 200 và vẫn nhanh, nên metric hạ tầng (latency, error, saturation) không có lý do gì để đỏ. Cách phát hiện:
(1) **Chặn trước khi deploy**: chạy eval golden dataset trong CI (job `eval.yml`). Riêng v1 rớt 20 câu regression nên không bao giờ qua được gate.
(2) **Metric chất lượng proxy khi chạy**: alert trên `agent_retrieval_total` (hit ratio < 50% trong 10 phút), trên `agent_info{version}` khi version đổi ngoài lịch release, và trên token/request giảm đột ngột.
(3) **Online eval**: định kỳ chạy một phần golden set như synthetic probe vào production, hoặc dùng LLM-as-judge chấm mẫu ~1% hội thoại thật, rồi đẩy điểm thành metric `agent_quality_score` và đặt alert trên nó.
(4) **Feedback người dùng** (👍/👎, tỉ lệ hỏi lại) làm SLI chất lượng.

### 4.3 Debug bằng trace

Trace chậm nhất trong 30 phút (Jaeger API, `duration_min=1s`):

- `trace_id`: `8095991ffbd5b8d7e765003dc1c23dc9`
- Tổng thời gian: 1795.5 ms (`POST /ask`, HTTP 200)
- Span chiếm nhiều thời gian nhất: `chat mock-llm-small` (1772.3 ms, 98.7%). `agent.retrieve_context` chỉ 18.3 ms (1.0%). Span `chat` có `gen_ai.request.model = mock-llm-small`, `gen_ai.usage.input_tokens = 81`, `gen_ai.usage.output_tokens = 46`. Kết luận: request chậm vì upstream LLM (request rơi vào nhóm "slow" của mock), không phải vì code agent hay retrieval. Cách xử lý đúng là timeout + retry/hedging với LLM, hoặc streaming để giảm thời gian chờ token đầu tiên, chứ không phải tối ưu code.
- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep 8095991ffbd5b8d7e765003dc1c23dc9`):

```json
{"ts": "2026-11-25T11:31:27.512775+00:00", "level": "INFO", "service": "observable-agent", "instance": "d955bfd360f7", "event": "llm_call", "trace_id": "8095991ffbd5b8d7e765003dc1c23dc9", "span_id": "e3c2d597f804397e", "request_id": "c1906e46b681", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 81, "output_tokens": 46, "cost_usd": 4e-05, "llm_ms": 1683.1}
{"ts": "2026-11-25T11:31:27.515057+00:00", "level": "INFO", "service": "observable-agent", "instance": "d955bfd360f7", "event": "http_request", "trace_id": "8095991ffbd5b8d7e765003dc1c23dc9", "span_id": "e3c2d597f804397e", "request_id": "c1906e46b681", "method": "POST", "route": "/ask", "status": 200, "ms": 1793.9}
```

(`llm_ms` = 1683 ms là độ trễ mock tự tính. Span đo được 1772 ms; ~90 ms chênh lệch là thời gian event loop bị trễ khi container chỉ có 0.5 CPU và đang chịu 100 user.)

Trace lỗi: `c20eaffa65a03726762abb168f0c56b9` (109.4 ms, HTTP 503). Span `agent.run` và `chat mock-llm-small` có status ERROR và event `exception` (`Upstream LLM error: 503 Service Unavailable (mock)`). Log cùng `trace_id`:

```json
{"ts": "2026-11-25T11:33:08.574389+00:00", "level": "INFO", "service": "observable-agent", "instance": "6205dae7ea01", "event": "llm_error", "trace_id": "c20eaffa65a03726762abb168f0c56b9", "span_id": "04603fc3761ce097", "request_id": "dd032315ebc4", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Số liệu dòng `POST /ask` trong `benchmark-results/scale_{1,3}_stats.csv`. Percentile của Locust đã được làm tròn sẵn.

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.96 | 190 | 250 | 1700 | 65 / 7161 (0.91 %) |
| 3 | 60.30 | 190 | 250 | 1700 | 84 / 7213 (1.16 %) |

**Giải thích kết quả:** Scale lên 3 instance **không cải thiện gì**, RPS và mọi percentile gần như y hệt. Trước khi chạy, mình xác nhận cả 3 instance đều nhận traffic: Prometheus thấy 3 target `up`, và 9 request thử chia đều 3/3/3 theo header `X-Served-By`.
Lý do là 1 instance chưa hề quá tải:
(1) Locust là **closed-loop**: mỗi user chờ 0.5–2 s (trung bình 1.25 s) giữa hai request, nên 100 user chỉ tạo tối đa khoảng 100 / (1.25 + 0.24) ≈ 67 req/s. Con số này cố định dù có bao nhiêu instance; RPS ~60 là trần do *load generator* đặt ra.
(2) Thời gian của mỗi request là thời gian chờ LLM (`asyncio.sleep` ~120–180 ms, và 1.7 s cho 3% request chậm), không phải CPU. Agent async nên 1 instance (0.5 CPU) giữ được hàng chục request cùng chờ: in-flight trung bình ≈ 60 req/s × 0.24 s ≈ 15.
**Nút thắt là latency của LLM upstream**: p50 ~190 ms và p99 ~1.7 s là latency của chính mock LLM (panel *LLM latency p95* ~199 ms). Thêm instance không làm LLM trả lời nhanh hơn. Scale ngang chỉ có ích khi instance bão hoà (CPU/in-flight cao, hàng đợi dài, p50 bắt đầu tăng như c=100 ở mục 2.1), hoặc khi cần chịu lỗi (thí nghiệm D: còn 1 instance vẫn phục vụ được). Muốn giảm tail latency phải xử lý ở LLM: timeout + retry, hedged request, cache, model nhanh hơn, streaming.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Đo sai còn nguy hiểm hơn không đo.** Chênh `localhost`/`127.0.0.1` trên Windows thêm ~250 ms vào p95. Locust closed-loop làm scale 1 → 3 trông như vô ích. 300 request không đủ để kết luận về error rate 2%. Trước khi tin một con số phải hiểu cách công cụ tạo ra nó.
  2. **Percentile, không phải average.** 3% request chậm làm p99 tăng 7 lần trong khi avg chỉ tăng 27%. Chất lượng cũng vậy: "cost / câu đúng" (v2 rẻ hơn 11 lần) cho kết luận ngược hẳn với "cost / câu" (v2 đắt gấp đôi).
  3. **Ba trụ cột phải nối được với nhau, và mỗi tầng chỉ thấy phần của nó.** Dashboard báo p95 tăng, Jaeger chỉ ra 98.7% thời gian nằm ở span LLM, `trace_id` dẫn tới đúng dòng log. Nhưng metric của agent không thấy 504 ở Nginx (D) và không thấy câu trả lời sai (E): cần thêm giám sát ở LB/synthetic và eval chất lượng.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Tầng LB / synthetic:** metric của Nginx (tỉ lệ 5xx/504 từ góc nhìn client), một blackbox probe gọi `/ask` từ ngoài mỗi phút, alert khi probe fail.
  - **Chất lượng:** `agent_retrieval_total` hit ratio < 50%, điểm online-eval (`agent_quality_score`), tỉ lệ 👎. Alert khi `agent_info` đổi version ngoài lịch release.
  - **SLO theo error budget** (multi-window burn rate, vd 1h và 6h) thay cho ngưỡng cứng, để giảm alert fatigue. Ngưỡng cost tính theo traffic dự kiến (cost/request × RPS × hệ số an toàn), kèm budget theo ngày như cost guard ở Part 6.
  - **Saturation:** CPU/memory của container, `agent_in_flight_requests` trên số instance, độ dài hàng đợi. Đây là tín hiệu để autoscale, vì như mục 2.1 cho thấy p50 bắt đầu tăng khi một process vượt ~c=50.
  - **LLM:** `llm_requests_total{status="error"}` theo provider, alert khi upstream lỗi > 5%, latency của token đầu tiên (TTFT) khi dùng streaming.

---

## Phụ lục — Câu trả lời Exercise 7.1 và 7.4

### Exercise 7.1 — Đọc metrics

Gọi `POST /ask` một lần với câu "Prometheus thu thập metrics như thế nào?" (v2: `input_tokens=91`, `output_tokens=55`, `cost_usd=4.665e-05`), rồi đọc `GET /metrics`:

| Loại | Ví dụ trong output |
|---|---|
| Counter | `http_requests_total{method="POST",route="/ask",status="200"} 1.0`, `llm_tokens_total{model="mock-llm-small",type="input"} 91.0`, `llm_cost_usd_total{model="mock-llm-small"} 4.665e-05` |
| Gauge | `agent_in_flight_requests 0.0`, `agent_info{model="mock-llm-small",version="v2"} 1.0` |
| Histogram | `http_request_duration_seconds_bucket{le="0.2",method="POST",route="/ask"} ...` + `_count` / `_sum`, `agent_question_length_chars_count 1.0` / `_sum 40.0` |

**Nếu dùng URL thật làm label:** mỗi URL khác nhau tạo một time series mới, nhân với số giá trị `method` × `status` × ~13 bucket của histogram. Bot quét hàng nghìn URL lạ như `/wp-admin`, `/.env`, `/a1b2c3` sẽ tạo hàng chục nghìn series rác. Prometheus tốn RAM tuyến tính theo số series, nên có thể bị OOM; query chậm; dashboard vỡ. Một client bên ngoài có thể dùng cách này để tấn công hệ thống giám sát của mình. Dùng `route.path` (template) thì mọi URL lạ gom vào 1 giá trị: mình gọi thử `GET /wp-admin` thì chỉ sinh `http_requests_total{method="GET",route="unmatched",status="404"} 1.0`.

**Nhiệm vụ:** đã thêm Histogram `agent_question_length_chars` (buckets 20…2000 ký tự) vào `develop/app.py`. Không dùng label `length=...` vì độ dài có vô số giá trị, mỗi giá trị lại thành một series, đúng vấn đề cardinality ở trên.

### Exercise 7.4 — Observability stack

Số đọc lúc 11:35:06 UTC, 30 user Locust, 2 instance (khớp ảnh `grafana-dashboard.png`):

1. **p95 = 296 ms, avg = 248 ms** (p50 174 ms, p99 1.87 s). p95 chỉ cao hơn avg ~48 ms, tức 95% request khá đồng đều. Nhưng p99 gấp 7.5 lần avg: phần đuôi ~3% request chậm của LLM bị avg che mất. Khoảng cách avg–p99 (thấy trên panel *Latency p50/p95/p99*) chính là tail latency mà SLO theo average sẽ bỏ sót.
2. **Có, chia đều.** Panel *Requests/s theo instance*: 9.36 req/s và 9.27 req/s (chênh < 1%). Nginx round-robin, mỗi request tốn thời gian tương đương nhau nên tải cân bằng. Lúc scale lên 3 instance, 9 request thử cũng chia đúng 3/3/3.
3. **Input 1450 tokens/s > output 764 tokens/s** (~1.9 lần). Input token của mỗi request gồm system prompt + tài liệu retrieve được (v2/RAG) + câu hỏi, còn output chỉ là câu trả lời ngắn. RAG làm phần input dài ra: ở thí nghiệm E (v1, không retrieval) input giảm còn ~677 tokens/s với cùng RPS. Tuy vậy, giá output đắt gấp 4 lần input (0.0006 so với 0.00015 USD/1K), nên chi phí lại nghiêng về output: 764 × 0.0006 ≈ 0.46 so với 1450 × 0.00015 ≈ 0.22 (đơn vị 0.001 USD/s), tức output chiếm ~2/3 cost.
4. `curl -i http://localhost:8080/metrics` trả **`HTTP/1.1 404 Not Found`**. `nginx.conf` có `location = /metrics { return 404; }`, nên metrics chỉ được Prometheus scrape trực tiếp qua mạng nội bộ Docker (`agent:8000`), không lộ ra ngoài. Metrics làm lộ thông tin nội bộ (route, version, model, chi phí, lưu lượng), và endpoint này không có auth. Ai cũng gọi được thì vừa rò thông tin vừa có thể bị spam.
