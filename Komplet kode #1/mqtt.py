import threading
import time
import json
import re
import paho.mqtt.client as mqtt
from sensor_data import data_opbevaring
from database import db
from config import (
    MQTT_BROKER_HOST,
    MQTT_BROKER_PORT,
    TOPIC_SENSOR_TEMP,
    TOPIC_SENSOR_HUM,
    TOPIC_SENSOR_BAT,
    TOPIC_VINDUE_STATUS,
    TOPIC_VINDUE_COMMAND,
    TOPIC_ERROR
)

# Regex patterns til validering
# validerer at tallet er float
FLOAT_PATTERN = re.compile(r'^-?\d+(\.\d+)?$')
# Validerer at tallet er positivt
POSITIV_PATTERN = re.compile(r'^\d+$')

# Her tjekkes og valideres indkommende værdier fra mqtt
def valider_værdi(value, min_val=None, max_val=None):
    if value is None:
        #Hvis der ikke er nogen værdi gør vi ikke mere
        return None
    # Her laver vi typekonvertering og gør vores værdi til string og fjerner mellemrum med .strip
    # Da vores regex forventer at tallet ikke har mellemrum er dette step vigtigt.
    # Et eksempel er hvis der blev modtaget " 12.5 " ville det blive lavet om til "12.5"
    værdi_str = str(value).strip()
    
    # Her tjekker vi om værdien passer med vores Regex validering
    if not FLOAT_PATTERN.match(værdi_str):
        return None
    
    # Her tjekkes der om tallene overskrider de min eller max værdier de burde kunne output
    try:
        num = float(værdi_str)
        if min_val is not None and num < min_val:
            return None
        if max_val is not None and num > max_val:
            return None
        return num
    #Error handling
    except (ValueError, TypeError):
        return None

#Den her bruges vi til at tjekke om et tal er mellem 0 og 100, det bruger vi til batteriprocent og fugtighed.
def valider_integer(value, min_val=0, max_val=100):
    if value is None:
        return None
    # Samme forklaring som længere oppe
    value_str = str(value).strip()
    
    # Vi sammenligner med vores Regex validering for at se om tallet er positivt.
    if not POSITIV_PATTERN.match(value_str):
        return None
    
    # og til sidst tjekker vi max og min værdier
    try:
        num = int(value_str)
        if num < min_val or num > max_val:
            return None
        return num
    except (ValueError, TypeError):
        return None

'''
Vi opretter en klasse til MQTT som gør brug af pythons "Thread" for concurrency.
Det betyder også at alle objekter der laves ud fra klassen kører med threads.
Vi ønsker at bruge Threads da MQTT så asynkront kan lytte efter indgående beskeder.
Threads giver også mulighed for at styrer funktionaliteten med f.eks. .start()
og vores graceful shutdown fra main.py'''
class MQTTClient(threading.Thread):
    '''
    Her bruger vi netop funktionalitet fra Thread -
    __init__ kalder konstruktøren fra "parent class" - som er (threading.Thread)
    super() sikrer at konstruktøren for "parent class" - threading.Thread kaldes først, før vi kører videre'''
    def __init__(self):
        '''daemon=True skaber en daemon-tråd som gør at vi kan lukke koden
        selvom at denne tråd stadig kører - det hjælper med vores graceful shutdown.
        name= gør at vi giver tråden et navn, så vi nemt kan finde fejl i error handling'''
        super().__init__(daemon=True, name="MQTT-Client")
        #Her opsætter vi den reelle mqtt client
        self.client = mqtt.Client()
        # Disse 2 er fra paho MQTT-biblioteket og vi bruger dem til hændelser så det tjekkes asynktront
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        #Sættes til True når der er forbindelse længere nede
        self.connected = False
        # Bruges i main.py når vi skal slukke for mqtt som følge af graceful shutdown
        self.running = True
        # Placeholder til adressen som gives i main.py til at PUSH'e nye beskeder fra MQTT til APP.py og altså til frontend
        self._websocket_callback = None
        
        # Vi opretter en funktion der giver os mulighed for at definere callback-adressen snere i main.py
        # Denne metode kaldes for en "Setter" og har som job at sætte værdien af en intern variabel.
    def set_websocket_callback(self, callback):
        self._websocket_callback = callback
        
    # Når en client forbinder tjekkes der her om der blev skabt forbindelse
    def on_connect(self, client, userdata, flags, rc):
        # en rc på 0 betyder CONNACK_ACCEPTED og altså at der blev skabt forbindelse.
        if rc == 0:
            self.connected = True
            
            # Den nye client subcriber på alle vores topics
            client.subscribe(TOPIC_SENSOR_TEMP)
            client.subscribe(TOPIC_SENSOR_HUM)
            client.subscribe(TOPIC_SENSOR_BAT)
            client.subscribe(TOPIC_VINDUE_STATUS)
            client.subscribe(TOPIC_ERROR) 
            
        else:
            #Laves om til error_logs på senere tidspunkt
            db.log_error('MQTT', f"Fejlkode: {rc}")

    
    # Her modtager, tjekker og validerer vi alle beskeder der modtages
    def on_message(self, client, userdata, msg):

        try:
            # Her er vores besked
            topic = msg.topic
            # Her unloader/decoder vi den rå data til en json-string
            payload_str = msg.payload.decode()
            #Her tager vi og laver json-stringen om til en python dictionary
            payload = json.loads(payload_str)
            
            # Hvis topic stammer fra vores DHT11 fanges det her og værdien tjekkes
            if topic == TOPIC_SENSOR_TEMP:
                temp = valider_værdi(payload.get('temperature'), min_val=-40, max_val=85)
                if temp is not None:
                    # Den opdaterer så vores data opbevaring i vores sensor_data.py fil
                    data_opbevaring.update_sensor_data('temperature', temp)
                    # Og gemmes i database
                    db.log_sensor('ESP32_UDENFOR', 'temperature', temp)
                    # Her giver besked til websockets clienterne - altså dem som kigger på vores frontend
                    if self._websocket_callback:
                        self._websocket_callback('sensor')
                else:
                    #Laves om til db error_logging
                    db.log_error('MQTT', f"Fejl værdi: {payload.get('temperature')}")
                
                #Samme som Temperatur - bare med luft fugtighed
            elif topic == TOPIC_SENSOR_HUM:
                hum = valider_integer(payload.get('humidity'), min_val=0, max_val=100)
                if hum is not None:
                    data_opbevaring.update_sensor_data('humidity', hum)
                    db.log_sensor('ESP32_UDENFOR', 'humidity', hum)
                    
                    if self._websocket_callback:
                        self._websocket_callback('sensor')
                else:
                    db.log_error('MQTT', f"Fejl værdi: {payload.get('humidity')}")
                
                # Samme - nu bare batteriprocent
            elif topic == TOPIC_SENSOR_BAT:
                bat = valider_integer(payload.get('battery'), min_val=0, max_val=100)
                if bat is not None:
                    data_opbevaring.update_sensor_data('battery', bat)
                    db.log_sensor('ESP32_UDENFOR', 'battery', bat)
                    
                    if self._websocket_callback:
                        self._websocket_callback('sensor')
                else:
                    db.log_error('MQTT', f"Fejl værdi: {payload.get('battery')}")
                
                #Her tjekker vi om det er opdatering af vinduets status
            elif topic == TOPIC_VINDUE_STATUS:
                status = payload.get('status', '')
                #Kan kun bestå af 3 tilstande
                if status in ['aaben', 'lukket', 'ukendt']:
                    # Skal lige tjekkes med en motor for at præcisere værdierne
                    position = valider_integer(payload.get('position', 0), min_val=0, max_val=10000)
                    max_pos = valider_integer(payload.get('max_position', 0), min_val=0, max_val=10000)
                    
                    #Her oprettes et dictionary som rengør dataen før den sendes videre til vores sensor_data.py fil
                    ''' 
                    Vi bruger nogle if/else argumenter på ´en enkelt linje - det hedder ternary operator
                    Det ville kunne se sådan her ud hvis vi skrev det som et normalt if/else statement:
                    if position is not None:
                        result = position
                    else:
                        result = 0'''
                    valideret_status = {
                        'status': status,
                        'position': position if position is not None else 0,
                        'max_position': max_pos if max_pos is not None else 0
                    }
                    
                    data_opbevaring.update_vindue_status(valideret_status)
                    db.log_sensor('ESP32_WINDOW', 'window_position', position if position is not None else 0)
                    # Giver selvfølgelig besked til frontend
                    if self._websocket_callback:
                        self._websocket_callback('vindue')
                else:
                    db.log_error('MQTT', f"Fejl værdi: {status}")
                
                #Tjekkes efter error fra vores ESP32'er
            elif topic == TOPIC_ERROR:
                error_msg = payload.get('error', '')
                client_id = payload.get('client', 'unknown')
                
                if error_msg:
                    # Opdater error i data_opbevaring
                    data_opbevaring.update_error({
                        'error': error_msg,
                        'client': client_id,
                        'timestamp': time.time()
                    })
                    
                    # Giv besked til frontend
                    if self._websocket_callback:
                        self._websocket_callback('error')
                else:
                    db.log_error('MQTT', f"Tom fejlbesked fra {client_id}")
                
            # Hvis payload_str ikke er gyldig JSON-format
        except json.JSONDecodeError as e:
            db.log_error('MQTT', f"JSON decode fejl: {e} | Rå payload: {msg.payload.decode()}")
            #Andre fejl
        except Exception as e:
            db.log_error('MQTT', f"Anden fejl: {e}")
    
    # Dette er Når clienten skal oprette forbindelse til brokeren
    def run(self):
        forsøg = 0
        max_forsøg = 5
        
        # Max 5 forsøg
        while self.running and forsøg < max_forsøg:
            try:
                # Her er ipv4 og port til brokeren - 60 er en inaktivitets ping der sikrer at forbindelsen holdes.
                # Der sendes en ping efter 60 sekunder hvis der ikke har været kommunikation for at opretholde forbindelsen.
                self.client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
                # start loop
                self.client.loop_start()
                
                # Sættes til 0 når forbindelsen er skabt
                forsøg = 0
                
                # forsøger hvert 5. sekund
                while self.running:
                    if not self.connected:
                        # Hvis forbindelse blev tabt, prøves der at reconnect
                        try:
                            self.client.reconnect()
                            db.log_event('MQTT', 'Reconnected til MQTT broker')
                        except:
                            pass
                    time.sleep(5)
                    
            except ConnectionRefusedError:
                forsøg += 1
                time.sleep(5)
                
            except Exception as e:
                forsøg += 1
                time.sleep(5)
        
        if forsøg >= max_forsøg:
            # Data base log
            db.log_error('MQTT', f"Kunne ikke forbinde efter {max_forsøg} forsøg")    
    # Dette er hvad der bruges for at kunne styre mqtt fra vores webserver
    def publish_command(self, command: str):
        #Tjekker først om der er forbindelse
        if not self.connected:
            #Vi smider en raise så koden ikke kører videre hvis der er fejl her.
            # Fejlen bliver "raised" til der hvor funktionen blev kaldt
            raise Exception("Ingen MQTT forbindelse")
        # Her klargører vi vores payload som skal sendes til vores ESP32
        try:
            payload = json.dumps({'command': command})
            result = self.client.publish(TOPIC_VINDUE_COMMAND, payload)
            
            # Vi kører dette for ikke at skulle bruge if/else, her tjekker vi kun efter fejl
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                #Smider den tilbage til hvor funktionen blev kaldt
                raise Exception(f"Kunne ikke sende kommandoen: {result.rc}")
            # mere til database error logs
        except Exception as e:
            db.log_error('MQTT', f"Publish fejl: {e}")
            raise Exception(f"MQTT publish fejlede: {e}")
        
    # Her slukker vi ned for mqtt clienten og stopper alle forbindelser
    def stop(self):
        self.running = False
        self.client.loop_stop()
        self.client.disconnect()
# Vi opretter vores globale instans - hele koden har været opsætning af den her
mqtt_client = MQTTClient()