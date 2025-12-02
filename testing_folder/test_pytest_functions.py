import pytest
from functions_to_test import *

def addition(a,b):
    return a+b

# Pytest - read_json()
def test_read_json():
    
    assert read_json("testing_json.json") == {'key': 'value'} #true
    #assert read_json("does_not_exist.json") == {'key': 'value'} #false
    with pytest.raises(FileNotFoundError):
        read_json("does_not_exist.json") #true
    #with pytest.raises():
     #   read_json("empty_json.json")
    #assert read_json("empty_json.json") == {'key': 'value'} #false
    assert read_json("empty_json.json") == "Decoding error: file could be empty" #true
    
    assert addition(1,1) == 2 #true