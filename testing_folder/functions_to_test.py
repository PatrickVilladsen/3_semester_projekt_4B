import json
from pathlib import Path
import os

# Pytest - function 1
def read_json(json_file: Path) -> dict:
    """Read from a given json-file.
    Args:
        json_fil(Path): given json-file.
    Returns:
        dict: json.load(json_file).
    """
    try:
        if os.path.exists(json_file):
            with open(json_file, encoding='utf-8') as data:
                json_data = json.load(data)
                return json_data
        else:
            raise FileNotFoundError
    except json.decoder.JSONDecodeError:
        return "Decoding error: file could be empty"

if __name__ == "__main__":
    print(read_json("testing_json.json"))