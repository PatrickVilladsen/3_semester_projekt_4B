import json

dummy_json = '{"Temperature":23, "Humidity":10}'

def read_json():
    data = json.loads(dummy_json)
    return data["Humidity"]

print(read_json())