from machine import Pin, ADC, lightsleep, deepsleep
from time import sleep
import dht
from umqtt.simple import MQTTClient
import network
import json

# MQTT indstillinger
MQTT_SERVER = '192.168.4.1'
CLIENT_ID = 'esp32_sensor'
MQTT_TOPIC_TEMP = 'sensor/temperature'
MQTT_TOPIC_HUM = 'sensor/humidity'
MQTT_TOPIC_BAT = 'sensor/battery'
MQTT_TOPIC_ERROR = 'error'

# WiFi indstillinger
SSID = 'RaspberryPi_AP'
PASSWORD = 'RPI12345'

# Pins
DHT11_PIN = 16
BATTERI_ADC_PIN = 34

# Batteri-opsætning
BATTERI_MAX_VOLT = 3.842
BATTERI_MAX_ADC = 2240
BATTERI_MIN_VOLT = 3.0
BATTERI_MIN_ADC = int((BATTERI_MIN_VOLT / BATTERI_MAX_VOLT) * BATTERI_MAX_ADC)

# Sleep varighed
SLEEP_TID = 1200

def forbind_til_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    if not wlan.isconnected():
        wlan.connect(SSID, PASSWORD)
        timeout = 20
        while not wlan.isconnected() and timeout > 0:
            sleep(1)
            timeout -= 1

        if not wlan.isconnected():
            raise Exception("WiFi forbindelse fejlede")
    
    return wlan

def læs_batteri():
    batteri_adc = ADC(Pin(BATTERI_ADC_PIN))
    batteri_adc.atten(ADC.ATTN_11DB)
    adc_værdi = batteri_adc.read()
    
    if adc_værdi <= BATTERI_MIN_ADC:
        batteri_procent = 0
    elif adc_værdi >= BATTERI_MAX_ADC:
        batteri_procent = 100
    else:
        batteri_procent = int(((adc_værdi - BATTERI_MIN_ADC) / (BATTERI_MAX_ADC - BATTERI_MIN_ADC)) * 100)
    
    return batteri_procent

def dht11_data():
    sensor = dht.DHT11(Pin(DHT11_PIN))
    
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            sensor.measure()
            temp = sensor.temperature()
            hum = sensor.humidity()
            return temp, hum
        except OSError:
            if attempt < max_attempts - 1:
                sleep(2)
            else:
                raise Exception("DHT11 læsning fejlede")

def publish_mqtt(topic, data):
    client = None
    try:
        client = MQTTClient(CLIENT_ID, MQTT_SERVER)
        client.connect()
        client.publish(topic, json.dumps(data))
        client.disconnect()
        return True
    except Exception as e:
        if client:
            try:
                client.disconnect()
            except:
                pass
        return False

def send_error(error_msg):
    try:
        publish_mqtt(MQTT_TOPIC_ERROR, {'error': error_msg, 'client': CLIENT_ID})
    except:
        pass

def shutdown():
    # Permanent deep sleep - kræver boot-knap tryk for at vække
    deepsleep(0)

def main():
    sleep(1)
    
    try:
        batteri_procent = læs_batteri()
        
        if batteri_procent <= 5:
            shutdown()
        
        forbind_til_wifi()
        temp, hum = dht11_data()
        
        # Send sensor data
        publish_mqtt(MQTT_TOPIC_TEMP, {'temperature': temp})
        publish_mqtt(MQTT_TOPIC_HUM, {'humidity': hum})
        publish_mqtt(MQTT_TOPIC_BAT, {'battery': batteri_procent})
        
        lightsleep(SLEEP_TID)
        
    except Exception as e:
        error_msg = str(e)
        
        # Kun send fejl hvis det ikke er en MQTT fejl
        if 'MQTT' not in error_msg:
            send_error(error_msg)
        
        # Prøv igen
        sleep(5)
        try:
            batteri_procent = læs_batteri()
            if batteri_procent <= 5:
                shutdown()
            forbind_til_wifi()
            temp, hum = dht11_data()
            publish_mqtt(MQTT_TOPIC_TEMP, {'temperature': temp})
            publish_mqtt(MQTT_TOPIC_HUM, {'humidity': hum})
            publish_mqtt(MQTT_TOPIC_BAT, {'battery': batteri_procent})
        except Exception as e2:
            if 'MQTT' not in str(e2):
                send_error(str(e2))
        
        lightsleep(SLEEP_TID)

if __name__ == "__main__":
    main() 