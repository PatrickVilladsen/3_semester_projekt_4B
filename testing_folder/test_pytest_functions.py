import pytest
from functions_to_test import *

# Pytest - read_json()
def test_read_from_json():
    
    assert read_from_json("testing_json.json") == {'key': 'value'} #true

    with pytest.raises(FileNotFoundError):
        read_from_json("does_not_exist.json") #true

    assert read_from_json("empty_json.json") == "Decoding error: file could be empty" #true

