import requests

response = requests.get("http://127.0.0.1:5000/add_DHT11")

print(response.json())