# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""日志轮转配置：RotatingFileHandler 应按大小切割并自动清理旧备份。"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch

from plugins_market.core.interface_log import (
    InterfaceLogParams,
    get_interface_logger,
    log_interface,
    setup_interface_logger,
)
from plugins_market.core.logging import (
    _DEFAULT_LOG_BACKUP_COUNT,
    _DEFAULT_LOG_MAX_BYTES,
    _GLOBAL_LOG_FILTER,
    PlainLogFormatter,
    _build_handler,
    _get_log_backup_count,
    _get_log_max_bytes,
)


def _close_handlers(*handlers: logging.Handler) -> None:
    for h in handlers:
        with contextlib.suppress(Exception):
            h.close()


class LogMaxBytesConfigTests(unittest.TestCase):
    """_get_log_max_bytes 从 LOG_MAX_BYTES 读取，非法值回退默认。"""

    def test_default_when_env_unset(self) -> None:
        os.environ.pop("LOG_MAX_BYTES", None)
        self.assertEqual(_get_log_max_bytes(), _DEFAULT_LOG_MAX_BYTES)

    def test_custom_value(self) -> None:
        with patch.dict(os.environ, {"LOG_MAX_BYTES": "1048576"}):
            self.assertEqual(_get_log_max_bytes(), 1048576)

    def test_invalid_string_falls_back(self) -> None:
        with patch.dict(os.environ, {"LOG_MAX_BYTES": "not-a-number"}):
            self.assertEqual(_get_log_max_bytes(), _DEFAULT_LOG_MAX_BYTES)

    def test_zero_falls_back(self) -> None:
        with patch.dict(os.environ, {"LOG_MAX_BYTES": "0"}):
            self.assertEqual(_get_log_max_bytes(), _DEFAULT_LOG_MAX_BYTES)

    def test_negative_falls_back(self) -> None:
        with patch.dict(os.environ, {"LOG_MAX_BYTES": "-100"}):
            self.assertEqual(_get_log_max_bytes(), _DEFAULT_LOG_MAX_BYTES)


class LogBackupCountConfigTests(unittest.TestCase):
    """_get_log_backup_count 从 LOG_BACKUP_COUNT 读取，非法值回退默认。"""

    def test_default_when_env_unset(self) -> None:
        os.environ.pop("LOG_BACKUP_COUNT", None)
        self.assertEqual(_get_log_backup_count(), _DEFAULT_LOG_BACKUP_COUNT)

    def test_custom_value(self) -> None:
        with patch.dict(os.environ, {"LOG_BACKUP_COUNT": "10"}):
            self.assertEqual(_get_log_backup_count(), 10)

    def test_invalid_string_falls_back(self) -> None:
        with patch.dict(os.environ, {"LOG_BACKUP_COUNT": "abc"}):
            self.assertEqual(_get_log_backup_count(), _DEFAULT_LOG_BACKUP_COUNT)

    def test_negative_falls_back(self) -> None:
        with patch.dict(os.environ, {"LOG_BACKUP_COUNT": "-1"}):
            self.assertEqual(_get_log_backup_count(), _DEFAULT_LOG_BACKUP_COUNT)

    def test_zero_falls_back(self) -> None:
        with patch.dict(os.environ, {"LOG_BACKUP_COUNT": "0"}):
            self.assertEqual(_get_log_backup_count(), _DEFAULT_LOG_BACKUP_COUNT)


class BuildHandlerTests(unittest.TestCase):
    """_build_handler 返回 RotatingFileHandler 且配置正确。"""

    def test_returns_rotating_file_handler(self) -> None:
        td = tempfile.mkdtemp()
        try:
            handler = _build_handler(str(Path(td) / "test.log"), logging.INFO)
            try:
                self.assertIsInstance(handler, RotatingFileHandler)
            finally:
                handler.close()
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_max_bytes_matches_config(self) -> None:
        with patch.dict(os.environ, {"LOG_MAX_BYTES": "2048"}):
            td = tempfile.mkdtemp()
            try:
                handler = _build_handler(str(Path(td) / "test.log"), logging.INFO)
                try:
                    self.assertEqual(handler.maxBytes, 2048)
                finally:
                    handler.close()
            finally:
                shutil.rmtree(td, ignore_errors=True)

    def test_backup_count_matches_config(self) -> None:
        with patch.dict(os.environ, {"LOG_BACKUP_COUNT": "3"}):
            td = tempfile.mkdtemp()
            try:
                handler = _build_handler(str(Path(td) / "test.log"), logging.INFO)
                try:
                    self.assertEqual(handler.backupCount, 3)
                finally:
                    handler.close()
            finally:
                shutil.rmtree(td, ignore_errors=True)

    def test_formatter_is_plain_log_formatter(self) -> None:
        td = tempfile.mkdtemp()
        try:
            handler = _build_handler(str(Path(td) / "test.log"), logging.INFO)
            try:
                self.assertIsInstance(handler.formatter, PlainLogFormatter)
            finally:
                handler.close()
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_level_matches(self) -> None:
        td = tempfile.mkdtemp()
        try:
            handler = _build_handler(str(Path(td) / "test.log"), logging.WARNING)
            try:
                self.assertEqual(handler.level, logging.WARNING)
            finally:
                handler.close()
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_has_global_log_filter(self) -> None:
        td = tempfile.mkdtemp()
        try:
            handler = _build_handler(str(Path(td) / "test.log"), logging.INFO)
            try:
                self.assertIn(_GLOBAL_LOG_FILTER, handler.filters)
            finally:
                handler.close()
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_writes_to_specified_path(self) -> None:
        td = tempfile.mkdtemp()
        log_path = str(Path(td) / "output.log")
        try:
            handler = _build_handler(log_path, logging.INFO)
            logger = logging.getLogger("test_build_handler_write")
            logger.handlers.clear()
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
            try:
                logger.info("hello world")
                handler.flush()
                content = Path(log_path).read_text(encoding="utf-8")
                self.assertIn("hello world", content)
            finally:
                logger.removeHandler(handler)
                handler.close()
        finally:
            shutil.rmtree(td, ignore_errors=True)


class LogRotationEndToEndTests(unittest.TestCase):
    """端到端：超过 maxBytes 后产生备份且不超过 backupCount。"""

    @staticmethod
    def _make_logger(name: str, handler: logging.Handler, level: int = logging.INFO):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
        return logger

    def test_rotation_creates_backup_files(self) -> None:
        td = tempfile.mkdtemp()
        log_path = str(Path(td) / "rotate.log")
        try:
            with patch.dict(os.environ, {"LOG_MAX_BYTES": "200", "LOG_BACKUP_COUNT": "3"}):
                handler = _build_handler(log_path, logging.INFO)
                logger = self._make_logger("test_rotation_backup", handler)
                try:
                    for i in range(50):
                        logger.info("rotation test line %d", i)
                    handler.flush()
                    files = sorted(
                        f.name for f in Path(td).iterdir()
                        if f.name.startswith("rotate.log")
                    )
                    self.assertGreaterEqual(len(files), 2)
                    self.assertLessEqual(len(files), 4)
                    backups = [f for f in files if f != "rotate.log"]
                    self.assertGreater(len(backups), 0)
                finally:
                    logger.removeHandler(handler)
                    handler.close()
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_backup_count_limits_old_files(self) -> None:
        td = tempfile.mkdtemp()
        log_path = str(Path(td) / "limit.log")
        try:
            with patch.dict(os.environ, {"LOG_MAX_BYTES": "100", "LOG_BACKUP_COUNT": "2"}):
                handler = _build_handler(log_path, logging.INFO)
                logger = self._make_logger("test_backup_limit", handler)
                try:
                    for i in range(100):
                        logger.info("limit test line number %d", i)
                    handler.flush()
                    files = [
                        f.name for f in Path(td).iterdir()
                        if f.name.startswith("limit.log")
                    ]
                    self.assertLessEqual(len(files), 3)
                finally:
                    logger.removeHandler(handler)
                    handler.close()
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_default_config_no_spurious_rotation(self) -> None:
        """默认阈值下少量日志不应触发轮转。"""
        td = tempfile.mkdtemp()
        log_path = str(Path(td) / "default.log")
        try:
            os.environ.pop("LOG_MAX_BYTES", None)
            os.environ.pop("LOG_BACKUP_COUNT", None)
            handler = _build_handler(log_path, logging.INFO)
            logger = self._make_logger("test_default_no_rotate", handler)
            try:
                for i in range(20):
                    logger.info("default config line %d", i)
                handler.flush()
                files = [
                    f.name for f in Path(td).iterdir()
                    if f.name.startswith("default.log")
                ]
                self.assertEqual(files, ["default.log"])
            finally:
                logger.removeHandler(handler)
                handler.close()
        finally:
            shutil.rmtree(td, ignore_errors=True)


class InterfaceLoggerRotationTests(unittest.TestCase):
    """setup_interface_logger 使用 RotatingFileHandler 并正确配置。"""

    def setUp(self) -> None:
        self._logger = get_interface_logger()
        self._saved_handlers = list(self._logger.handlers)
        self._saved_propagate = self._logger.propagate
        self._saved_level = self._logger.level

    def tearDown(self) -> None:
        _close_handlers(*self._logger.handlers)
        self._logger.handlers.clear()
        self._logger.handlers.extend(self._saved_handlers)
        self._logger.propagate = self._saved_propagate
        self._logger.setLevel(self._saved_level)

    def test_uses_rotating_file_handler(self) -> None:
        td = tempfile.mkdtemp()
        try:
            setup_interface_logger(log_file=str(Path(td) / "interface.log"))
            self.assertTrue(
                any(isinstance(h, RotatingFileHandler) for h in self._logger.handlers)
            )
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_max_bytes_and_backup_count(self) -> None:
        with patch.dict(os.environ, {"LOG_MAX_BYTES": "1024", "LOG_BACKUP_COUNT": "3"}):
            td = tempfile.mkdtemp()
            try:
                setup_interface_logger(log_file=str(Path(td) / "interface.log"))
                rh = next(
                    h for h in self._logger.handlers
                    if isinstance(h, RotatingFileHandler)
                )
                self.assertEqual(rh.maxBytes, 1024)
                self.assertEqual(rh.backupCount, 3)
            finally:
                shutil.rmtree(td, ignore_errors=True)

    def test_formatter_is_message_only(self) -> None:
        td = tempfile.mkdtemp()
        try:
            setup_interface_logger(log_file=str(Path(td) / "interface.log"))
            rh = next(
                h for h in self._logger.handlers
                if isinstance(h, RotatingFileHandler)
            )
            self.assertIsNotNone(rh.formatter)
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg="test message only", args=None, exc_info=None,
            )
            self.assertEqual(rh.formatter.format(record), "test message only")
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_propagate_is_false(self) -> None:
        td = tempfile.mkdtemp()
        try:
            setup_interface_logger(log_file=str(Path(td) / "interface.log"))
            self.assertFalse(self._logger.propagate)
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_disabled_file_skips_handler(self) -> None:
        with patch.dict(os.environ, {"INTERFACE_LOG_DISABLE_FILE": "true"}):
            td = tempfile.mkdtemp()
            try:
                setup_interface_logger(log_file=str(Path(td) / "disabled.log"))
                self.assertEqual(len(self._logger.handlers), 0)
            finally:
                shutil.rmtree(td, ignore_errors=True)

    def test_rotation_end_to_end(self) -> None:
        td = tempfile.mkdtemp()
        log_file = str(Path(td) / "iface_rotate.log")
        try:
            with patch.dict(os.environ, {"LOG_MAX_BYTES": "200", "LOG_BACKUP_COUNT": "2"}):
                setup_interface_logger(log_file=log_file)
                for i in range(50):
                    log_interface(InterfaceLogParams(
                        request_id=f"req_{i}",
                        interface_name=f"test_iface_{i}",
                        source_ip="127.0.0.1",
                        user_id="user1",
                        cost_time=i,
                        success=True,
                        return_code="200",
                        return_info="ok",
                        body_size=100,
                        extra_msg="",
                    ))
                for h in self._logger.handlers:
                    h.flush()
                files = [
                    f.name for f in Path(td).iterdir()
                    if f.name.startswith("iface_rotate.log")
                ]
                self.assertGreater(len(files), 1)
                self.assertLessEqual(len(files), 3)
        finally:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
