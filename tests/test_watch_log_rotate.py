"""watch 日志滚动：超过阈值时把 watch.log 滚成 .1/.2/.3。"""
from pathlib import Path

from app.jobs.watch import _rotate_log


def test_rotate_log_skips_small_file(tmp_path):
    log = tmp_path / "watch.log"
    log.write_text("small", encoding="utf-8")
    assert _rotate_log(log, max_bytes=1024) == []
    assert log.exists()


def test_rotate_log_moves_oversized_file(tmp_path):
    log = tmp_path / "watch.log"
    log.write_text("x" * 4096, encoding="utf-8")
    rotated = _rotate_log(log, max_bytes=1024, keep=3)
    assert len(rotated) == 1
    assert not log.exists()
    assert (tmp_path / "watch.log.1").exists()
    assert (tmp_path / "watch.log.1").stat().st_size == 4096


def test_rotate_log_shifts_older_files(tmp_path):
    log = tmp_path / "watch.log"
    log.write_text("x" * 4096, encoding="utf-8")
    (tmp_path / "watch.log.1").write_text("old1", encoding="utf-8")
    (tmp_path / "watch.log.2").write_text("old2", encoding="utf-8")

    _rotate_log(log, max_bytes=1024, keep=3)

    assert (tmp_path / "watch.log.1").read_text(encoding="utf-8") == "x" * 4096
    assert (tmp_path / "watch.log.2").read_text(encoding="utf-8") == "old1"
    assert (tmp_path / "watch.log.3").read_text(encoding="utf-8") == "old2"
