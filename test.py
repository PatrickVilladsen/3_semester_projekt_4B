import json

dummy_json = '{"Temperature":23, "Humidity":10}'

def read_json(json_data):
    data = json.loads(json_data)
    return data["Humidity"]

print(read_json(dummy_json))