# Makes scripts importable as a package so entry points can be run as
# `python -m scripts.<name>` from the repo root. This matters because a plain
# `python scripts/<name>.py` puts scripts/ first on sys.path, and inspect.py
# here shadows the stdlib inspect module (breaking numpy/httpx imports).
