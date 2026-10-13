"""실행: `uv run uvicorn api.main:app --reload` (레포 맨 위 폴더에서). 설정은 api/app.py 참고."""

from api.app import create_app

app = create_app()
