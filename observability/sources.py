"""Persist synthetic source snapshots; telemetry is never treated as stored rows."""
import json

from sqlalchemy import func, select

from .database import PipelineRun, RawCustomer, RawOrder, RawPayment, decode, get_session

SOURCE_MODELS = {"raw_orders": RawOrder, "raw_customers": RawCustomer, "raw_payments": RawPayment}


def load_source_batch(session, run_id, batch):
    """Use the existing batch, without filling missing columns or deduplicating orders.

    payload_json is authoritative. Nullable SQL projections are for inspection only:
    a missing customer_id column remains absent from every original order payload.
    Customers/payments are demo projections, not independent upstream connectors.
    The caller commits snapshots and monitoring results in the same transaction.
    """
    orders = json.loads(batch.frame.to_json(orient="records", date_format="iso", double_precision=15))
    customers = {}
    for position, payload in enumerate(orders):
        common = {"run_id": run_id, "row_number": position}
        session.add(RawOrder(**common, payload_json=json.dumps(payload, ensure_ascii=False, allow_nan=False),
                             **{key: payload.get(key) for key in (
                                 "order_id", "customer_id", "amount", "payment_method", "order_status", "event_time")}))
        payment = {key: payload[key] for key in ("order_id", "amount", "payment_method", "event_time") if key in payload}
        session.add(RawPayment(**common, payload_json=json.dumps(payment, allow_nan=False), **payment))
        customer_field = "customer_id" if "customer_id" in payload else "customer_key"
        customer_id = payload.get(customer_field)
        if customer_id is not None:
            customers[(customer_field, customer_id)] = {
                customer_field: customer_id, "display_name": f"Demo customer {customer_id}",
            }
    for position, payload in enumerate(customers.values()):
        session.add(RawCustomer(run_id=run_id, row_number=position,
                                payload_json=json.dumps(payload, allow_nan=False), **payload))


def get_sources(run_id, source="raw_orders", limit=50, offset=0):
    model = SOURCE_MODELS[source]
    with get_session() as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            return None
        counts = {name: int(session.scalar(select(func.count()).select_from(table).where(table.run_id == run_id)) or 0)
                  for name, table in SOURCE_MODELS.items()}
        rows = session.scalars(select(model).where(model.run_id == run_id)
                               .order_by(model.row_number, model.id).offset(offset).limit(limit)).all()
        return {
            "run_id": run_id, "source": source, "stored_counts": counts,
            "snapshot_available": counts["raw_orders"] > 0,
            "order_columns": decode(run.schema_json, []),
            "provenance": "synthetic batch; customers/payments projected from generated orders",
            "upstream_telemetry_simulated": True,
            "limit": limit, "offset": offset,
            "rows": [decode(row.payload_json, {}) for row in rows],
        }
