import time

import pytest

from app.core.semantic import _invoke_with_deadline


def test_invoke_with_deadline_returns_result():
    assert _invoke_with_deadline(lambda value: value + 1, 4, timeout=1) == 5


def test_invoke_with_deadline_raises_on_timeout():
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="provider call exceeded"):
        _invoke_with_deadline(time.sleep, 0.25, timeout=0.05)
    assert time.monotonic() - started < 0.20
