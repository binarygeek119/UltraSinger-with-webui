"""Load and persist WebUI settings (data/webui_config.json).

YARG / UltraStar export toggles and folder paths are also stored in ``export_folders.json``
in the same data directory (written whenever settings are saved). If that file is newer
than ``webui_config.json``, its export fields override on load so you can edit paths by hand.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

EXPORT_FOLDERS_FILENAME = "export_folders.json"

_EXPORT_FOLDER_KEYS: tuple[str, ...] = (
    "yarg_export_enabled",
    "yarg_export_path",
    "ultrastar_export_enabled",
    "ultrastar_export_path",
)

# faster-whisper / CTranslate2 `compute_type` (see https://opennmt.net/CTranslate2/quantization.html).
# Value "" means omit --whisper_compute_type; UltraSinger then uses float16 on CUDA and int8 on CPU.
WHISPER_COMPUTE_TYPE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("", "Default (UltraSinger: float16 on CUDA, int8 on CPU)"),
    ("default", "default (keep model quantization)"),
    ("auto", "auto (fastest supported on device)"),
    ("int8", "int8"),
    ("int8_float32", "int8_float32"),
    ("int8_float16", "int8_float16"),
    ("int8_bfloat16", "int8_bfloat16"),
    ("int16", "int16"),
    ("float16", "float16"),
    ("float32", "float32"),
    ("bfloat16", "bfloat16"),
)
WHISPER_COMPUTE_TYPE_VALUES: frozenset[str] = frozenset(v for v, _ in WHISPER_COMPUTE_TYPE_OPTIONS)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@dataclass
class WebUIConfig:
    # System
    data_directory: str = ""  # default: <repo>/data
    host: str = "127.0.0.1"
    port: int = 8756
    force_cpu: bool = False
    force_whisper_cpu: bool = False
    whisper_model: str = "large-v2"
    whisper_compute_type: str = ""
    whisper_batch_size: int = 16
    demucs_model: str = "htdemucs"
    user_ffmpeg_path: str = ""
    ytdlp_binary_path: str = ""
    cookiefile: str = ""

    # Default job
    yarg_mode: bool = False
    delete_workfiles_after_complete: bool = True

    # Storage / cleanup
    job_retention_hours: int = 24
    cleanup_on_startup: bool = True
    cleanup_interval_hours: int = 24

    # Debug
    debug_logging: bool = False

    # Downloads (ZIP) / YARG export folder
    zip_exclude_stem_tracks: bool = False
    yarg_export_enabled: bool = False
    yarg_export_path: str = ""
    ultrastar_export_enabled: bool = False
    ultrastar_export_path: str = ""

    # Tray (desktop)
    tray_enabled: bool = False
    start_minimized: bool = False
    open_browser_on_start: bool = True
    minimize_to_tray_on_close: bool = True
    tray_notifications: bool = False

    def effective_data_dir(self) -> Path:
        root = _repo_root()
        if self.data_directory.strip():
            return Path(self.data_directory).expanduser().resolve()
        return (root / "data").resolve()

    def jobs_dir(self) -> Path:
        return self.effective_data_dir() / "jobs"

    def history_dir(self) -> Path:
        return self.effective_data_dir() / "history"

    def history_log_path(self) -> Path:
        return self.history_dir() / "history.log"

    def config_path(self) -> Path:
        return self.effective_data_dir() / "webui_config.json"


def default_config() -> WebUIConfig:
    return WebUIConfig()


def load_config() -> WebUIConfig:
    cfg = default_config()
    path = cfg.config_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        for k, v in raw.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    _normalize_whisper_compute_type(cfg)
    _maybe_merge_export_folders_file(cfg)
    _apply_env_overrides(cfg)
    return cfg


def _normalize_whisper_compute_type(cfg: WebUIConfig) -> None:
    wct = (cfg.whisper_compute_type or "").strip()
    if wct not in WHISPER_COMPUTE_TYPE_VALUES:
        cfg.whisper_compute_type = ""


def _export_folders_path(cfg: WebUIConfig) -> Path:
    return cfg.effective_data_dir() / EXPORT_FOLDERS_FILENAME


def _write_export_folders_file(cfg: WebUIConfig) -> None:
    base = cfg.effective_data_dir()
    base.mkdir(parents=True, exist_ok=True)
    subset = {k: getattr(cfg, k) for k in _EXPORT_FOLDER_KEYS}
    _export_folders_path(cfg).write_text(
        json.dumps(subset, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _apply_export_folders_raw(cfg: WebUIConfig, raw: dict[str, Any]) -> None:
    if "yarg_export_enabled" in raw:
        v = raw["yarg_export_enabled"]
        cfg.yarg_export_enabled = v is True or v == "true" or v == "1"
    if "ultrastar_export_enabled" in raw:
        v = raw["ultrastar_export_enabled"]
        cfg.ultrastar_export_enabled = v is True or v == "true" or v == "1"
    if "yarg_export_path" in raw:
        cfg.yarg_export_path = str(raw["yarg_export_path"] or "").strip()
    if "ultrastar_export_path" in raw:
        cfg.ultrastar_export_path = str(raw["ultrastar_export_path"] or "").strip()


def _maybe_merge_export_folders_file(cfg: WebUIConfig) -> None:
    p_exp = _export_folders_path(cfg)
    p_main = cfg.config_path()
    if not p_exp.is_file():
        return
    try:
        if p_main.is_file() and p_exp.stat().st_mtime <= p_main.stat().st_mtime:
            return
    except OSError:
        return
    try:
        raw = json.loads(p_exp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(raw, dict):
        return
    _apply_export_folders_raw(cfg, raw)


def _apply_env_overrides(cfg: WebUIConfig) -> None:
    """Deployment overrides (e.g. Docker). Env wins over file for these keys."""
    h = os.environ.get("ULTRASINGER_WEBUI_HOST", "").strip()
    if h:
        cfg.host = h
    p = os.environ.get("ULTRASINGER_WEBUI_PORT", "").strip()
    if p:
        try:
            cfg.port = int(p)
        except ValueError:
            pass


def save_config(cfg: WebUIConfig) -> None:
    path = cfg.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    d = asdict(cfg)
    path.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
    _write_export_folders_file(cfg)


def ensure_data_layout(cfg: WebUIConfig) -> None:
    base = cfg.effective_data_dir()
    for sub in ("jobs", "history"):
        (base / sub).mkdir(parents=True, exist_ok=True)


def paths_for_worker(cfg: WebUIConfig) -> dict[str, Path]:
    root = _repo_root()
    return {
        "repo_root": root,
        "src_dir": root / "src",
        "ultrasinger_py": root / "src" / "UltraSinger.py",
    }


def config_to_api_dict(cfg: WebUIConfig) -> dict[str, Any]:
    return asdict(cfg)
