import uuid
from typing import Any


def response(data: Any) -> dict[str, Any]:
    return {"data": data, "meta": {"request_id": str(uuid.uuid4())}}
