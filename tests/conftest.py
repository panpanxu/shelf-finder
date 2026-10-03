import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def compact(text):
    return "".join(text.split()).lower()


@pytest.fixture
def shelf():
    def load(name):
        return (FIXTURES / f"{name}.jpg").read_bytes(), json.loads((FIXTURES / f"{name}.json").read_text())

    return load
