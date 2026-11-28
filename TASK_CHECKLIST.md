# Checklist kiểm chứng 5 task — Nhóm 6, AI Data Observability

Đối chiếu yêu cầu với code và test. `PASS` là phần đã được kiểm thử trong môi trường local/DB tạm; `MOCK PASS` là nhánh giả lập được đánh dấu rõ; `TĨNH` không có nghĩa đã deploy thật. Ngày 22/11/2026, nhánh main local và remote cùng commit `3f336ae` trước khi sửa Task 5. Thay đổi mới nằm trong workspace, chưa push lên GitHub.

| Task | Yêu cầu | File triển khai | Công nghệ | Trạng thái | Cách kiểm chứng |
|---|---|---|---|---|---|
| 1 | Có ba bảng nguồn, PK kỹ thuật và FK tới run | `observability/database.py` | SQLAlchemy | PASS SQLite; DDL PostgreSQL | `test_task1.py::test_source_tables_and_postgres_ddl` |
| 1 | Nạp dữ liệu giả lập, lưu theo run | `generator.py`, `sources.py`, `service.py` | pandas, ORM | PASS | `test_normal_pipeline_snapshots_and_history`; `test_all_scenarios_persist_monitoring_context` |
| 1 | Không sửa NULL/trùng/schema lỗi trước khi lưu | `sources.py` | JSON payload, khóa không UNIQUE nghiệp vụ | PASS | `test_faults_survive_source_storage` |
| 1 | Phân biệt snapshot và upstream telemetry | `sources.py`, `generator.py`, `dashboard.py` | DEMO telemetry, stored_counts | PASS | `test_faults_survive_source_storage[upstream_failure-validity]` |
| 1 | API chạy thủ công và xem nguồn/lịch sử | `api.py`, `service.py`, `sources.py` | FastAPI, Pydantic | PASS | `test_source_api_preserves_existing_routes`; `test_api_routes` |
| 1 | APScheduler độc lập, interval và lần đầu ngay | `scheduler.py` | BlockingScheduler 3.x | PASS | `test_scheduler_real_dispatch_and_recovers_after_error`; `test_scheduler_cli_env_override_and_graceful_shutdown` |
| 1 | Không chạy job chồng; khóa worker local | `scheduler.py`, `worker_lock.py` | max_instances=1, coalesce, OS lock | PASS | `test_scheduler_skips_overlap`; `test_worker_lock_rejects_second_process_and_releases` |
| 1 | Job lỗi vẫn có chu kỳ kế tiếp | `scheduler.py` | Exception handling | PASS | `test_scheduler_real_dispatch_and_recovers_after_error` |
| 1 | Log start/end/run/status/rows/duration, không lặp handler | `service.py`, `logging_config.py` | logging, RotatingFileHandler | PASS | `test_logging_is_idempotent_and_contains_run_metadata`; `test_snapshot_failure_rolls_back_transaction` |
| 1 | Sự kiện thực thi DB cho context RCA | `database.py::PipelineEvent`, `service.py` | STARTED/SNAPSHOT_SAVED/FINISHED | PASS | `test_full_workflow.py::test_all_scenarios_persist_monitoring_context` |
| 1 | Lịch sử còn sau mở lại; DB cũ năm bảng dùng được | `database.py`, `service.py` | create_all thêm bảng | PASS | `test_statistics_recovery_and_reopen_history`; `test_legacy_exact_five_table_upgrade_preserves_history` |
| 1 | Scheduler 15 giây thật, API đọc được hai run | `scheduler.py`, `api.py` | Worker process thật | PASS | `test_live_schedule.py::test_real_fifteen_second_schedule_visible_in_api` — chờ một interval thật |
| 1 | Dashboard cập nhật khi dữ liệu đổi, xem lịch sử/nguồn | `dashboard.py`, `api.py`, `service.py` | Polling fragment 15s, revision token | PASS AppTest; chưa xác minh bằng mắt | `test_dashboard_refresh.py` kiểm tra mặc định thủ công, tick không đổi chỉ gọi revision, lỗi polling an toàn, dữ liệu mới cập nhật |
| 2 | Freshness ≤60 phút | `quality.py`, `settings.py` | Rule ngưỡng cấu hình | PASS | `test_system.py::test_quality_detects_scenario[late_data-freshness]` |
| 2 | Completeness ≥98% | `quality.py`, `settings.py` | Tỷ lệ non-NULL | PASS | `test_quality_detects_scenario[null_values-completeness]` |
| 2 | Uniqueness ≥99,5% | `quality.py`, `settings.py` | duplicated(order_id) | PASS | `test_quality_detects_scenario[duplicate_keys-uniqueness]` |
| 2 | Schema đúng contract | `quality.py`, `generator.py` | Cột thiếu/thêm | PASS | `test_quality_detects_scenario[schema_change-schema]`; test snapshot replay |
| 2 | Ngưỡng so sánh và nhãn thống nhất | `settings.py`, `quality.py` | DQ_* environment, threshold snapshot | PASS | `test_full_workflow.py::test_threshold_labels_use_configured_values`; `/api/config` |
| 2 | Lưu observed/expected/PASS/FAIL/severity/details/run/time | `database.py`, `service.py`, `dashboard.py` | quality_checks + thời điểm run hoàn tất | PASS | `test_all_scenarios_persist_monitoring_context`; tab 02 |
| 3 | Z-score row_count và amount_mean | `statistics.py` | Mean, population std, Z-score | PASS | `test_statistics.py::test_zscore_contract_population_std_and_anomalous_aggregate` |
| 3 | Baseline chỉ HEALTHY, không học run hiện tại/tương lai/dataset khác | `statistics.py`, `service.py` | Lọc history | PASS | `test_statistics_wrapper_settings_history_exclusion_and_transaction`; `test_statistics_recovery_and_reopen_history` |
| 3 | Không đủ lịch sử và phương sai 0 | `statistics.py` | Trạng thái rõ, JSON null thay Infinity | PASS | `test_zscore_minimum_history`; `test_zscore_exact_zero_variance_without_epsilon`; `test_zscore_finite_extremes_cannot_emit_infinity` |
| 3 | KS dùng amount snapshot, so với run ngay trước | `statistics.py` | scipy.stats.ks_2samp | PASS | `test_ks_uses_previous_id_same_dataset_even_if_failed_not_timestamp`; fixture preview cố ý khác snapshot |
| 3 | NULL/thiếu mẫu/legacy snapshot | `statistics.py` | Lọc hữu hạn, skip có lý do | PASS | `test_ks_excludes_null_nonfinite_and_obeys_minimum`; `test_ks_legacy_snapshot_is_not_replaced_by_older_run` |
| 3 | D và p-value riêng; phân biệt ý nghĩa/ảnh hưởng | `statistics.py`, `dashboard.py` | alpha + effect_threshold | PASS | `test_ks_distance_is_separate_from_significance`; `test_ks_diagnostic_alert_does_not_change_zscore_health` |
| 3 | Giữ IsolationForest và JS | `anomaly.py`, `quality.py` | IsolationForest/RobustScaler, JS | PASS | Test cũ 8 scenario, drift; `test_all_scenarios_persist_monitoring_context` |
| 3 | Biểu đồ đánh dấu anomaly và lịch sử KS | `dashboard.py` | Plotly bar/scatter, marker riêng | PASS render; chưa kiểm tra bằng mắt | `test_dashboard_five_tabs_render_against_real_api`; tab 03 |
| 4 | Incident tự động, P1/P2/P3, owner, SLA | `service.py`, `database.py` | Incident/Alert ORM | PASS | `test_seed_and_incident_workflow`; `test_pipeline_notifications_and_incident_lifecycle` |
| 4 | Email mock và Slack mock lưu nội dung/thời gian/trạng thái/incident | `notifications.py`, `database.py::Alert` | email_mock/slack_mock, MOCK_SENT | MOCK PASS | `test_creates_mock_alerts_and_matching_events`; test tích hợp notification |
| 4 | Mock không truy cập mạng, không trùng khi retry tuần tự | `notifications.py` | Ghi DB trong transaction caller | MOCK PASS | `test_creates_mock_alerts_and_matching_events` chặn socket; `test_retries_skip_existing_channels_before_and_after_commit` |
| 4 | Rollback cả thông báo và event khi ghi lỗi | `notifications.py` | Transaction | PASS | `test_caller_rollback_removes_alerts_and_events`; `test_event_insert_failure_rolls_back_both_channels` |
| 4 | OPEN → ACK → xử lý DEMO → RESOLVED | `service.py`, `api.py`, `dashboard.py` | IncidentEvent timeline | PASS | `test_all_scenarios_persist_monitoring_context`; tab 04 có nút ghi bước xử lý |
| 4 | Xem từng notification và lịch sử sự cố | `dashboard.py`, `service.py` | Bảng/expander/timeline | PASS render | `test_dashboard_five_tabs_render_against_real_api` kiểm tra expander email_mock |
| 5 | Giữ RCA cũ, thêm LLM cấu hình provider | `rca.py`, `llm_rca.py`, `service.py` | HTTP Chat Completions compatible | MOCK PASS; provider thật chưa thử | `test_llm_success_persisted_then_timeout_restores_fallback`; `test_success_uses_runtime_config_and_only_sanitized_context` |
| 5 | Offline deterministic không key/Internet, nguồn phân tích rõ | `llm_rca.py`, `dashboard.py` | RULE_BASED_FALLBACK / LLM | PASS | `test_offline_is_deterministic_and_preserves_actual_rule_reason`; test AppTest |
| 5 | Context có run/incident/checks/severity/logs/upstream/lineage/ML/Z/KS | `llm_rca.py::build_context`, `service.py` | Aggregate allowlist | PASS | `test_context_preserves_aggregate_statistics_logs_and_excludes_private_data`; `test_all_scenarios_persist_monitoring_context` |
| 5 | Không bịa log, không gửi giao dịch/key | `llm_rca.py`, `database.py::PipelineEvent` | Lọc field, evidence ID, secret guard | PASS | `test_secret_in_allowlisted_node_or_output_is_blocked`; `test_success_uses_runtime_config_and_only_sanitized_context` |
| 5 | Bật gửi ngoài bằng cấu hình cho phép rõ | `llm_rca.py` | MODE + ALLOW_EXTERNAL | PASS | `test_external_requires_explicit_optin`; `test_invalid_config_never_requests` |
| 5 | Timeout/lỗi provider/JSON → fallback | `llm_rca.py` | requests timeout, error codes | MOCK PASS | `test_request_errors_are_safe_and_never_retried`; `test_http_errors_never_expose_body_or_retry`; test tích hợp timeout |
| 5 | JSON Pydantic, node/evidence hợp lệ, cần người xác minh | `llm_rca.py::LLMAnalysis` | Pydantic strict, Literal True, grounding | PASS | `test_invalid_or_ungrounded_output_falls_back`; `test_malformed_chat_envelopes` |
| 5 | Lưu kết quả và context, phân tích lại không phình JSON | `service.py`, `llm_rca.py` | root_cause_json mở rộng | PASS | `test_new_endpoints_and_reanalysis_are_bounded`; `test_reanalysis_strips_previous_nested_analysis_and_keeps_size_bounded` |
| 5 | Tóm tắt/upstream/bằng chứng/giả thuyết/checklist trên tab 05 | `dashboard.py` | Streamlit AppTest | PASS LLM mock và fallback | `test_task5_dashboard.py::test_llm_success_renders_without_fallback_reason`; `test_offline_without_key_shows_complete_report` |
| 5 | Chưa có incident và không giữ dữ liệu incident cũ | `dashboard.py` | Render có phạm vi riêng | PASS | `test_no_incident_and_no_previous_incident_state`; `test_incident_disappears_without_reusing_previous_ui` |
| 5 | Phân tích lại: POST thành công, GET DB mới và hiển thị đúng nguồn | `dashboard.py`, `service.py` | Lưu analyzed_at, đọc lại dữ liệu | PASS | `test_click_reanalyze_reads_latest_committed_result` |
| 5 | Provider lỗi hiển thị fallback; API lỗi không giả thành công | `dashboard.py`, `llm_rca.py` | AppTest, mock lỗi | PASS | `test_provider_failure_reanalyzes_to_visible_fallback`; `test_api_failure_has_error_and_preserves_prior_result`; `test_post_commits_but_refresh_fails_does_not_claim_success` |
| 4 | Incident đóng trễ vẫn ghi nhận vi phạm SLA | `database.py` | So sánh resolved_at với deadline | PASS | `test_task5_dashboard.py::test_resolved_sla_uses_resolution_time` — trước/đúng/sau hạn |
| 5 | Dockerfile và Compose PostgreSQL/API/Streamlit/scheduler | `Dockerfile`, `compose.yaml` | Docker Compose, volume, healthcheck | TĨNH; chưa deploy thật | Kiểm tra YAML, bốn service, điều kiện API healthy; môi trường không có Docker |

## Cách chạy kiểm chứng

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-test.txt
.venv/Scripts/python.exe -m compileall -q observability tests dashboard.py
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m pip check
```

Tất cả test dùng DB tạm. Các test kiểm tra reset chủ động chỉ thao tác DB tạm, không gọi reset database nhóm. Worker smoke test chuyển cả database, CSV, lock và log sang thư mục tạm. Test live scheduler chờ khoảng 15 giây; các test scheduler còn lại dùng đồng hồ giả hoặc chu kỳ 1 giây.

## Kết quả mới nhất — sửa refresh dashboard ngày 27/11/2026

Cập nhật hiển thị thời gian sau vòng toàn suite: `observability/display.py` chuyển UTC sang giờ Việt Nam UTC+7, dùng ở bảng lịch sử/DQ/SLA/timeline/cảnh báo/RCA. API/DB và JSON kỹ thuật giữ UTC. `tests/test_display_time.py` kiểm tra offset, chuyển ngày và AppTest không thay đổi API/DB. Phạm vi đã chạy lại cùng RCA/refresh: **31 passed, 2 warnings in 17.38s**. Kết quả toàn suite dưới đây được chạy trước sửa hiển thị thời gian; chưa chạy lại toàn suite sau thay đổi này.

Kết quả chạy thật:

```text
.venv/Scripts/python.exe -m pytest -q
183 passed, 2 warnings in 49.42s
```

Bao gồm 181 test có sẵn và 2 test mới trong `test_dashboard_refresh.py`. Kiểm tra mặc định tắt auto refresh, nút thủ công hoạt động, tick không có thay đổi chỉ gọi endpoint revision, timeout polling không crash và run mới cập nhật dữ liệu; revision đổi theo thao tác incident và SLA. Không sửa assertion cũ hoặc bỏ qua test lỗi.

2 cảnh báo gồm Starlette TestClient/httpx deprecation và Streamlit/pandas/NumPy timedelta deprecation. Không có test thất bại; không ẩn cảnh báo để làm đẹp kết quả.

- Dependencies đã có trong `.venv`: Python 3.13.0rc2, pytest 9.2.0, Streamlit 1.65.0, FastAPI 0.143.0.
- `compileall`: thành công; `pip check`: `No broken requirements found`; `git diff --check`: thành công.
- `bash -n run_demo.sh`: thành công.
- AppTest + FastAPI TestClient: Task 5 được render qua API/DB thật trong tiến trình test, chỉ provider bên ngoài là mock; API Task 1–4 và worker 15 giây vẫn qua toàn suite.
- Compose YAML: kiểm tra tĩnh bốn service, config dùng chung và điều kiện healthcheck thành công. Không có Docker để xác minh runtime.

SHA-256 database demo trước và sau phiên sửa Task 5 cùng là `DF341657767D0588881BDAAEBA6662501FDEDD7882BDF5FD783352061CED83F3`. Không reset/ghi DB nhóm hoặc sửa `.env`; không gọi provider bằng key người dùng. Context ghi rõ sự kiện DB (`pipeline_events`), không giả là đã đọc file log, và upstream telemetry là DEMO.

## Những phần chưa xác minh

- PostgreSQL runtime và `docker compose up --build`: chưa có Docker/PostgreSQL server; kiểm tra DDL/YAML không thay thế triển khai thật.
- Provider LLM thật: không có cấu hình/key được sử dụng trong phiên; chỉ kiểm thử HTTP mock, validation, timeout và fallback.
- Giao diện trong trình duyệt: đã dùng AppTest, chưa đánh giá trực quan hoặc đo chu kỳ refresh qua browser. Worker 15 giây được kiểm chứng độc lập bằng run timestamp và lịch sử API.
- Nguồn là DEMO tổng hợp, telemetry upstream là mô phỏng; email/Slack không gửi thật. RCA là giả thuyết; incident RESOLVED ở demo không chứng minh pipeline đã được sửa.
- Khóa worker chỉ cùng filesystem/project; dedup notification chỉ retry tuần tự, chưa bảo vệ nhiều caller đồng thời. API chưa có auth/RBAC và demo chỉ dành cho môi trường nội bộ.

## Khởi động và demo

PowerShell: `./run_demo.bat 15`; mở dashboard `http://127.0.0.1:8501`, Swagger `http://127.0.0.1:8000/docs`. Nếu đủ dependencies có thể chạy ba file start riêng. Chỉ chạy một worker local. Kịch bản 5 task, cấu hình ngưỡng, LLM và offline ở README mục 13–16; chi tiết Task 1 ở TASK1_GUIDE.
