"""실행 메타데이터: 원본 해시, 코드 커밋, 정제 규칙·원장·규칙 버전, 임계값, 스냅샷.

같은 run_id 로 여러 번 저장해도 기록은 하나다 (멱등, 도구 write_run_metadata).
내용이 다른데 같은 run_id 로 저장하려 하면 RunMetadataConflict.
원본 해시(raw_files)는 실행이 고정한 스냅샷을 **만들 때 캡처한 값**을 받는다 (DataSource.raw_file_hashes).
지금 파일을 다시 해시하면 이전 snapshot_id 아래 새 파일 해시가 섞이므로, 다시 해시하는 길(raw_files 경로 목록)은
스냅샷 밖 단독 사용에만 쓴다. 실행 중 파일이 바뀌었으면 snapshot_check 에 "불일치 … 재실행 필요"를 남긴다.
run_id 는 파일 이름이 되므로 쓰기·읽기 전에 모양을 검사한다 (`check_run_id`).
"""

import hashlib
import json
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from bigcontest.agent.schemas import AnalysisMode
from bigcontest.config import ROOT
from bigcontest.contracts import candidate_inputs_config, cohorts_config, grain_contract
from bigcontest.ingest import CLEANING_RULE_VERSION

RUNS_DIR = ROOT / "data" / "runs"
LEDGER_FILE = ROOT / "external" / "resources" / "복지자원_원장_v2.csv"  # 도구가 읽는 원장 (datasource DEV_FILES)


class RunMetadataConflict(RuntimeError):
    pass


SNAPSHOT_OK = "일치"


def snapshot_changed_note(changed: list[str]) -> str:
    return (f"불일치: 실행 중 입력 파일이 바뀜 ({', '.join(changed)}). 기록한 해시는 실행 시작 때 값이고 "
            "지금 파일과 다름. 재실행 필요")


# 영문·숫자·_·- 만, 100자까지. 점·슬래시·콜론은 못 씀 (경로 이동, Windows 대체 스트림 차단).
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")


def check_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f"run_id 형식이 맞지 않음: {run_id!r}")
    return run_id


class RunMetadata(BaseModel):
    run_id: str
    created_at: datetime
    snapshot_id: str
    analysis_mode: AnalysisMode
    as_of: date
    code_commit: str | None  # git 이 없으면 None
    code_dirty: bool | None  # 커밋 안 된 변경이 있었나
    raw_files: dict[str, str] = Field(default_factory=dict)  # 파일 이름 → sha256
    cleaning_rule_version: str = CLEANING_RULE_VERSION
    contract_versions: dict[str, str] = Field(default_factory=dict)  # grain·cohorts·inputs 설정 버전
    rule_version: str | None = None
    ledger_version: str | None = None
    thresholds: dict[str, Any] = Field(default_factory=dict)
    # 기록할 때 지금 파일이 스냅샷(raw_files)과 같은지: "일치" / "불일치: … 재실행 필요" / None(검사 안 함)
    snapshot_check: str | None = None

    def identity(self) -> dict:
        """멱등 비교용: 저장 시각만 뺀 내용."""
        return self.model_dump(mode="json", exclude={"created_at"})


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_files(paths: list[Path]) -> dict[str, str]:
    return {Path(p).name: file_sha256(Path(p)) for p in sorted(paths, key=lambda p: Path(p).name)}


def git_commit(root: Path = ROOT) -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        return commit, bool(dirty)
    except (OSError, subprocess.SubprocessError):
        return None, None


def ledger_version(path: Path = LEDGER_FILE) -> str | None:
    """자원 원장 버전 = 원장 파일 해시 앞 12자리."""
    return f"ledger@sha256:{file_sha256(path)[:12]}" if Path(path).exists() else None


def collect(
    run_id: str,
    snapshot_id: str,
    analysis_mode: AnalysisMode,
    as_of: date,
    raw_files: list[Path] | None = None,
    thresholds: dict[str, Any] | None = None,
    rule_version: str | None = None,
    ledger: str | None = None,
    root: Path = ROOT,
    raw_hashes: dict[str, str] | None = None,
    snapshot_check: str | None = None,
) -> RunMetadata:
    """raw_hashes 가 있으면 그대로 기록 (스냅샷을 만들 때 캡처한 해시). 없으면 raw_files 를 지금 해시."""
    commit, dirty = git_commit(root)
    return RunMetadata(
        run_id=run_id,
        created_at=datetime.now(),
        snapshot_id=snapshot_id,
        analysis_mode=analysis_mode,
        as_of=as_of,
        code_commit=commit,
        code_dirty=dirty,
        raw_files=dict(sorted(raw_hashes.items())) if raw_hashes is not None else hash_files(raw_files or []),
        contract_versions={
            "grain": grain_contract()["version"],
            "cohorts": cohorts_config()["version"],
            "candidate_inputs": candidate_inputs_config()["version"],
        },
        rule_version=rule_version,
        ledger_version=ledger if ledger is not None else ledger_version(),
        thresholds=thresholds or {},
        snapshot_check=snapshot_check,
    )


def write_run_metadata(meta: RunMetadata, out_dir: Path = RUNS_DIR) -> tuple[RunMetadata, bool]:
    """run_id 키로 한 번만 기록. (저장된 메타데이터, 새로 썼는지)를 돌려준다."""
    out_dir = Path(out_dir)
    path = out_dir / f"{check_run_id(meta.run_id)}.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(meta.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
    try:
        with open(path, "x", encoding="utf-8") as f:  # 이미 있으면 FileExistsError (동시 실행도 1건)
            f.write(text)
        return meta, True
    except FileExistsError:
        saved = read_run_metadata(meta.run_id, out_dir)
        if saved.identity() != meta.identity():
            raise RunMetadataConflict(f"{meta.run_id}: 이미 다른 내용으로 기록됨") from None
        return saved, False


def read_run_metadata(run_id: str, out_dir: Path = RUNS_DIR) -> RunMetadata:
    path = Path(out_dir) / f"{check_run_id(run_id)}.json"
    return RunMetadata.model_validate_json(path.read_text(encoding="utf-8"))
