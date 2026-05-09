import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "resume_web.settings")


def pytest_collection_modifyitems(session, config, items):
	items.sort(key=lambda item: item.nodeid.startswith("tests/test_llm.py::"))