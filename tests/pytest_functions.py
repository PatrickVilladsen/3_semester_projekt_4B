import json
from pathlib import Path

def read_json(json_fil: Path) -> dict:
    try:
        with open(json_fil) as json_file:
            json_data = json.load(json_file)
            return json_data
    except json.decoder.JSONDecodeError:
        return "Decoding error: file could be empty"
    except FileNotFoundError as e:
        return e

if __name__ == "__main__":
    print(read_json("data1.json"))