import requests

response = requests.get("http://127.0.0.1:5000/DHT11_data")

print(response.json())