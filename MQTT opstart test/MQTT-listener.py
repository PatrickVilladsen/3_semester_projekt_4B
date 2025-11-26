import paho.mqtt.client as mqtt
import json

def on_connect(client, userdata, flags, rc):
    print(f"Forbundet til MQTT broker (kode: {rc})")
    client.subscribe("esp32/calculate")
    print("Lytter efter værdier fra ESP32")

def on_message(client, userdata, msg):
    print(f"\nBeked modtaget")
    print(f"Topic: {msg.topic}")
    
    try:
        # Få extractet dataen fra ESP32
        data = json.loads(msg.payload.decode())
        a = data['a']
        b = data['b']
        
        print(f"Modtaget: a={a}, b={b}")
        
        # Beregn resultatet
        result = a * b
        print(f"Beregning: {a} * {b} = {result}")
        
        # Send resultat tilbage til ESP32
        response = {
            'result': result,
            'a': a,
            'b': b
        }
        client.publish("esp32/result", json.dumps(response))
        print(f"Returneret resultat: {result}")
        
    except Exception as e:
        print(f"Fejl: {e}")

# Opretter MQTT klient
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

# Forbind til lokal broker
print("Starter MQTT og forbinder til broker")
client.connect("192.168.4.1", 1883, 60)

# Start loop
client.loop_forever()