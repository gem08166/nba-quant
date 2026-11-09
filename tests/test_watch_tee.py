"""watch.Tee：pythonw（无控制台）下 sys.stdout 为 None 时仍能写日志。"""
import io
import sys

from app.jobs.watch import Tee


def test_tee_handles_none_stdout(tmp_path):
    path = tmp_path / "watch.log"
    tee = Tee(path)
    tee.stdout = None  # 模拟 pythonw：无控制台
    tee.write("hello\n")
    tee.flush()
    assert path.read_text(encoding="utf-8") == "hello\n"


def test_tee_writes_both_streams(tmp_path):
    path = tmp_path / "watch.log"

    class Sink:
        def __init__(self):
            self.buf = []

        def write(self, text):
            self.buf.append(text)

        def flush(self):
            pass

    tee = Tee(path)
    sink = Sink()
    tee.stdout = sink
    tee.write("abc")
    assert sink.buf == ["abc"]
    assert path.read_text(encoding="utf-8") == "abc"


def test_tee_falls_back_when_log_cannot_open(tmp_path, monkeypatch):
    # 用一个目录冒充日志文件 → open() 抛 IsADirectoryError(OSError)
    target = tmp_path / "watch.log"
    target.mkdir()

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    tee = Tee(target)

    assert tee.file is None
    tee.write("hello\n")
    tee.flush()
    assert buf.getvalue() == "hello\n"


def test_tee_writes_file(tmp_path, monkeypatch):
    target = tmp_path / "watch.log"
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    tee = Tee(target)
    tee.write("line\n")
    tee.flush()
    assert target.read_text(encoding="utf-8") == "line\n"
