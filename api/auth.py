"""데모용 최소 인증.

- 요청 헤더 `Authorization: Bearer <토큰>`으로 사용자를 정한다. 감사 로그의 actor와 검토자·승인자 이름은 서버가
  토큰으로 정한 이름이다 (요청 본문의 reviewer·approver 값은 믿지 않는다).
- 토큰 설정 (둘 다 없으면 인증 끔 = 개발 모드)
  - `LINKRADAR_API_TOKENS_FILE` (권장): 사람마다 토큰 하나를 적은 YAML. 레포에 올리지 않는다.
        tokens:
          <토큰>: {name: worker-01, role: 검토}
  - `LINKRADAR_API_TOKEN`: 여럿이 같이 쓰는 공용 토큰 하나. 이름은 `LINKRADAR_API_USER`(기본 demo),
    역할은 `LINKRADAR_API_ROLE`(기본 **조회**). 화면 빌드(`VITE_API_TOKEN`)에 넣는 토큰은 화면을 여는 누구나
    개발자 도구로 볼 수 있으므로 이 공용 토큰만 넣는다. 공용 토큰의 기록 이름·actor 는 `공용:<이름>`으로 남겨
    한 사람의 이름처럼 보이지 않게 한다. `LINKRADAR_ENV=prod` 에서는 공용 토큰에 검토·승인 역할을 줄 수 없다.
- 역할: 조회 < 검토 < 승인. 실행 만들기·질의응답·이어 묻기(LLM 비용)와 검토 저장은 검토 이상, 승인은 승인만.
- 개발 모드(토큰 설정 없음)에서는 모두 허용하되, 감사 로그 actor를 `dev-unauthenticated`로 남긴다.
  `LINKRADAR_ENV=prod`인데 토큰 설정이 없으면 앱을 만들지 않는다.
- 운영(파일럿)에서는 앞단 프록시(nginx 등)에서 기관 로그인(SSO·LDAP)을 거치게 하고, 화면 번들에는 토큰을 넣지 않는다.
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
SHARED_PREFIX = "공용:"
SHARED_DEFAULT_ROLE = "조회"


@dataclass(frozen=True)
class Identity:
    name: str
    role: str
    authenticated: bool
    shared: bool = False  # 여럿이 같이 쓰는 공용 토큰 (LINKRADAR_API_TOKEN·VITE_API_TOKEN)

    def actor(self) -> str:
        if not self.authenticated:
            return DEV_ACTOR
        return SHARED_PREFIX + self.name if self.shared else self.name

    def record_name(self) -> str:
        """검토자·승인자·기록자 칸에 적을 이름. 공용 토큰이면 `공용:` 표시를 붙인다."""
        return self.actor()


DEV_IDENTITY = Identity(name=DEV_ACTOR, role="승인", authenticated=False)


def _identity(name: str, role: str, shared: bool = False) -> Identity:
    if role not in ROLES:
        raise ValueError(f"역할은 {', '.join(ROLES)} 중 하나: {role!r}")
    if not name or len(name) > 50:
        raise ValueError("이름은 1~50자")
    return Identity(name=name, role=role, authenticated=True, shared=shared)


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
                                       os.environ.get("LINKRADAR_API_ROLE", SHARED_DEFAULT_ROLE), shared=True)
        path = os.environ.get("LINKRADAR_API_TOKENS_FILE", "").strip()
        if path:
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            for tok, who in (data.get("tokens") or {}).items():
                tokens[str(tok)] = _identity(str(who["name"]), str(who["role"]))
        return cls(tokens)

    def check_prod(self) -> None:
        """prod 시작 검사: 공용 토큰은 조회만 (승인 기록이 공용 사용자 이름으로 남지 않게)."""
        bad = [who.name for who in self.tokens.values() if who.shared and who.role != SHARED_DEFAULT_ROLE]
        if bad:
            raise RuntimeError("LINKRADAR_ENV=prod 에서 공용 토큰(LINKRADAR_API_TOKEN)은 조회 역할만 됩니다. "
                               "검토·승인은 LINKRADAR_API_TOKENS_FILE 에 사람마다 토큰을 적으세요")

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
