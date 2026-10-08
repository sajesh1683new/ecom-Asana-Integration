from typing import Any
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app import models as m
from backend.app.db import add, record, all_rows, now


class MockAsanaClient:
    def __init__(self, s: Session):
        self.s = s

    def gid(self) -> str:
        import uuid

        return str(uuid.uuid4().int)[:18]

    def list_projects(self) -> list[dict[str, Any]]:
        return all_rows(self.s, m.Project)

    def list_tasks(self, project_gid: str) -> list[dict[str, Any]]:
        return all_rows(self.s, m.Task, project_gid=project_gid)

    def get_task(self, gid: str) -> dict[str, Any]:
        task = self.s.scalar(select(m.Task).filter_by(gid=gid))
        if not task:
            raise ValueError("Asana task not found")
        return record(task)

    def create_task(self, **values: Any) -> dict[str, Any]:
        return record(add(self.s, m.Task, gid=self.gid(), **values))

    def update_task(self, gid: str, **values: Any) -> dict[str, Any]:
        task = self.s.scalar(select(m.Task).filter_by(gid=gid))
        if not task:
            raise ValueError("Asana task not found")
        for key, value in values.items():
            setattr(task, key, value)
        task.modified_at = now()
        self.s.flush()
        return record(task)

    def add_comment(self, gid: str, text: str) -> dict[str, Any]:
        existing = self.s.scalar(select(m.Story).filter_by(task_gid=gid, text=text))
        return record(
            existing
            or add(
                self.s, m.Story, gid=self.gid(), task_gid=gid, text=text, author="Visual QA Bridge"
            )
        )

    def list_comments(self, gid: str) -> list[dict[str, Any]]:
        return all_rows(self.s, m.Story, task_gid=gid)

    def list_subtasks(self, gid: str) -> list[dict[str, Any]]:
        return all_rows(self.s, m.Task, parent_gid=gid)

    def add_attachment(self, gid: str, file_path: str) -> dict[str, Any]:
        old = self.s.scalar(select(m.Attachment).filter_by(task_gid=gid, file_path=file_path))
        return record(
            old
            or add(
                self.s,
                m.Attachment,
                gid=self.gid(),
                task_gid=gid,
                name=Path(file_path).name,
                file_path=file_path,
            )
        )

    def create_subtask(self, parent_gid: str, **values: Any) -> dict[str, Any]:
        parent = self.get_task(parent_gid)
        return self.create_task(
            parent_gid=parent_gid, project_gid=parent["project_gid"], section_gid="1004", **values
        )

    def set_custom_field(self, gid: str, field_gid: str, value: Any) -> dict[str, Any]:
        task = self.get_task(gid)
        field = self.s.scalar(select(m.CustomField).filter_by(gid=field_gid))
        fields = {**task["custom_fields"], field.name if field else field_gid: value}
        row = self.s.scalar(select(m.FieldValue).filter_by(task_gid=gid, field_gid=field_gid))
        if row:
            row.value = value
        else:
            add(self.s, m.FieldValue, task_gid=gid, field_gid=field_gid, value=value)
        return self.update_task(gid, custom_fields=fields)
