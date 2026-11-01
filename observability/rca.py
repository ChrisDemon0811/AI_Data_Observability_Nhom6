"""Evidence-based explainable RCA, without reading injected scenario labels."""

CHECK_REASON = {
    "freshness": "Dòng sự kiện mới chưa về đúng thời hạn; kiểm tra lịch nạp và độ trễ nguồn.",
    "completeness": "Các trường bắt buộc có nhiều NULL; kiểm tra phép JOIN hoặc trích xuất dữ liệu.",
    "uniqueness": "order_id bị lặp; có thể do nạp lại batch không idempotent.",
    "schema": "Schema bị thay đổi so với data contract đã công bố.",
    "validity": "Trường payment_method có giá trị thiếu hoặc ngoài tập giá trị hợp lệ.",
    "distribution_drift": "Phân phối phương thức thanh toán khác đáng kể so với lịch sử khỏe mạnh.",
    "amount_drift": "Giá trị trung bình amount lệch mạnh, có thể do đổi đơn vị hoặc thay đổi nghiệp vụ.",
    "volume": "Khối lượng bản ghi lệch khỏi dải tham chiếu; kiểm tra lọc, nhân bản hoặc nguồn đổ về.",
}


def analyze_root_cause(checks, upstream, metrics, ml_info):
    """Candidate hypotheses based on failed checks + independently captured upstream signals.

    Deliberately does not use scenario labels; signals are evidence not a diagnosis.
    """
    failures = {x.code: x for x in checks if not x.passed}
    hypotheses = []
    evidence = []
    for code, check in failures.items():
        evidence.append(f"{check.title}: quan sát={check.observed}, yêu cầu {check.expected}")

    bad_sources = [(name, data) for name, data in upstream.items() if data.get("status") != "OK"]
    for name, data in bad_sources:
        evidence.append(f"{name}: trạng thái={data['status']}, errors={data['error_count']}, rows={data['rows']}")

    if any(name == "raw_payments" for name, _ in bad_sources) and ("completeness" in failures or "validity" in failures):
        hypotheses.append((0.96, "Nguồn thanh toán raw_payments gián đoạn", "Nguồn thanh toán lỗi đồng thời các trường amount/payment_method thiếu hoặc sai.", "raw_payments"))
    if "schema" in failures:
        hypotheses.append((0.93, "Data contract/schema thay đổi không tương thích", "Danh sách cột thực tế khác kỳ vọng; cần khớp schema với upstream.", "raw_customers → stg_orders"))
    if "freshness" in failures:
        hypotheses.append((0.90, "Nguồn dữ liệu hoặc lịch ETL bị trễ", "Freshness vượt ngưỡng; kiểm tra nguồn, queue và bộ lập lịch.", "raw_orders → stg_orders"))
    if "uniqueness" in failures:
        hypotheses.append((0.91, "Nạp lại dữ liệu gây trùng order_id", "Có các khóa xuất hiện nhiều lần; kiểm tra merge/upsert.", "stg_orders"))
    if "completeness" in failures:
        hypotheses.append((0.81, "Bản ghi nguồn thiếu trường bắt buộc", "Tỷ lệ NULL trong cột quan trọng vượt ngưỡng.", "raw_orders / raw_payments"))
    if "distribution_drift" in failures or "amount_drift" in failures:
        hypotheses.append((0.81, "Thay đổi phân phối hoặc quy tắc nghiệp vụ", "Hành vi thanh toán/giá trị đơn vượt baseline được đo.", "raw_orders → fact_orders"))
    if "volume" in failures:
        hypotheses.append((0.80, "Thay đổi số lượng bản ghi đầu vào", "Volume khác đáng kể so với lịch sử; xem filter, load, duplicate.", "raw_orders"))
    if "validity" in failures:
        hypotheses.append((0.74, "Giá trị thanh toán không hợp lệ", "Có NULL hoặc hạng mục không thuộc hợp đồng dữ liệu.", "raw_payments"))
    if not hypotheses:
        hypotheses.append((0.61, "Bất thường thống kê chưa rõ nguyên nhân", "IsolationForest thấy mô hình dữ liệu khác baseline; cần xác minh thủ công.", "fact_orders"))

    hypotheses.sort(key=lambda x: x[0], reverse=True)
    top = hypotheses[0]
    checklist = []
    if "schema" in failures:
        checklist.extend(["So sánh schema hiện tại với data contract.", "Sửa phép ánh xạ tên cột và bổ sung kiểm thử schema trước deploy."])
    if "freshness" in failures:
        checklist.extend(["Kiểm tra lần chạy gần nhất và hàng đợi của scheduler.", "Kiểm tra tốc độ lấy dữ liệu từ raw_orders; chạy lại batch bị trễ."])
    if "completeness" in failures or "validity" in failures:
        checklist.extend(["Đối soát bản ghi source với staging theo order_id.", "Sửa phép JOIN/ánh xạ null; quarantine bản ghi không đạt."])
    if "uniqueness" in failures:
        checklist.extend(["Kiểm tra khóa tự nhiên và batch_id trước khi nạp lại.", "Dùng MERGE/UPSERT và ràng buộc unique trong đích."])
    if "distribution_drift" in failures or "amount_drift" in failures:
        checklist.extend(["Đối chiếu thay đổi chính sách giá hoặc phương thức thanh toán.", "Lấy mẫu bản ghi bất thường và xác thực với chủ dữ liệu."])
    if "volume" in failures:
        checklist.append("So sánh row count theo từng bước nguồn → staging → mart.")
    if not checklist:
        checklist = ["Kiểm tra các đặc trưng nổi bật trong báo cáo ML.", "Xác nhận với data owner trước khi thay đổi pipeline."]
    checklist += ["Ghi nhận giải pháp và người chịu trách nhiệm vào incident timeline.", "Chạy lại pipeline, chỉ đóng incident sau khi các kiểm tra đạt."]
    checklist = list(dict.fromkeys(checklist))
    return {
        "approach": "Hybrid AI: IsolationForest + data lineage + luật suy luận có thể giải thích",
        "conclusion": top[1],
        "confidence": top[0],
        "confidence_note": "Điểm ưu tiên heuristic, KHÔNG phải xác suất được hiệu chuẩn hoặc kết luận chắc chắn.",
        "affected_node": top[3],
        "explanation": top[2],
        "evidence": evidence + [f"ML: {ml_info.get('message', '')}"],
        "alternatives": [{"hypothesis": name, "priority": score, "node": node} for score, name, _, node in hypotheses[1:4]],
        "checklist": checklist,
        "human_review_required": True,
    }
