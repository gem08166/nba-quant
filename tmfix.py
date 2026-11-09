from pathlib import Path

p = Path("app/web/live.js")
t = p.read_text(encoding="utf-8")
old = "/api/live?date=${encodeURIComponent(requestedDate)}"
n = t.count(old)
t = t.replace(old, "/api/live?date=${encodeURIComponent(bjToSlate(requestedDate))}")
p.write_text(t, encoding="utf-8")
print("replaced", n)
for i, line in enumerate(t.splitlines(), 1):
    if "bjToSlate" in line:
        print(i, line.strip()[:90])
