import json
from datetime import date, timedelta

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .mapping import PRODUCT_FIELDS
from .store import Store, encode, utcnow


def create_mock_app(settings: Settings, store: Store) -> FastAPI:
    app = FastAPI(title="Local mock upstream APIs")

    @app.get(settings.claims_path)
    def claims(request: Request):
        start = date.fromisoformat(request.query_params[settings.claims_from_param])
        end = date.fromisoformat(request.query_params[settings.claims_to_param])
        limit = int(request.query_params.get("limit", "1000"))
        total = ((end - start).days + 1) * settings.mock_claims_per_day
        rows = []
        day = start
        while day <= end and len(rows) < limit:
            for index in range(settings.mock_claims_per_day):
                if len(rows) >= limit:
                    break
                rows.append(
                    {
                        "farNo": f"FAR-{day:%Y%m%d}-{index:04}",
                        "sampleNo": f"S{index + 1:03}",
                        "analName": "Mock Analyst",
                        "rcvDate": day.isoformat(),
                        "dueDate": (day + timedelta(days=14)).isoformat(),
                        "firstCompDate": None,
                        "actualCompDate": None if index % 3 == 0 else (day + timedelta(days=3)).isoformat(),
                        "imsKey": f"IMS-{day:%Y%m%d}-{index:04}",
                        "imsKeyCreatedDate": f"{day}T09:30:00+09:00",
                        "custName": ["Demo Electronics", "Sample Systems", "Test Mobility"][index % 3],
                        "failLoc": ["Korea", "Vietnam", "Taiwan"][index % 3],
                        "failSymptom": ["Read failure", "Power issue", "Performance drop"][index % 3],
                        "partId": f"DEMO-PART-{index % 4:05}-EXT",
                        "failMajorCategory": "Electrical",
                        "failMinorCategory": "Functional",
                        "shippingWeekCode": day.strftime("%Y%W"),
                        "lotId": f"LOT-{day:%Y%m}-{index % 3}",
                        "failMode1": "Read",
                        "failMode2": "Intermittent",
                    }
                )
            day += timedelta(days=1)
        if rows and settings.mock_scenario == "malformed_claim":
            rows[0]["rcvDate"] = "invalid-date"
        return {"data": rows, "total": total}

    @app.get(settings.product_schema_path)
    def schema():
        return {
            "type": "object",
            "properties": {field: {"type": ["string", "null"]} for field in PRODUCT_FIELDS},
        }

    @app.get(settings.product_record_path)
    def product(part_id: str):
        if settings.mock_scenario == "product_missing" and part_id.endswith("00000"):
            raise HTTPException(404, "Mock product missing")
        if len(part_id) != 15:
            raise HTTPException(400, "Exactly 15 characters required")
        return {
            "data": {
                "app": "Client SSD",
                "device": "NVMe",
                "ctrl": "Demo Controller",
                "denstiy": "1 TB",
                "nand_gen": "V8",
                "nand_ver": "1.0",
                "dram_gen": "LPDDR4",
                "dram_ver": "2.0",
            }
        }

    @app.post(settings.target_path)
    async def target(request: Request):
        body = await request.json()
        values = body.get("values", {})
        key = encode([values.get(field) for field in ("far_no", "sample_no")])
        if settings.mock_scenario == "target_error":
            raise HTTPException(422, "Mock validation rejection")
        if not values.get("far_no") or not values.get("sample_no"):
            raise HTTPException(422, "far_no and sample_no are required")
        with store.connect() as db:
            inserted = db.execute(
                "INSERT INTO mock_target VALUES(?,?,?) ON CONFLICT(record_key) DO NOTHING",
                (key, encode(values), utcnow()),
            ).rowcount
        if not inserted:
            # Match the supplied server contract, including its spelling mistakes.
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": {
                        "code": "CREATE_FAIELD, UNIQUE",
                        "massage": "UNIQUE constraint failed: far_tabl.far_no, far_table.sample_no",
                    },
                },
            )
        if settings.mock_scenario == "target_timeout":
            # Commit then lose the response to exercise ambiguous-delivery recovery.
            raise httpx.ReadTimeout("Mock response lost after commit")
        return {"ok": True, "success": True, "operation": "insert", "key": key}

    @app.patch(settings.target_path)
    async def patch_target(request: Request):
        body = await request.json()
        where, values = body.get("where"), body.get("values")
        if not isinstance(where, dict) or set(where) != {"far_no", "sample_no"} or not all(where.values()):
            raise HTTPException(422, "Both far_no and sample_no conditions are required")
        if not isinstance(values, dict) or not values or {"far_no", "sample_no"} & values.keys():
            raise HTTPException(422, "Non-key update values are required")
        key = encode([where["far_no"], where["sample_no"]])
        with store.connect() as db:
            row = db.execute("SELECT payload FROM mock_target WHERE record_key=?", (key,)).fetchone()
            if not row:
                raise HTTPException(404, "No record matches both conditions")
            updated = {**json.loads(row["payload"]), **values}
            db.execute(
                "UPDATE mock_target SET payload=?,updated_at=? WHERE record_key=?",
                (encode(updated), utcnow(), key),
            )
        if settings.mock_scenario == "target_timeout":
            raise httpx.ReadTimeout("Mock PATCH response lost after commit")
        return {"ok": True, "success": True, "operation": "update", "key": key, "updated": 1}

    return app
