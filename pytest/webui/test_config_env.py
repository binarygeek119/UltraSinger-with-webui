"""Tests for Docker / deployment env overrides on WebUIConfig."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from webui.config import _apply_env_overrides, default_config


class TestEnvOverrides(unittest.TestCase):
    def test_host_and_port(self) -> None:
        cfg = default_config()
        with patch.dict(
            os.environ,
            {"ULTRASINGER_WEBUI_HOST": "0.0.0.0", "ULTRASINGER_WEBUI_PORT": "8080"},
            clear=False,
        ):
            _apply_env_overrides(cfg)
        self.assertEqual(cfg.host, "0.0.0.0")
        self.assertEqual(cfg.port, 8080)

    def test_invalid_port_ignored(self) -> None:
        cfg = default_config()
        cfg.port = 8756
        with patch.dict(os.environ, {"ULTRASINGER_WEBUI_PORT": "nope"}, clear=False):
            _apply_env_overrides(cfg)
        self.assertEqual(cfg.port, 8756)

    def test_force_cpu_and_tray_flags(self) -> None:
        cfg = default_config()
        with patch.dict(
            os.environ,
            {"ULTRASINGER_WEBUI_FORCE_CPU": "1", "ULTRASINGER_WEBUI_TRAY": "0"},
            clear=False,
        ):
            _apply_env_overrides(cfg)
        self.assertTrue(cfg.force_cpu)
        self.assertFalse(cfg.tray_enabled)

    def test_export_paths_and_enable_flags(self) -> None:
        cfg = default_config()
        with patch.dict(
            os.environ,
            {
                "ULTRASINGER_YARG_EXPORT_PATH": "/export/yarg",
                "ULTRASINGER_YARG_EXPORT_ENABLED": "true",
                "ULTRASINGER_ULTRASTAR_EXPORT_PATH": "/export/ultrastar",
                "ULTRASINGER_ULTRASTAR_EXPORT_ENABLED": "false",
                "ULTRASINGER_WEBUI_DATA_DIRECTORY": "/data",
            },
            clear=False,
        ):
            _apply_env_overrides(cfg)
        self.assertEqual(cfg.yarg_export_path, "/export/yarg")
        self.assertTrue(cfg.yarg_export_enabled)
        self.assertEqual(cfg.ultrastar_export_path, "/export/ultrastar")
        self.assertFalse(cfg.ultrastar_export_enabled)
        self.assertEqual(cfg.data_directory, "/data")
