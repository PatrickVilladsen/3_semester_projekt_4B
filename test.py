import json

dummy_json = '{"Temperature":23, "Humidity":10}'

def read_humidity(json_data):
    data = json.loads(json_data)
    return data["Humidity"]

print(read_humidity(dummy_json))