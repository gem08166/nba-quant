"""watch.Tee：pythonw（无控制台）下 sys.stdout 为 None 时仍能写日志。"""
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
