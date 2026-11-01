"""Streamlit dashboard, all content in Vietnamese for a 5-member presentation."""
import os
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

from observability.generator import SCENARIOS

st.set_page_config(page_title="AI Data Observability | Nhóm 6", page_icon="📊", layout="wide", initial_sidebar_state="expanded")

API = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")

st.markdown("""
<style>
 .stApp {background: #f6f8fc; color: #182947;}
 h1,h2,h3 {color:#182947;}
 div[data-testid="stMetric"] {background:#fff; border:1px solid #e5eaf3; border-radius:12px; padding:14px 16px; box-shadow:0 3px 12px #15234709;}
 .hero {background:linear-gradient(110deg,#112f59,#2662a8); padding:25px 30px; border-radius:16px; margin-bottom:15px; color:white;}
 .hero h1 {color:white; font-size:2.05rem; margin:0 0 6px 0;}
 .hero p {font-size:1rem; color:#dce9fa; margin:0;}
 .section-note {font-size:.9rem; color:#536885;}
 .chip {padding:4px 10px; border-radius:16px; display:inline-block; background:#e8effa; color:#254c84; font-weight:600;}
 </style>
""", unsafe_allow_html=True)


def request_api(path, method="GET", payload=None, timeout=25):
    try:
        rsp = requests.request(method, f"{API}{path}", json=payload, timeout=timeout)
        rsp.raise_for_status()
        return rsp.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"Không thể kết nối API {API}: {exc}") from exc


def datatable(df, key=None):
    st.dataframe(df, hide_index=True, use_container_width=True, key=key)


def percent(v):
    return f"{float(v)*100:.1f}%"


def title_note(title, note):
    st.subheader(title)
    st.caption(note)


with st.sidebar:
    st.markdown("### 🛰️ DATA OBSERVABILITY")
    st.caption("NHÓM 6 · ĐỀ TÀI AI 3 · DEMO")
    st.divider()
    st.markdown("**Điều khiển kịch bản**")
    selected_scenario = st.selectbox(
        "Chọn lỗi để mô phỏng", options=list(SCENARIOS.keys()),
        format_func=lambda x: SCENARIOS[x], index=0,
    )
    run_clicked = st.button("▶ Chạy kiểm tra dữ liệu", type="primary", use_container_width=True)
    st.caption("Kết quả chạy sẽ tự sinh bản ghi, kiểm tra, cảnh báo và phân tích nguyên nhân.")
    st.divider()
    refresh_clicked = st.button("↻ Làm mới dashboard", use_container_width=True)
    st.markdown("[Tài liệu REST API / Swagger](http://localhost:8000/docs)")
    st.caption("API: " + API)
    st.divider()
    st.caption("Kỹ thuật: FastAPI · SQLAlchemy · Streamlit · scikit-learn · PostgreSQL/SQLite")

if run_clicked:
    try:
        result = request_api("/api/runs", "POST", {"scenario": selected_scenario}, timeout=60)
        if result["status"] == "HEALTHY":
            st.success(f"Run #{result['id']} hoàn tất — hệ thống không phát hiện lỗi. Điểm chất lượng: {result['quality_score']}.")
        else:
            st.warning(f"Run #{result['id']} có bất thường — tạo incident #{result['incident_id']}. Chuyển đến tab RCA để xem nguyên nhân.")
    except RuntimeError as error:
        st.error(str(error))

st.markdown("""
<div class="hero">
<h1>AI-POWERED DATA OBSERVABILITY</h1>
<p>Giám sát dữ liệu • Kiểm tra chất lượng • Drift Detection • Incident Timeline • AI Root Cause Analysis</p>
</div>
""", unsafe_allow_html=True)

try:
    summary = request_api("/api/summary")
    runs = request_api("/api/runs?limit=100")
    incidents = request_api("/api/incidents?limit=100")
    trends = request_api("/api/metrics/trend?limit=100")
    alerts = request_api("/api/alerts")
except RuntimeError as error:
    st.error(str(error))
    st.info("Bật FastAPI trước bằng: `python -m uvicorn observability.api:app --reload --port 8000` rồi làm mới trang.")
    st.stop()

if not runs:
    st.warning("Hệ thống chưa có dữ liệu. Khởi tạo bằng `python -m observability.cli seed`, rồi nhấn Làm mới.")
    st.stop()

latest = summary["latest_run"]
trend_df = pd.DataFrame(trends)
columns = st.columns(5)
columns[0].metric("Pipeline đã chạy", summary["runs_total"])
columns[1].metric("Tỷ lệ thành công", f"{summary['success_rate']}%")
columns[2].metric("Điểm chất lượng TB", f"{summary['quality_average']}/100")
columns[3].metric("Incident đang mở", summary["active_incidents"])
columns[4].metric("Run gần nhất", f"#{latest['id']} · {latest['status']}")

st.write("")
tab_overview, tab_quality, tab_anomaly, tab_alert, tab_ai = st.tabs([
    "01 · Tổng quan & Kiến trúc", "02 · Data Quality", "03 · Anomaly & Drift",
    "04 · Cảnh báo & SLA", "05 · AI Root Cause",
])

with tab_overview:
    left, right = st.columns([1.7, 1], gap="large")
    with left:
        title_note("Lịch sử chất lượng pipeline", "Điểm chất lượng theo từng lần chạy, từ 0 đến 100")
        chart = px.line(trend_df, x="run_id", y="quality_score", markers=True,
                        labels={"run_id": "Mã run", "quality_score": "Điểm DQ"},
                        color_discrete_sequence=["#1b5aa6"])
        chart.update_traces(line_width=3, marker_size=8)
        chart.update_layout(height=330, margin=dict(l=10,r=10,b=10,t=10), yaxis_range=[-3,103], plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(chart, use_container_width=True)
    with right:
        title_note("Tình trạng xử lý", "Run lỗi tự động mở ticket và đánh giá SLA")
        plot = px.pie(
            names=["Khỏe mạnh", "Có lỗi"],
            values=[sum(1 for r in runs if r["status"] == "HEALTHY"), sum(1 for r in runs if r["status"] != "HEALTHY")],
            hole=.62, color_discrete_sequence=["#2ca87a", "#eb6676"],
        )
        plot.update_layout(height=330, margin=dict(l=10,r=10,b=20,t=0), legend=dict(orientation="h",y=-.08))
        st.plotly_chart(plot, use_container_width=True)
    st.subheader("Kiến trúc xử lý")
    st.code("Nguồn giao dịch (raw_orders, raw_customers, raw_payments)\n     ↓   Python ETL / Data Quality Engine\n     ↓   Lớp stg_orders → fact_orders (lưu lịch sử ở PostgreSQL/SQLite)\n     ↓   Isolation Forest + Drift Detection + Explainable RCA\n     ↓   FastAPI REST → Streamlit Dashboard / Incident / SLA", language="text")
    st.markdown("**Data Lineage**")
    nodes = ["raw_orders", "raw_customers", "raw_payments", "stg_orders", "fact_orders", "dashboard_sales"]
    source = [0,1,2,3,4]
    target = [3,3,3,4,5]
    fig = go.Figure(go.Sankey(
        arrangement="snap", node=dict(label=nodes, pad=25, thickness=18, color=["#4c82bd"]*3+["#29a98b","#254d85","#8695b4"]),
        link=dict(source=source,target=target,value=[1,1,1,3,3],color="rgba(64,124,183,.23)"),
    ))
    fig.update_layout(height=275, margin=dict(l=5,r=5,t=5,b=5), font_size=13)
    st.plotly_chart(fig, use_container_width=True)
    with st.expander("Các lần chạy gần đây", expanded=False):
        df = pd.DataFrame([{ "Run": r["id"], "Kịch bản": r["scenario"], "Kết quả": r["status"], "Số dòng": r["row_count"], "Điểm DQ": r["quality_score"], "Thời điểm": r["finished_at"]} for r in runs[:30]])
        datatable(df)

with tab_quality:
    title_note("Kiểm tra chất lượng dữ liệu", "Freshness · Completeness · Uniqueness · Schema · Validity · Drift · Amount · Volume")
    run_ids = [r["id"] for r in runs]
    chosen_quality_id = st.selectbox("Chọn lần chạy để xem 8 phép kiểm tra", run_ids, format_func=lambda rid: f"Run #{rid} · {next(x['scenario'] for x in runs if x['id']==rid)}", key="run_quality")
    run_detail = request_api(f"/api/runs/{chosen_quality_id}")
    metrics = run_detail["metrics"]
    a,b,c,d = st.columns(4)
    a.metric("Freshness", f"{metrics['freshness_minutes']} phút")
    b.metric("Completeness", percent(metrics["completeness"]))
    c.metric("Uniqueness", percent(metrics["uniqueness"]))
    d.metric("Schema", "Đạt" if metrics["schema_match"] else "Không đạt")
    df_checks = pd.DataFrame([{
        "Trạng thái": "✅ ĐẠT" if check["passed"] else "❌ LỖI",
        "Tiêu chí": check["title"], "Quan sát": check["observed"],
        "Ngưỡng": check["expected"], "Mức độ": check["severity"],
        "Giải thích": check["details"],
    } for check in run_detail["checks"]])
    datatable(df_checks)
    st.caption("Điểm DQ = (số rule đạt / 8) × 100. Đây là thang điểm demo, không phải chứng nhận chất lượng sản xuất.")
    st.markdown("**Schema thực tế**")
    st.code(", ".join(run_detail["schema"]), language="text")
    with st.expander("Xem 12 bản ghi mẫu (dữ liệu tổng hợp, không phải dữ liệu thật)"):
        datatable(pd.DataFrame(run_detail["preview"]))
    st.markdown("**Sự cố nguồn upstream tại thời điểm chạy**")
    datatable(pd.DataFrame([{"Nguồn": source, **info} for source,info in run_detail["upstream"].items()]))

with tab_anomaly:
    title_note("Phát hiện bất thường bằng Machine Learning", "IsolationForest học đặc trưng 9 chiều từ các run khỏe mạnh; kiểm tra drift riêng bằng Jensen–Shannon Divergence")
    a,b = st.columns([1.25,1], gap="large")
    with a:
        feature = st.selectbox("Chọn chỉ số để quan sát", [
            "freshness_minutes", "completeness", "uniqueness", "amount_mean", "distribution_js", "row_count", "amount_mean_ratio", "volume_ratio",
        ], format_func=lambda x: {
            "freshness_minutes":"Độ trễ (phút)", "completeness":"Completeness", "uniqueness":"Uniqueness",
            "amount_mean":"Giá trị đơn trung bình", "distribution_js":"JS divergence", "row_count":"Số dòng",
            "amount_mean_ratio":"Biến động amount / baseline", "volume_ratio":"Volume / baseline",
        }[x])
        color = ["#2ca87a" if x == "HEALTHY" else "#eb6676" for x in trend_df["status"]]
        fig = go.Figure(go.Bar(x=trend_df["run_id"], y=trend_df[feature], marker_color=color, name=feature))
        fig.update_layout(height=355, xaxis_title="Run ID", yaxis_title=feature, margin=dict(l=10,r=10,t=20,b=25), plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Màu xanh: đạt kiểm tra. Màu đỏ: có ít nhất một lỗi hoặc ML đánh dấu bất thường.")
    with b:
        st.markdown("**ML bất thường ở run mới nhất**")
        info = latest["metrics"].get("ml", {})
        st.metric("Điểm novelty (0–100)", latest["ml_anomaly_score"])
        st.write("**Kết quả:**", "⚠️ Bất thường" if latest["ml_anomaly"] else "✅ Chưa phát hiện bằng ML")
        st.write("**Model:**", info.get("model", "Chưa có"))
        st.write("**Số mẫu train:**", info.get("trained_samples", 0))
        st.caption(info.get("message", ""))
        if info.get("most_changed_features"):
            datatable(pd.DataFrame(info["most_changed_features"]).rename(columns={"feature":"Đặc trưng", "deviation":"Mức lệch chuẩn hóa"}))
    st.markdown("**Distribution drift: payment_method**")
    df_pay = pd.DataFrame([
        {"Phương thức": name, "Tỷ lệ": latest["metrics"].get("payment_" + name,0), "Nhóm":"Run mới nhất"}
        for name in ["card","bank_transfer","wallet"]
    ] + [
        {"Phương thức": "card", "Tỷ lệ": .5, "Nhóm":"Phân phối tham khảo"},
        {"Phương thức": "bank_transfer", "Tỷ lệ": .3, "Nhóm":"Phân phối tham khảo"},
        {"Phương thức": "wallet", "Tỷ lệ": .2, "Nhóm":"Phân phối tham khảo"},
    ])
    fig = px.bar(df_pay, x="Phương thức", y="Tỷ lệ", color="Nhóm", barmode="group", color_discrete_sequence=["#2462a7", "#a4b7d0"])
    fig.update_layout(height=300, margin=dict(l=10,r=10,t=15,b=15))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Đường tham khảo 50/30/20 là phân phối thiết kế của bộ dữ liệu demo. Bộ phát hiện drift thực tế sử dụng baseline từ lịch sử run đạt, không dùng phân phối cố định trong đồ thị.")

with tab_alert:
    title_note("Quản lý cảnh báo và SLA", "Một run vi phạm sẽ mở một incident, chỉ định owner, mốc SLA và chuỗi sự kiện")
    if incidents:
        df_incs = pd.DataFrame([{
            "ID": f"INC-{i['id']:03d}", "Run": i["run_id"], "Tiêu đề": i["title"],
            "Severity": i["severity"], "Trạng thái": i["status"], "Owner": i["owner"],
            "SLA": i["sla_deadline"], "Vi phạm SLA": "Có" if i["sla_breached"] else "Không",
        } for i in incidents])
        datatable(df_incs)
        st.caption("Mốc SLA demo: P1 = 30 phút, P2 = 2 giờ, P3 = 8 giờ. Incident được lưu và không tự biến mất khi chạy lần mới.")
        incident_id = st.selectbox("Chọn incident", [i["id"] for i in incidents], format_func=lambda x: f"INC-{x:03d} · {next(i['title'] for i in incidents if i['id']==x)}", key="inc_a")
        detail = request_api(f"/api/incidents/{incident_id}")
        l,r = st.columns([1.3,1], gap="large")
        with l:
            st.markdown("**Nhật ký xử lý (incident timeline)**")
            for event in detail["events"]:
                st.markdown(f"**{event['type']}** · `{event['timestamp']}`  \n{event['message']}")
                st.divider()
        with r:
            st.markdown("**Thao tác người phụ trách**")
            st.write("Trạng thái:", detail["status"])
            st.write("Owner:", detail["owner"])
            st.write("SLA deadline:", detail["sla_deadline"])
            st.info("Bấm đóng incident chỉ mô phỏng thao tác nghiệp vụ. Trong hệ thống thật cần tái chạy kiểm tra đạt trước khi đóng.")
            btn_a, btn_b = st.columns(2)
            if btn_a.button("✓ Tiếp nhận", key="ack"):
                request_api(f"/api/incidents/{incident_id}/acknowledge", "POST")
                st.rerun()
            if btn_b.button("✔ Đóng incident", key="resolve"):
                request_api(f"/api/incidents/{incident_id}/resolve", "POST")
                st.rerun()
    else:
        st.success("Chưa có incident nào. Chạy một kịch bản có lỗi để xem.")
    with st.expander("Bảng các cảnh báo được phát ra", expanded=False):
        if alerts:
            datatable(pd.DataFrame(alerts))
        else:
            st.caption("Chưa có cảnh báo.")
    st.caption("Kênh cảnh báo trong phiên bản demo là Dashboard; không gửi email/SMS hoặc Slack thật.")

with tab_ai:
    title_note("AI phân tích nguyên nhân gốc (Root Cause Analysis)", "Kết hợp ML phát hiện bất thường + đồ thị upstream/data lineage + luật suy luận giải thích được; không đọc nhãn kịch bản để chẩn đoán")
    if not incidents:
        st.info("Chưa có incident. Hãy tạo lỗi trước bằng các kịch bản bên trái.")
    else:
        incident_id = st.selectbox("Chọn incident để truy vết", [i["id"] for i in incidents], format_func=lambda x: f"INC-{x:03d} · {next(i['title'] for i in incidents if i['id']==x)}", key="inc_rca")
        item = request_api(f"/api/incidents/{incident_id}")
        rca = item["root_cause"]
        st.markdown("### 🔎 " + rca["conclusion"])
        c1,c2,c3 = st.columns(3)
        c1.metric("Mức ưu tiên giả thuyết", f"{rca['confidence']*100:.0f}/100")
        c2.metric("Độ nghiêm trọng", item["severity"])
        c3.metric("Thành phần nghi vấn", rca["affected_node"])
        st.warning("AI đưa ra giả thuyết có giải thích, không phải kết luận chắc chắn. Người phụ trách cần xác minh bằng log và dữ liệu thực tế.")
        st.markdown("**Giải thích**")
        st.write(rca["explanation"])
        st.markdown("**Bằng chứng đã sử dụng**")
        for fact in rca["evidence"]:
            st.markdown("- " + fact)
        if rca["alternatives"]:
            with st.expander("Các giả thuyết khác"):
                datatable(pd.DataFrame(rca["alternatives"]))
        st.markdown("**Checklist đề xuất khắc phục**")
        for n, step in enumerate(rca["checklist"], start=1):
            st.checkbox(step, key=f"check_{incident_id}_{n}")
        st.caption(rca["confidence_note"])
    st.divider()
    st.markdown("**Kế hoạch triển khai trên hệ thống thật**")
    st.code("1. Thay generate_batch() bằng connector đọc PostgreSQL/API/Kafka.\n2. Chuẩn hóa data contracts và baseline theo từng dataset.\n3. Deploy backend + dashboard + scheduler qua Docker Compose.\n4. Bổ sung RBAC, quản lý secrets, giám sát và kênh gửi cảnh báo thực.\n5. Đánh giá AI RCA trên incident được chuyên gia gán nhãn.", language="text")

st.caption("© Nhóm 6 — Ứng dụng demo môn học. Dữ liệu giao dịch được tạo ngẫu nhiên; không chứa dữ liệu cá nhân thật.")
