

from machine import Pin, ADC, deepsleep
from time import sleep
import dht
from umqtt.simple import MQTTClient
import network
import json


MQTT_SERVER = '192.168.4.1'


ENHEDS_ID = 'esp32_sensor'


MQTT_TOPIC_TEMP = 'sensor/temperatur'


MQTT_TOPIC_FUGT = 'sensor/luftfugtighed'


MQTT_TOPIC_BAT = 'sensor/batteri'


MQTT_TOPIC_FEJLBESKED = 'fejlbesked'


MQTT_TIMEOUT = 5



SSID = 'RaspberryPi_AP'


PASSWORD = 'RPI12345'


WIFI_TIMEOUT = 20



DHT11_PIN = 16


BATTERI_ADC_PIN = 34



BATTERI_MAX_VOLT = 3.842


BATTERI_MAX_ADC = 2380


BATTERI_MIN_VOLT = 3.0


BATTERI_MIN_ADC = int((BATTERI_MIN_VOLT / BATTERI_MAX_VOLT) * BATTERI_MAX_ADC)


BATTERI_SHUTDOWN_GRÆNSE = 5



SLEEP_TID = 900*1000


GENFORSØGS_DELAY = 5



DHT11_MAX_FORSØG = 3


DHT11_FORSØGS_DELAY = 2




def forbind_til_wifi():

    print("Forbinder til WiFi: {}".format(SSID))
    
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    if wlan.isconnected():
        print("WiFi allerede forbundet")
        print("IP adresse: {}".format(wlan.ifconfig()[0]))
        return wlan
    
    wlan.connect(SSID, PASSWORD)
    
    timeout = WIFI_TIMEOUT
    while not wlan.isconnected() and timeout > 0:
        sleep(1)
        timeout -= 1
    
    if not wlan.isconnected():
        raise Exception("WiFi forbindelse timeout efter {}s".format(WIFI_TIMEOUT))
    
    print("WiFi forbundet")
    print("IP adresse: {}".format(wlan.ifconfig()[0]))
    
    return wlan



def læs_batteri():

    batteri_adc = ADC(Pin(BATTERI_ADC_PIN))
    batteri_adc.atten(ADC.ATTN_11DB)
    
    adc_værdi = batteri_adc.read()
    
    print("Batteri ADC: {}".format(adc_værdi))
    
    if adc_værdi == 0:
        return 0
    elif adc_værdi <= BATTERI_MIN_ADC:
        return 0
    elif adc_værdi >= BATTERI_MAX_ADC:
        return 100
    
    batteri_procent = int(
        ((adc_værdi - BATTERI_MIN_ADC) / (BATTERI_MAX_ADC - BATTERI_MIN_ADC)) * 100
    )
    
    batteri_procent = max(0, min(100, batteri_procent))
    
    print("Batteri niveau: {}%".format(batteri_procent))
    
    return batteri_procent


def læs_dht11_data():

    print("Læser DHT11 sensor")
    
    sensor = dht.DHT11(Pin(DHT11_PIN))
    
    for forsøg in range(DHT11_MAX_FORSØG):
        try:
            sensor.measure()
            
            temp = sensor.temperature()
            hum = sensor.humidity()
            
            print("Temperatur: {}°C".format(temp))
            print("Fugtighed: {}%".format(hum))
            
            return temp, hum
        
        except OSError as fejl:
            print("DHT11 fejl (forsøg {}/{}): {}".format(
                forsøg + 1, DHT11_MAX_FORSØG, fejl
            ))
            
            if forsøg < DHT11_MAX_FORSØG - 1:
                sleep(DHT11_FORSØGS_DELAY)
    
    raise Exception("DHT11 læsning fejlede efter {} forsøg".format(
        DHT11_MAX_FORSØG
    ))



def publicer_mqtt(topic, data):

    klient = None
    
    try:
        print("Publisher til {}: {}".format(topic, data))
        
        klient = MQTTClient(ENHEDS_ID, MQTT_SERVER)
        
        klient.connect()
        
        payload = json.dumps(data)
        
        klient.publish(topic, payload, qos=1)
        
        klient.disconnect()
        
        print("MQTT publish succesfuld")
        return True
        
    except Exception as fejl:
        print("MQTT publish fejl: {}".format(fejl))
        
        if klient:
            try:
                klient.disconnect()
            except:
                pass
        
        return False


def send_fejl(fejl_besked):

    try:
        fejl_data = {
            'fejl': fejl_besked,
            'enhed': ENHEDS_ID
        }
        publicer_mqtt(MQTT_TOPIC_FEJLBESKED, fejl_data)
    except:
        pass



def luk_ned():

    print("Problem: Batteri under {}% - går i deepsleep".format(
        BATTERI_SHUTDOWN_GRÆNSE
    ))
    
    try:
        send_fejl("Lavt batteri: (<{}%)".format(
            BATTERI_SHUTDOWN_GRÆNSE
        ))
        sleep(1)
    except:
        pass
    
    deepsleep(64800)



def _udfør_måling():

    print("Starter måle-cyklus")
    
    batteri_procent = læs_batteri()
    
    if batteri_procent <= BATTERI_SHUTDOWN_GRÆNSE:
        luk_ned()
    
    forbind_til_wifi()
    
    temp, fugt = læs_dht11_data()
    
    print("Sender data til MQTT broker")
    
    publicer_mqtt(MQTT_TOPIC_TEMP, {'temperatur': temp})
    publicer_mqtt(MQTT_TOPIC_FUGT, {'luftfugtighed': fugt})
    publicer_mqtt(MQTT_TOPIC_BAT, {'batteri': batteri_procent})
    
    print("Måle-cyklus fuldført")

def udfør_måling_med_retry():

    try:
        _udfør_måling()
        
    except Exception as fejl:
        fejl_besked = str(fejl)
        print("FEJL under måling: {}".format(fejl_besked))
        
        if 'MQTT' not in fejl_besked:
            send_fejl(fejl_besked)
        
        print("Venter {}s før der forsøges igen".format(GENFORSØGS_DELAY))
        sleep(GENFORSØGS_DELAY)
        
        print("Forsøger igen")
        try:
            _udfør_måling()
            print("succesfuld")
            
        except Exception as fejl2:
            fejl_besked2 = str(fejl2)
            print("Fejl ved andet forsøg: {}".format(fejl_besked2))
            
            if 'MQTT' not in fejl_besked2:
                send_fejl("fejl ved andet forsøg: {}".format(fejl_besked2))
            print("Fortsætter til sleep efter to fejl")



def main():

    print("ESP32 tænder")
    print("Sleep interval: {} sekunder".format(SLEEP_TID))
    print("Batteri slukker: <{}%".format(BATTERI_SHUTDOWN_GRÆNSE))
    
    sleep(1)
    
    udfør_måling_med_retry()
        
    print("Går i deepsleep i {} sekunder".format(SLEEP_TID))
        
    deepsleep(SLEEP_TID)
        
if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception:
            deepsleep(60*1000)