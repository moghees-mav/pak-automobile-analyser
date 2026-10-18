import sys

print(sys.version)
for name in ("flask", "pandas", "bs4", "requests", "lxml"):
    try:
        module = __import__(name)
        print(name, getattr(module, "__version__", "?"))
    except Exception as exc:  # noqa: BLE001
        print(name, "MISSING", exc)
