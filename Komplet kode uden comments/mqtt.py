

import threading
import time
import json
import re
from typing import Optional, Dict, Any, Callable, Pattern
import paho.mqtt.client as mqtt
from sensor_data import data_opbevaring
from database import db
from config import (
    MQTT_BROKER_HOST,
    MQTT_BROKER_PORT,
    TOPIC_SENSOR_TEMP,
    TOPIC_SENSOR_FUGT,
    TOPIC_SENSOR_BAT,
    TOPIC_VINDUE_STATUS,
    TOPIC_VINDUE_KOMMANDO,
    TOPIC_FEJLBESKED,
    ENHEDS_ID
)



FLOAT_VALIDERING: Pattern[str] = re.compile(r'^-?\d+(\.\d+)?$')


POSITIV_VALIDERING: Pattern[str] = re.compile(r'^\d+$')



ESP32_SENSOR_ID = 'esp32_sensor'



BATCH_TIMEOUT = 2.0



MAX_FORBINDELSES_FORSØG = 5


GENFORSØGS_DELAY = 5


KEEPALIVE_INTERVAL = 60



def valider_værdi(
    værdi: Any,
    min_værdi: Optional[float] = None,
    max_værdi: Optional[float] = None
) -> Optional[float]:

    if værdi is None:
        return None
    
    værdi_str = str(værdi).strip()
    
    if not FLOAT_VALIDERING.match(værdi_str):
        return None
    
    try:
        nummer = float(værdi_str)
        
        if min_værdi is not None and nummer < min_værdi:
            return None
        if max_værdi is not None and nummer > max_værdi:
            return None
        
        return nummer
        
    except (ValueError, TypeError):
        return None


def valider_heltal(
    værdi: Any,
    min_værdi: int = 0,
    max_værdi: int = 100
) -> Optional[int]:

    if værdi is None:
        return None
    
    værdi_str = str(værdi).strip()
    
    if not POSITIV_VALIDERING.match(værdi_str):
        return None
    
    try:
        nummer = int(værdi_str)
        
        if nummer < min_værdi or nummer > max_værdi:
            return None
        
        return nummer
        
    except (ValueError, TypeError):
        return None



class MQTTKlient(threading.Thread):

    
    def __init__(self) -> None:

        super().__init__(daemon=True, name="MQTT-Klient")
        
        self.klient: mqtt.Client = mqtt.Client()
        self.klient.on_connect = self.on_connect
        self.klient.on_message = self.on_message
        
        self.forbundet: bool = False
        self.kører: bool = True
        
        self._websocket_callback: Optional[Callable[[str], None]] = None

        
        self._sensor_batch: Dict[str, bool] = {
            'temp': False,
            'fugt': False,
            'bat': False
        }


        self._sidste_batch_tid: float = 0

        
        print("MQTT klient initialiseret")
    
    def sæt_websocket_callback(self, callback: Callable[[str], None]) -> None:

        self._websocket_callback = callback
        print("WebSocket callback konfigureret")
    
    def on_connect(
        self,
        klient: mqtt.Client,
        brugerdata: Any,
        flags: Dict[str, Any],
        returkode: int
    ) -> None:

        if returkode == 0:
            self.forbundet = True
            
            klient.subscribe(TOPIC_SENSOR_TEMP, qos=1)
            klient.subscribe(TOPIC_SENSOR_FUGT, qos=1)
            klient.subscribe(TOPIC_SENSOR_BAT, qos=1)
            klient.subscribe(TOPIC_VINDUE_STATUS, qos=1)
            klient.subscribe(TOPIC_FEJLBESKED, qos=1)
            
            print(f"MQTT forbundet til broker: {MQTT_BROKER_HOST}")
            db.gem_system_log(
                ENHEDS_ID,
                'MQTT',
                f"Forbundet til broker: {MQTT_BROKER_HOST}"
            )
        else:
            self.forbundet = False
            print(f"MQTT forbindelse afvist, kode: {returkode}")
            db.gem_fejl(
                ENHEDS_ID,
                'MQTT',
                f"Forbindelse afvist, kode: {returkode}"
            )
    
    def _notificer_frontend(self, opdaterings_type: str) -> None:

        if self._websocket_callback:
            try:
                import asyncio
                
                loop = asyncio.get_event_loop()
                
                if loop.is_running():
                    asyncio.create_task(
                        self._websocket_callback(opdaterings_type)
                    )
            except Exception as fejl:
                print(f"WebSocket notify fejl: {fejl}")
    
    def on_message(
        self,
        klient: mqtt.Client,
        brugerdata: Any,
        besked: mqtt.MQTTMessage
    ) -> None:

        try:
            emne = besked.topic
            payload_str = besked.payload.decode()
            payload = json.loads(payload_str)
            
            print(f"MQTT modtaget på {emne}: {payload}")
            
            if emne == TOPIC_SENSOR_TEMP:
                værdi = valider_værdi(
                    payload.get('temperatur'),
                    min_værdi=-25,
                    max_værdi=40
                )
                
                if værdi is not None:
                    data_opbevaring.opdater_sensor_data('temperatur', værdi)
                    
                    db.gem_sensor_data(
                        ESP32_SENSOR_ID,
                        'DHT11',
                        'temperatur',
                        værdi
                    )
                    
                    self._sensor_batch['temp'] = True
                    self._sidste_batch_tid = time.time()
                    
                    print(f"Temperatur gemt: {værdi}°C")
                else:
                    fejl_besked = f"Ugyldig temperatur: {payload.get('temperatur')}"
                    db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
            
            elif emne == TOPIC_SENSOR_FUGT:
                værdi = valider_heltal(
                    payload.get('luftfugtighed'),
                    min_værdi=0,
                    max_værdi=100
                )
                
                if værdi is not None:
                    data_opbevaring.opdater_sensor_data('luftfugtighed', værdi)
                    
                    db.gem_sensor_data(
                        ESP32_SENSOR_ID,
                        'DHT11',
                        'luftfugtighed',
                        værdi
                    )
                    
                    self._sensor_batch['fugt'] = True
                    self._sidste_batch_tid = time.time()
                    
                    print(f"Luftfugtighed gemt: {værdi}%")
                else:
                    fejl_besked = f"Ugyldig fugtighed: {payload.get('luftfugtighed')}"
                    db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
            
            elif emne == TOPIC_SENSOR_BAT:
                værdi = valider_heltal(
                    payload.get('batteri'),
                    min_værdi=0,
                    max_værdi=100
                )
                
                if værdi is not None:
                    data_opbevaring.opdater_sensor_data('batteri', værdi)
                    
                    db.gem_sensor_data(
                        ESP32_SENSOR_ID,
                        'Power',
                        'batteri',
                        værdi
                    )
                    
                    self._sensor_batch['bat'] = True
                    
                    print(f"Batteri gemt: {værdi}%")
                    
                    nu = time.time()
                    alt_modtaget = all(self._sensor_batch.values())
                    timeout = (nu - self._sidste_batch_tid > BATCH_TIMEOUT)
                    
                    if alt_modtaget or timeout:
                        print("Sensor batch komplet - opdaterer frontend")
                        self._notificer_frontend('sensor')
                        
                        self._sensor_batch = {k: False for k in self._sensor_batch}
                        self._sidste_batch_tid = nu
                else:
                    fejl_besked = f"Batteri fejl: {payload.get('batteri')}"
                    db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
            
            elif emne == TOPIC_VINDUE_STATUS:
                status = payload.get('status', 'ukendt')
                
                if status in ['aaben', 'lukket', 'ukendt']:
                    pos = valider_heltal(
                        payload.get('position', 0),
                        min_værdi=0,
                        max_værdi=4096
                    )
                    max_pos = valider_heltal(
                        payload.get('max_position', 0),
                        min_værdi=0,
                        max_værdi=4096
                    )
                    
                    ny_status = {
                        'status': status,
                        'position': pos if pos is not None else 0,
                        'max_position': max_pos if max_pos is not None else 0
                    }
                    data_opbevaring.opdater_vindue_status(ny_status)
                    
                    db.gem_sensor_data(
                        'esp32_vindue',
                        'Motor',
                        'position',
                        pos if pos is not None else 0
                    )
                    
                    print(f"Vindue status opdateret: {status} ({pos}/{max_pos})")
                    
                    self._notificer_frontend('vindue')
                else:
                    fejl_besked = f"Ugyldig vindue status: {status}"
                    db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
            
            elif emne == TOPIC_FEJLBESKED:
                besked_tekst = payload.get('fejl', '')
                kilde_enhed = payload.get('enhed', 'ukendt_enhed')
                
                if besked_tekst:
                    data_opbevaring.opdater_fejl({
                        'fejl': besked_tekst,
                        'kilde': kilde_enhed,
                        'tid': time.time()
                    })
                    
                    db.gem_fejl(kilde_enhed, 'ESP32', besked_tekst)
                    
                    print(f"Fejl modtaget fra {kilde_enhed}: {besked_tekst}")
                    
                    self._notificer_frontend('fejl')
        
        except json.JSONDecodeError as fejl:
            fejl_besked = f"Ugyldig JSON modtaget: {besked.payload}"
            db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
        
        except Exception as fejl:
            fejl_besked = f"Uventet fejl i message handler: {fejl}"
            db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
    
    def run(self) -> None:

        forsøg = 0
        
        print("MQTT klient thread startet")
        
        while self.kører and forsøg < MAX_FORBINDELSES_FORSØG:
            try:
                print(f"Forbinder til MQTT broker: {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
                
                self.klient.connect(
                    MQTT_BROKER_HOST,
                    MQTT_BROKER_PORT,
                    KEEPALIVE_INTERVAL
                )
                
                self.klient.loop_start()
                
                forsøg = 0
                
                print("MQTT forbindelse etableret")
                
                while self.kører:
                    if not self.forbundet:
                        print("MQTT forbindelse tabt - venter på reconnect")
                    time.sleep(2)
            
            except Exception as fejl:
                forsøg += 1
                fejl_besked = f"Kunne ikke forbinde (Forsøg {forsøg}/{MAX_FORBINDELSES_FORSØG}): {fejl}"
                db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
                
                if forsøg < MAX_FORBINDELSES_FORSØG:
                    print(f"Venter {GENFORSØGS_DELAY}sekunder før næste forsøg")
                    time.sleep(GENFORSØGS_DELAY)
        
        if forsøg >= MAX_FORBINDELSES_FORSØG:
            fejl_besked = f"Giver op efter {MAX_FORBINDELSES_FORSØG} fejlslagne forbindelsesforsøg"
            db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
    
    def publicer_kommando(self, kommando: str) -> None:

        if not self.forbundet:
            fejl_besked = "Kan ikke sende kommando: Ingen MQTT forbindelse"
            db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
            return
        
        try:
            payload = json.dumps({'kommando': kommando})
            
            print(f"Sender vindue kommando: {kommando}")
            info = self.klient.publish(
                TOPIC_VINDUE_KOMMANDO,
                payload,
                qos=1
            )
            
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise Exception(f"Publish fejlkode: {info.rc}")
            
            db.gem_system_log(
                ENHEDS_ID,
                'MQTT',
                f"Kommando sendt: {kommando}"
            )
            print(f"Kommando sendt succesfuldt: {kommando}")
        
        except Exception as fejl:
            fejl_besked = f"Fejl ved afsendelse af kommando '{kommando}': {fejl}"
            db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
    
    def stop(self) -> None:

        print("Stopper MQTT klient")
        
        self.kører = False
        
        self.klient.loop_stop()
        
        self.klient.disconnect()
        
        db.gem_system_log(ENHEDS_ID, 'MQTT', "MQTT klient stoppet")
        
        print("MQTT klient stoppet")



mqtt_klient: MQTTKlient = MQTTKlient()
