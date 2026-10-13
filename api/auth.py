"""데모용 최소 인증.

- 요청 헤더 `Authorization: Bearer <토큰>`으로 사용자를 정한다. 감사 로그의 actor는 서버가 정한 이름이다
  (요청 본문의 reviewer·approver 값은 믿지 않는다).
- 토큰 설정 (둘 다 없으면 인증 끔 = 개발 모드)
  - `LINKRADAR_API_TOKEN`: 토큰 하나. 이름은 `LINKRADAR_API_USER`(기본 demo), 역할은 `LINKRADAR_API_ROLE`(기본 승인)
  - `LINKRADAR_API_TOKENS_FILE`: 여러 토큰을 적은 YAML. 레포에 올리지 않는다.
        tokens:
          <토큰>: {name: worker-01, role: 검토}
- 역할: 조회 < 검토 < 승인. 검토 저장은 검토 이상, 승인은 승인만.
- 개발 모드(토큰 설정 없음)에서는 모두 허용하되, 감사 로그 actor를 `dev-unauthenticated`로 남긴다.
  `LINKRADAR_ENV=prod`인데 토큰 설정이 없으면 앱을 만들지 않는다.
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from fastapi import Request

from api.errors import ApiError

ROLES = {"조회": 0, "검토": 1, "승인": 2}
DEV_ACTOR = "dev-unauthenticated"


@dataclass(frozen=True)
class Identity:
    name: str
    role: str
    authenticated: bool

    def actor(self) -> str:
        return self.name if self.authenticated else DEV_ACTOR


DEV_IDENTITY = Identity(name=DEV_ACTOR, role="승인", authenticated=False)


def _identity(name: str, role: str) -> Identity:
    if role not in ROLES:
        raise ValueError(f"역할은 {', '.join(ROLES)} 중 하나: {role!r}")
    if not name or len(name) > 50:
        raise ValueError("이름은 1~50자")
    return Identity(name=name, role=role, authenticated=True)


class Auth:
    def __init__(self, tokens: dict[str, Identity] | None = None):
        self.tokens = dict(tokens or {})

    @property
    def enabled(self) -> bool:
        return bool(self.tokens)

    @classmethod
    def from_env(cls) -> Auth:
        tokens: dict[str, Identity] = {}
        single = os.environ.get("LINKRADAR_API_TOKEN", "").strip()
        if single:
            tokens[single] = _identity(os.environ.get("LINKRADAR_API_USER", "demo"),
                                       os.environ.get("LINKRADAR_API_ROLE", "승인"))
        path = os.environ.get("LINKRADAR_API_TOKENS_FILE", "").strip()
        if path:
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            for tok, who in (data.get("tokens") or {}).items():
                tokens[str(tok)] = _identity(str(who["name"]), str(who["role"]))
        return cls(tokens)

    def identify(self, header: str | None) -> Identity:
        if not self.enabled:
            return DEV_IDENTITY
        if not header or not header.startswith("Bearer "):
            raise ApiError(401, "UNAUTHENTICATED", "로그인 토큰이 필요합니다.")
        given = header[len("Bearer "):].strip().encode()
        for tok, who in self.tokens.items():
            if hmac.compare_digest(given, tok.encode()):
                return who
        raise ApiError(401, "UNAUTHENTICATED", "토큰이 맞지 않습니다.")


def require(role: str):
    """경로 의존성: 이 역할 이상이어야 함. 사용자 정보를 돌려준다."""
    need = ROLES[role]

    def dep(request: Request) -> Identity:
        who = request.app.state.auth.identify(request.headers.get("Authorization"))
        if ROLES[who.role] < need:
            raise ApiError(403, "FORBIDDEN", f"{role} 권한이 필요합니다.")
        request.state.actor = who.actor()
        return who

    dep.__name__ = f"require_{need}"
    return dep
