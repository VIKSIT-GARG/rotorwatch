import pytest

from drone_assistant.sources import Registry
from drone_assistant.storage import Store


@pytest.fixture
def store():
    store = Store(":memory:")
    yield store
    store.close()


@pytest.fixture
def registry():
    return Registry()


@pytest.fixture
def product(store):
    pid = store.add_product("https://robu.in/product/pi-5/", "robu", "Raspberry Pi 5 4GB")
    store.watch("42", pid)
    return pid
