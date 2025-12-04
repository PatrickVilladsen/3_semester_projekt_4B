import json
from pathlib import Path
import os

# Pytest - function 1
def read_from_json(json_file: Path) -> dict:
    """Read from a given json-file.
    Args:
        json_fil(Path): given json-file.
    Returns:
        dict: json.load(json_file).
    """
    try:
        if os.path.exists(json_file):
            with open(json_file, 'r', encoding='utf-8') as file:
                json_data = json.load(file)
                return json_data
        else:
            raise FileNotFoundError
    except json.decoder.JSONDecodeError:
        return "Decoding error: file could be empty"

def write_to_json(data: dict, json_file: Path) -> None:
    """Write to a given json-file.
    Args:
        data(dict): the data you want to write, 
        json_file(Path): the file you want to write to.
    Returns:
        None: None.
    """
    if os.path.exists(json_file):
        with open(json_file, 'w') as file:
            json.dump(data, file)
    else:
        raise FileNotFoundError

if __name__ == "__main__":
    write_to_json({'key': 'BIG vålue'}, "testing_json.json")
    print(read_from_json("testing_json.json"))