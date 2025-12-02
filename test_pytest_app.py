import pytest
from app.py import *

def test_read_json():
    assert pytest.read_json("test_json.json") == "something" #true

