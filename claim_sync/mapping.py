from datetime import date, datetime

PRODUCT_FIELDS = ("app", "device", "ctrl", "denstiy", "nand_gen", "nand_ver", "dram_gen", "dram_ver")
STRING_MAPPING = {
    "far_no": "farNo",
    "sample_no": "sampleNo",
    "cust_name": "custName",
    "fail_loc": "failLoc",
    "fail_symptom": "failSymptom",
    "part_id": "partId",
    "failmode1": "failMode1",
    "failmode2": "failMode2",
    "comp_wc": "shippingWeekCode",
    "ims_key": "imsKey",
    "lot_id": "lotId",
}
DATE_MAPPING = {
    "rcv_date": "rcvDate",
    "due_date": "dueDate",
    "far_comp_date": "actualCompDate",
    "ims_created_date": "imsKeyCreatedDate",
}


def scalar(value, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{field}: 문자열 또는 숫자가 필요합니다.")
    return str(value)


def normalized_date(value, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field}: ISO 날짜 문자열이 필요합니다.")
    try:
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        # Preserve the source calendar day; do not shift a claim date between timezones.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError as exc:
        raise ValueError(f"{field}: 유효한 ISO 날짜가 아닙니다.") from exc


def part_prefix(claim: dict) -> str:
    value = scalar(claim.get("partId"), "partId")
    if not value or len(value) < 15:
        raise ValueError("partId: 제품 조회를 위한 최소 15자 문자열이 필요합니다.")
    return value[:15]


def map_record(claim: dict, product: dict) -> dict:
    missing = set(PRODUCT_FIELDS) - product.keys()
    if missing:
        raise ValueError(f"제품 응답의 필수 필드 누락: {', '.join(sorted(missing))}")
    values = {dest: scalar(claim.get(src), src) for dest, src in STRING_MAPPING.items()}
    values.update({dest: normalized_date(claim.get(src), src) for dest, src in DATE_MAPPING.items()})
    for field in ("far_no", "sample_no", "rcv_date", "part_id"):
        if not values[field] or not str(values[field]).strip():
            raise ValueError(f"{field}: 필수 값이 비어 있습니다.")
    for field in ("app", "device", "ctrl"):
        values[field] = scalar(product[field], field)
    # The upstream product field is spelled denstiy; FAR still expects density.
    values["density"] = scalar(product["denstiy"], "denstiy")
    for field in ("nand", "dram"):
        parts = [scalar(product[f"{field}_{suffix}"], f"{field}_{suffix}") for suffix in ("gen", "ver")]
        values[field] = " ".join(p.strip() for p in parts if p and p.strip()) or None
    return values
