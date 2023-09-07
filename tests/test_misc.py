import importlib

from homeassistant.components.sensor import DOMAIN

from custom_components.yandex_dialogs import source_handler

assert DOMAIN


def test_source():
    source = """
from requests import __title__

def test1():
    return len([1, 2, 3])

def test2():
    return test1()

def handler(event: dict, context: dict) -> dict:
    return {"event": event, "context": context, "test": test2(), "import": __title__}
"""
    # check arguments, external functions and imports
    handle = source_handler(source)
    response = handle(1, 2)
    assert response == {"context": 2, "event": 1, "import": "requests", "test": 3}
