from umqtt.simple import MQTTClient
import network
import time
import json
import random

# WiFi credentials
SSID = 'RaspberryPi_AP'
PASSWORD = 'RPI12345'

# MQTT settings
MQTT_SERVER = '192.168.4.1'
CLIENT_ID = 'esp32_calculator'

# Callback for modtagne beskeder
def mqtt_callback(topic, msg):
    print(f"\nResultat modtaget")
    try:
        data = json.loads(msg)
        result = data['result']
        a = data['a']
        b = data['b']
        
        print(f"Modtaget svar fra Raspberry Pi!")
        print(f"Resultat: {a} * {b} = {result}")
        
    except Exception as e:
        print(f"Fejl: {e}")

# Forbind til WiFi
print('Forbinder til WiFi.')
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(SSID, PASSWORD)

while not wlan.isconnected():
    print('.', end='')
    time.sleep(0.5)

print('\nForbundet til WiFi')
print('IP:', wlan.ifconfig()[0])

# Forbind til MQTT
print(f'Forbinder til MQTT broker {MQTT_SERVER}...')
client = MQTTClient(CLIENT_ID, MQTT_SERVER)
client.set_callback(mqtt_callback)
client.connect()
client.subscribe(b'esp32/result')
print('Forbundet til MQTT')

# Hovedloop
print('\nOpretter beregninger\n')
while True:
    # Generer tilfældige tal
    a = random.randint(1, 10)
    b = random.randint(1, 10)
    
    print(f"Sender: a={a}, b={b}")
    
    # Send data til Raspberry Pi
    data = {
        'a': a,
        'b': b
    }
    client.publish(b'esp32/calculate', json.dumps(data))
    
    # Vent på svar (tjek for indkommende beskeder)
    for _ in range(10):
        client.check_msg()
        time.sleep(0.1)
    
    time.sleep(5)