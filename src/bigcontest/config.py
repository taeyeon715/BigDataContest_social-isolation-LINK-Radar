import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "configs" / "project.yaml"


def load_config() -> dict:
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    raw = Path(os.environ.get("BC_RAW_DIR", cfg["raw_dir"]))
    cfg["raw_dir"] = raw if raw.is_absolute() else ROOT / raw
    cfg["interim_dir"] = ROOT / cfg["interim_dir"]
    return cfg
