import pytest
from functions_to_test import *

# Pytest - read_json()
def test_read_json():
    assert pytest.read_json("test_json.json") == "{'key': 'value'}" #true
    assert pytest.read_json("does_not_exist.json") == "{'key': 'value'}" #false
    assert pytest.read_json("does_not_exist.json") == "[Errno 2] No such file or directory: 'does_not_exist.json'" #true
    assert pytest.read_json("empty_json.json") == "{'key': 'value'}" #false
    assert pytest.read_json("empty_json.json") == "Decoding error: file could be empty" #true