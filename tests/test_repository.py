import sys


def test_supported_python_runtime():
    assert sys.version_info >= (3, 11)
