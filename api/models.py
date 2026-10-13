"""API 명세에 아직 없는 조회 응답 모델.

명세의 모델은 모두 `bigcontest.agent.schemas`에 있다. 여기 모델은 나중에 명세·스키마로 옮길 후보다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bigcontest.agent.schemas import AnalysisMode, Report, RunPhase


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunSummary(_Model):
    run_id: str
    mode: Literal["monitoring", "qa"]
    status: RunPhase
    route: str | None = None
    question: str | None = None
    candidate_id: str | None = None
    city: str | None = None
    as_of: date
    analysis_mode: AnalysisMode
    snapshot_id: str
    cleaning_rule_version: str | None = None
    rule_version: str | None = None
    ledger_version: str | None = None
    evidence_revision: int
    report_revision: int
    report_stale: bool
    extra_rounds: int
    created_at: datetime
    updated_at: datetime


class RunListResponse(_Model):
    items: list[RunSummary]


class ReportRevisionInfo(_Model):
    report_revision: int
    evidence_revision: int
    snapshot_id: str
    reason: str
    created_at: datetime


class ResourceJudgementRecord(_Model):
    resource_id: str
    judgement: Literal["적합", "보류", "부적합"]
    memo: str | None = None


class ReviewRecord(_Model):
    review_id: str
    reviewer: str
    checked_evidence_ids: list[str] = Field(default_factory=list)
    resource_judgements: list[ResourceJudgementRecord] = Field(default_factory=list)
    memo: str | None = None
    follow_up_on: date | None = None
    report_revision_before: int
    report_revision_after: int
    created_at: datetime


class ApprovalRecord(_Model):
    approval_id: str
    idempotency_key: str
    decision: Literal["approved", "held", "more"]
    report_revision: int
    approver: str
    memo: str | None = None
    created_at: datetime


class RunHistoryResponse(_Model):
    run_id: str
    report_revisions: list[ReportRevisionInfo]
    reviews: list[ReviewRecord]
    approvals: list[ApprovalRecord]
    finalized: list[str] = Field(default_factory=list)  # 기록된 부작용 단계 (예: finalize)


class ReportResponse(_Model):
    run_id: str
    snapshot_id: str
    report_stale: bool
    report: Report
