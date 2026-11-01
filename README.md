# NHÓM 6 — AI DATA OBSERVABILITY

**Đề tài AI 3:** Hệ thống Data Observability có AI phân tích nguyên nhân lỗi  
**Số thành viên:** 5  
**Phiên bản:** 1.0 — chương trình demo hoàn chỉnh chạy offline với dữ liệu giả lập

> **Giới hạn trung thực:** Đây là hệ thống chứng minh ý tưởng (proof-of-concept). Dataset giao dịch là dữ liệu sinh tổng hợp, không kết nối hệ thống doanh nghiệp thật; AI root-cause tạo giả thuyết dựa trên bằng chứng, không thay thế kết luận của kỹ sư. Chỉ dùng trong demo/lab nội bộ, không được công khai API chưa có xác thực.

## 1. Chạy nhanh nhất trên Windows

**Yêu cầu:** Python **3.11, 3.12 hoặc 3.13**, Internet để cài thư viện lần đầu, Windows 10/11.

1. Giải nén ZIP vào một thư mục, ví dụ `D:\AI_Data_Observability_Nhom6`.
2. Cài Python từ trang chính thức và tích ô **Add Python to PATH**.
3. **Nhấp đúp `run_demo.bat`**. File sẽ tạo `.venv`, cài package và khởi tạo dữ liệu mẫu.
4. Sau khi ba cửa sổ **FastAPI, Streamlit, Scheduler** xuất hiện, mở **http://127.0.0.1:8501**.
5. Mở tài liệu FastAPI tại **http://127.0.0.1:8000/docs**.
6. Giữ **cả ba cửa sổ CMD** đang chạy. Để dừng, nhấn **Ctrl+C trong từng cửa sổ**, đợi job hoàn tất rồi đóng cửa sổ.

Demo Task 1 mỗi 15 giây: chạy `run_demo.bat 15` trong CMD hoặc `./run_demo.bat 15` trong PowerShell. Mặc định là 300 giây; cũng có thể đặt `SCHEDULER_INTERVAL_SECONDS=15`. Worker từ chối mở bản thứ hai trong cùng thư mục project. Khi đổi chu kỳ, dừng worker cũ trước.

Hướng dẫn kiến trúc, bảng nguồn, API kiểm chứng và kịch bản trình bày 3–5 phút: **[TASK1_GUIDE.md](TASK1_GUIDE.md)**.

Lần đầu cài thư viện có thể phụ thuộc tốc độ mạng và cấu hình máy. Nếu Windows chặn chạy batch, dùng cách thủ công bên dưới.

## 2. Chạy thủ công trong CMD/PowerShell

**Windows CMD, tại thư mục project:**

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m observability.cli seed
.venv\Scripts\python.exe -m uvicorn observability.api:app --host 127.0.0.1 --port 8000
```

**Mở thêm CMD thứ hai, trong cùng thư mục:**

```bat
.venv\Scripts\python.exe -m streamlit run dashboard.py --server.address 127.0.0.1 --server.port 8501
```

**Mở CMD thứ ba cho scheduler độc lập:**

```bat
.venv\Scripts\python.exe -m observability.scheduler --interval 15
```

Chạy ngay khi khởi động rồi lặp mỗi 15 giây bằng APScheduler 3.x (`BlockingScheduler`, interval trigger). Bỏ `--interval 15` để dùng biến môi trường hoặc mặc định 300 giây. Log worker ở terminal và `logs/scheduler.log` (UTC, xoay vòng). FastAPI không khởi động scheduler và `/api/health` chỉ xác nhận API.

> Cơ sở dữ liệu mặc định là SQLite trong `data/observability.db`. **Không cần cài PostgreSQL hoặc đăng ký API AI** để trình diễn. SQLite chứa sẵn các kịch bản lịch sử nếu dùng ZIP này.

**macOS/Linux:** `bash run_demo.sh` (cần Python 3.11+).

## 3. Nếu đã cài Docker: bản có PostgreSQL và scheduler

```bash
docker compose up --build
```

Truy cập dashboard tại http://localhost:8501 và Swagger tại http://localhost:8000/docs.

- `postgres`: cơ sở dữ liệu PostgreSQL 16, lưu bằng Docker volume.
- `api`: FastAPI; tự khởi tạo lịch sử demo nếu DB trống.
- `dashboard`: Streamlit kết nối API nội bộ.
- `scheduler`: APScheduler chạy ngay lần đầu, sau đó **5 phút/lần**; đọc `SCHEDULER_INTERVAL_SECONDS`, chờ API healthy trước khi chạy. File log được gắn ra thư mục `logs/` trên máy.

Demo Docker 15 giây trong PowerShell: `$env:SCHEDULER_INTERVAL_SECONDS='15'; docker compose up --build`.

Dừng: `docker compose down`. Xóa toàn bộ dữ liệu PostgreSQL: `docker compose down -v` (chỉ khi muốn reset).

PostgreSQL demo dùng mật khẩu mẫu trong `compose.yaml` **không phù hợp triển khai production**. Cổng 8000/8501 chỉ ánh xạ về máy cục bộ.

## 4. Hệ thống làm được gì?

| Phần | Chức năng | Giải thích khi thuyết trình |
|---|---|---|
| Data Pipeline | Tạo batch, lưu ba bảng nguồn theo run, đo chất lượng, lưu lịch sử trong DB | Dữ liệu và kết quả được lưu cùng transaction |
| Data Quality | **8 quy tắc**: freshness, completeness, uniqueness, schema, validity, JS drift, amount drift, volume | Hiện observed / threshold / PASS-FAIL |
| Anomaly Detection | **IsolationForest + khoảng cách robust** học 9 đặc trưng từ lịch sử run **HEALTHY**, đánh dấu novelty | ML học không giám sát; không cần gán nhãn đầu vào |
| Drift Detection | **Jensen–Shannon Divergence** giữa phân phối payment hiện tại và baseline từ run khỏe | Phát hiện thay đổi thống kê ngay cả khi schema đúng |
| Incident | Tự mở ticket, đặt P1/P2/P3, owner, SLA và timeline | Tiếp nhận, xem nguồn lỗi, đóng incident |
| AI Root Cause Analysis | Kết hợp kiểm tra thất bại + telemetry upstream + lineage + đề xuất checklist | AI suy ra **giả thuyết có dẫn chứng**, người xác minh cuối |
| Dashboard | 5 tab, đồ thị tương tác, bảng rule, timeline, truy vết nguyên nhân | Mỗi thành viên có một tab phù hợp task |
| Lập lịch | APScheduler 3.x, CLI, Docker Compose | Worker độc lập, interval trigger, không chạy job chồng lấn |

## 5. Gợi ý trình diễn 5 thành viên (5–8 phút)

### Thành viên 1 — Lê Hồ Hoàng Long: Bài toán và kiến trúc

1. Mở tab **01 – Tổng quan & Kiến trúc**.
2. Giải thích vì sao dashboard báo cáo thường chỉ phát hiện sai ở đầu ra mà không biết lỗi ở nguồn nào.
3. Giải thích lineage `raw_orders/raw_customers/raw_payments → stg_orders → fact_orders → dashboard_sales`.
4. Chỉ ra FastAPI + SQLite/Postgres + ETL Python + scheduler + Streamlit trong sơ đồ.
5. Chạy kịch bản **Bình thường**. Điểm DQ 100/100, không mở incident.

### Thành viên 2 — Trần Ngọc Trí: Kiểm tra chất lượng dữ liệu

1. Chọn **Dữ liệu đến trễ** trong sidebar rồi nhấn **Chạy kiểm tra dữ liệu**.
2. Mở tab **02 – Data Quality**, chọn run mới.
3. Chỉ **Freshness > 60 phút** màu lỗi. Giải thích ngưỡng, SLA và source telemetry.
4. Chạy thêm **Thiếu dữ liệu** để minh họa completeness; **Trùng khóa** để minh họa uniqueness; **Đổi schema** để minh họa data contract.
5. Nêu công thức: `Completeness = ô dữ liệu quan trọng khác NULL / tổng số ô dữ liệu quan trọng`.

### Thành viên 3 — Quách Khải Hậu: Bất thường và distribution drift

1. Chạy **Distribution drift**.
2. Mở tab **03 – Anomaly & Drift**. Chỉ đồ thị **JS divergence** và **giá trị đơn trung bình**.
3. So sánh thanh toán trước và sau; nêu JS Divergence là số đo thay đổi phân phối.
4. Giải thích **IsolationForest** học dữ liệu lịch sử khỏe, chạy kiểm tra độ lệch 9 chiều; ML hỗ trợ, không thay thế rule.

### Thành viên 4 — Trần Quang Long: Cảnh báo, SLA và incident

1. Mở tab **04 – Cảnh báo & SLA**. Chọn incident mới.
2. Giải thích `OPEN → ACKNOWLEDGED → RESOLVED` và actor trong timeline.
3. Chỉ severity P1/P2/P3, người chịu trách nhiệm, SLA deadline.
4. Nhấn **Tiếp nhận**, sau đó minh họa **Đóng incident** (hành động demo).

### Thành viên 5 — Trịnh Huỳnh Nam Sơn: AI tìm nguyên nhân và triển khai

1. Chạy **Lỗi upstream** và mở tab **05 – AI Root Cause**.
2. Chọn incident liên quan. Nêu căn cứ `raw_payments.status = ERROR`, thiếu `amount/payment_method`.
3. Giải thích kết luận AI là **giả thuyết có bằng chứng**, ưu tiên cao chứ không phải độ chính xác dự đoán đã đo.
4. Theo checklist: kiểm tra nguồn → đối soát dữ liệu → sửa pipeline → chạy lại → xác minh.
5. Giới thiệu triển khai Postgres + Docker + scheduler và các bổ sung khi production.

## 6. Dữ liệu mẫu & các kịch bản test

| Kịch bản | Lỗi xuất hiện | Expected check |
|---|---|---|
| `normal` | Bản ghi giao dịch hợp lệ | 8/8 PASS |
| `late_data` | Thời gian sự kiện cũ ~180–240 phút | freshness FAIL |
| `null_values` | 28% amount, 12% customer_id bị thiếu | completeness FAIL |
| `duplicate_keys` | 65 order_id trùng | uniqueness FAIL |
| `schema_change` | `customer_id` đổi thành `customer_key` | schema FAIL |
| `distribution_drift` | Giá trị amount gấp 3, tỷ lệ bank_transfer ~92% | amount drift & JS drift FAIL |
| `upstream_failure` | raw_payments báo lỗi; amount/payment thiếu | completeness, validity FAIL |
| `volume_spike` | Số dòng từ 400 lên 1200 | volume FAIL |

Có thể chạy bằng lệnh:

```bash
python -m observability.cli run --scenario schema_change
python -m observability.cli run --scenario upstream_failure
python -m observability.cli run --scenario normal
```

> Khi chạy thủ công bằng `python`, hãy chắc chắn đó là Python thuộc `.venv` đã cài dependencies.

Tạo lại dữ liệu demo (xóa toàn bộ lịch sử cũ):

```bash
python -m observability.cli reset
```

Trong ZIP có sẵn `data/latest_orders.csv`: **dữ liệu được sinh hoàn toàn để demo**. File cập nhật sau mỗi lần chạy.

Các run mới còn lưu `raw_orders`, `raw_customers`, `raw_payments` theo `run_id`. `raw_orders.payload_json` giữ nguyên tên cột thực tế và NULL; khóa kỹ thuật cho phép lưu cả `order_id` trùng. Customer/payment là dữ liệu giả lập suy ra từ batch, không phải connector upstream độc lập. Telemetry upstream (ví dụ 8.000 customers hoặc 140 payments trong kịch bản lỗi) vẫn là mô phỏng, không phải số dòng đã lưu. `stg_orders` và `fact_orders` vẫn là các bước logic trên batch trong bộ nhớ, không phải bảng SQL mới.

Database cũ được bổ sung bảng bằng `create_all()`, không xóa hay sửa các bảng hiện hữu. Run cũ không được tự tạo lại snapshot: endpoint nguồn trả `snapshot_available=false`. Chạy pipeline mới để có dữ liệu nguồn kiểm chứng; không cần reset.

## 7. REST API

| Verb | URL | Mục đích |
|---|---|---|
| GET | `/api/health` | Kiểm tra server |
| GET | `/api/summary` | Chỉ số tổng quan |
| GET | `/api/runs` | Lịch sử chạy |
| POST | `/api/runs` | Chạy mô phỏng `{"scenario":"schema_change"}` |
| GET | `/api/runs/{id}` | Rule chi tiết và sample |
| GET | `/api/runs/{id}/sources?source=raw_orders&limit=50&offset=0` | Payload nguồn đã lưu, số dòng thực tế, phân trang (tối đa 500 dòng) |
| GET | `/api/metrics/trend` | Metrics phục vụ biểu đồ |
| GET | `/api/incidents` | Danh sách sự cố |
| GET | `/api/incidents/{id}` | Incident + timeline + AI RCA |
| POST | `/api/incidents/{id}/acknowledge` | Tiếp nhận |
| POST | `/api/incidents/{id}/resolve` | Đóng |
| GET | `/api/alerts` | Cảnh báo dashboard |
| GET | `/api/lineage` | Danh sách node/edge phụ thuộc |
| POST | `/api/seed?reset=true` | Khởi tạo lại demo (local only) |

**Thử API**: mở http://localhost:8000/docs → chọn `POST /api/runs` → `Try it out` → chọn scenario → `Execute`.

## 8. Kiến trúc thư mục

```text
AI_Data_Observability_Nhom6/
├─ dashboard.py                 # Giao diện Streamlit: 5 phần demo
├─ observability/
│  ├─ api.py                    # FastAPI REST
│  ├─ settings.py               # Ngưỡng cảnh báo và cấu hình
│  ├─ database.py               # SQLAlchemy: runs/checks/incidents/timeline
│  ├─ generator.py              # Dữ liệu mô phỏng, upstream telemetry
│  ├─ quality.py                # Data quality và Jensen-Shannon Divergence
│  ├─ anomaly.py                # Machine Learning: IsolationForest
│  ├─ rca.py                    # AI assisted explainable root cause
│  ├─ service.py                # Pipeline và các xử lý nghiệp vụ
│  ├─ scheduler.py              # APScheduler BlockingScheduler, interval
│  ├─ sources.py                # Nạp/truy xuất snapshot nguồn theo run
│  ├─ logging_config.py         # Terminal + RotatingFileHandler
│  ├─ worker_lock.py            # Khóa chống worker trùng trong cùng project
│  └─ cli.py                    # CLI seed/reset/run
├─ data/
│  ├─ observability.db          # SQLite demo có sẵn dữ liệu
│  └─ latest_orders.csv         # Lô dữ liệu mẫu gần nhất
├─ tests/test_system.py        # Kiểm thử tự động
├─ requirements.txt
├─ run_demo.bat                 # Chạy nhanh Windows
├─ start_scheduler.bat          # Worker độc lập, hỗ trợ --interval 15
├─ TASK1_GUIDE.md               # Hướng dẫn và kịch bản demo Task 1
├─ run_demo.sh                  # Chạy nhanh macOS/Linux
├─ Dockerfile
└─ compose.yaml                 # Postgres + API + Dashboard + scheduler
```

## 9. Kiểm thử và tính đúng đắn

```bash
python -m pip install -r requirements-test.txt
python -m pytest -q
```

Các bài test kiểm tra 8 kịch bản, incident/timeline/RCA, REST API, snapshot nguồn, nâng cấp DB cũ, rollback, log, khóa worker, interval thực thi theo đồng hồ giả và khôi phục sau exception. Có smoke test tiến trình worker thật với hai lượt chạy chu kỳ 1 giây trên DB tạm. Test không sửa DB demo hoặc CSV hiện có. Cấu trúc bảng PostgreSQL được kiểm tra bằng biên dịch DDL; điều đó không thay cho kiểm thử tích hợp PostgreSQL thật.

**Công thức cốt lõi:**

- Freshness lag: thời gian hiện tại trừ event_time mới nhất.
- Completeness: tỷ lệ giá trị không null trong các cột bắt buộc.
- Uniqueness: `1 - duplicated(order_id)/row_count`.
- JS Divergence: trung bình hai KL-divergence tới phân phối trung gian, có miền [0,1].
- ML novelty: tính IsolationForest decision score và độ lệch robust có ngưỡng theo từng đặc trưng so với healthy history; nhằm phát hiện tín hiệu mạnh ngay khi dữ liệu lịch sử còn ít.
- Điểm chất lượng: số rule đạt / tổng số rule * 100.
- RCA: ưu tiên giả thuyết dựa trên *kết quả phép kiểm tra + telemetry upstream + sơ đồ phụ thuộc*; không dựa vào nhãn scenario.

## 10. Giới hạn và hướng phát triển

- Chưa có ingestion thật, CDC/Kafka connector hoặc dbt chính thức; dùng **Python ETL thay thế dbt** vì mục tiêu demo dễ chạy.
- Dashboard không có đăng nhập; API `seed/reset` và hành động incident không được dùng trên mạng công cộng nếu chưa kiểm soát truy cập.
- Alerts hiện ở dashboard, không gửi email, SMS, Slack thật.
- SLA thực tế thường kết hợp lịch hoạt động và lịch làm việc; phiên bản demo tính theo số phút tĩnh.
- Không phải tất cả bất thường đều do lỗi: anomaly và drift cần data owner xác thực.
- Chưa đánh giá độ chính xác RCA trên bộ incident có nhãn; `confidence` là **điểm ưu tiên heuristic**, không phải xác suất được hiệu chuẩn.
- Dữ liệu hiện dùng một fact_orders, cần mở rộng registry đa dataset, baseline theo chu kỳ, API auth/RBAC, CI/CD, observability cho chính pipeline, test tách train/test, bias theo mùa vụ, data retention và quản lý secrets nếu đưa lên thật.

## 11. Tài liệu tham khảo kỹ thuật chính thức

- FastAPI: https://fastapi.tiangolo.com/
- Streamlit: https://docs.streamlit.io/develop/concepts/architecture/run-your-app
- scikit-learn Isolation Forest: https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html
- SQLAlchemy: https://docs.sqlalchemy.org/en/20/
- APScheduler 3.x: https://apscheduler.readthedocs.io/en/3.x/userguide.html
- Docker Compose: https://docs.docker.com/compose/
- PostgreSQL: https://www.postgresql.org/docs/

**Liên hệ với yêu cầu 5 task:** Tab 1 = kiến trúc / bài toán; tab 2 = chất lượng; tab 3 = anomaly/drift; tab 4 = cảnh báo/SLA/timeline; tab 5 = AI RCA / checklist / triển khai.
