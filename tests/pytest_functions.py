import json
from pathlib import Path

def read_json(json_file: Path) -> dict:
    """Read from a given json-file.
    Args:
        json_fil(Path): given json-file.
    Returns:
        dict: json.load(json_file).
    """
    try:
        with open(json_file) as data:
            json_data = json.load(data)
            return json_data
    except json.decoder.JSONDecodeError:
        return "Decoding error: file could be empty"
    except FileNotFoundError as e:
        return e

if __name__ == "__main__":
    print(read_json("data1.json"))