from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.orm import Session

from apps.api.app.models import entities as db
from apps.api.app.schemas.job import JobImportPayload
from apps.api.app.services.job_service import import_job
from apps.api.app.services.user_service import DEFAULT_USER_ID


def test_import_unknown_work_mode_as_onsite() -> None:
    session = MagicMock(spec=Session)
    session.get.return_value = db.User(
        id=DEFAULT_USER_ID,
        display_name="默认用户",
    )
    session.scalar.return_value = None

    def assign_job_id() -> None:
        for call in session.add.call_args_list:
            entity = call.args[0]
            if isinstance(entity, db.Job) and entity.id is None:
                entity.id = uuid4()

    session.flush.side_effect = assign_job_id

    result = import_job(
        session,
        JobImportPayload(
            title="Java 开发工程师",
            company_name="示例科技",
            location=None,
            work_mode="UNKNOWN",
            description="负责 Java 后端研发",
            source="MOCK",
        ),
    )

    assert result.job.work_mode == "ONSITE"
    imported_job = next(
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], db.Job)
    )
    assert imported_job.work_mode == "ONSITE"
    assert imported_job.raw_data["work_mode"] == "ONSITE"
