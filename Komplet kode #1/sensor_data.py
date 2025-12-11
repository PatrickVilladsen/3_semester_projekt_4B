import threading
from datetime import datetime
from typing import Set
from fastapi import WebSocket

'''
Denne fil har som ansvar at opbevare alt vores data og fungerer altså som vores "RAM" til resten af systemet
Det er her at alle de andre filer henter dataen fra.
Da der er flere tråde som kommer med informationer samtidigt benytter vi her thread safety.
Informationer om thread safety står mere detaljeret beskrevet inde vores vores dokumentations fil'''

#Klasse til opbevaring af alt vores data med thread-safety
class SensorData:
    
    '''
    Her initierer vi med threading.lock som netop er vores thread safety
    Dette er en Mutex låsemekanisme
    Vi starter med at opsætte vores dictionary med tomme værdier så der kan blive fyldt på
    fra sensorne'''
    def __init__(self):
        self.lock = threading.Lock()
        
        # Sensor data fra ESP32 udenfor - det bliver udfyldt fra mqtt
        self.sensor_data = {
            'temperature': None,
            'humidity': None,
            'battery': None,
            'timestamp': None
        }
        
        # BME680 data fra Raspberry Pi - udfyldes lokalt
        self.bme680_data = {
            'temperature': None,
            'humidity': None,
            'gas': None,
            'timestamp': None
        }
        
        # Vindue-status fra ESP32 ved vinduet - udfyldet fra mqtt
        self.vindue_status = {
            'status': 'ukendt',
            'position': 0,
            'max_position': 0,
            'timestamp': None
        }
        
        '''
        Her holder vi stur på antallet af aktive websocket klienter.
        Vi bruger Set som er lidt som en liste, men har den fordel at hver browser
        får en unik HASH-værdi i et hash-table.
        Det gør at en brower ikke bliver tilføjet 2 gange i samme "liste" ved en fejl
        og derfor modtager besked 2 gange'''
        self.websocket_clients: Set[WebSocket] = set()
    
    '''
    Her opdaterer vi data i vores sensor_data dictionary
    Vi bruger self.lock så det er kun er en der kan rode i data'en ad gangen.
    ved at bruge "key" giver vi muligheden for at der kan ændres i en værdi ad gangen
    og at der så kun bliver skabt et timestamp for den værdi - det er relevant 
    hvis nu at mqtt kun fik dht11 data med fra ESP32'eren, så ville dens værdi blive
    opdateret med et timestamp, hvor der så stadig ville være et gammelt timestamp
    ved batteriet.'''
    def update_sensor_data(self, key: str, value):
        with self.lock:
            self.sensor_data[key] = value
            self.sensor_data['timestamp'] = datetime.now().isoformat()
    
    '''
    Her gør vi det så uden key, da vi ikke skal igennem mqtt her og ved at hvis sensoren
    virker så får vi alt data sammen.'''
    def update_bme680_data(self, temp: float, hum: float, gas: float):
        with self.lock:
            self.bme680_data['temperature'] = round(temp, 1) if temp else None
            self.bme680_data['humidity'] = round(hum, 1) if hum else None
            self.bme680_data['gas'] = round(gas, 0) if gas else None
            self.bme680_data['timestamp'] = datetime.now().isoformat()
    
    # Her er det så selve status på vinduet vi opdaterer
    def update_vindue_status(self, status_data: dict):
        with self.lock:
            self.vindue_status.update(status_data)
            self.vindue_status['timestamp'] = datetime.now().isoformat()

    def update_error(self, error_data: dict):
        with self.lock:
            # Du kan vælge at gemme errors i en liste hvis du vil vise dem i frontend
            # For nu logger vi bare til database
            pass           
    
    '''Her samler vi så alt dataen ned i en samlet dictionary så vi kan sende det
    hele ud i en samlet pakke til de filer der skal bruge det'''
    def get_all_data(self) -> dict:
        """Hent al data (thread-safe)"""
        with self.lock:
            return {
                'sensor': self.sensor_data.copy(),
                'bme680': self.bme680_data.copy(),
                'vindue': self.vindue_status.copy()
            }
    
    # Her tilføjes nye browsere til vores set vi oprettede før
    def add_websocket_client(self, client: WebSocket):
        self.websocket_clients.add(client)
    
    # Her fjernes de så igen når de disconneter
    def remove_websocket_client(self, client: WebSocket):
        self.websocket_clients.discard(client)
    
    # Her henter vi set-listen over alle de websockets der lytter lige nu, det skal bruges i app.py
    def get_websocket_clients(self) -> Set[WebSocket]:
        return self.websocket_clients.copy()

'''
Global Singleton Instance, der sikrer at alle har fat i de samme informationer
Dette gøres ved at rette vores "RAM" med det nye data der kommer'''
data_opbevaring = SensorData()