"""
MQTT Klient modul til IoT-"Edge Gateway" funktionalitet på vores RPi5.

Dette modul fungerer som broen mellem de "dumme" enheder (ESP32) og
den "kloge" server (RPi5). Den oversætter letvægts MQTT-beskeder til
strukturerede database-rækker.

Hovedansvarsområder:
    1. Ingestion: Modtager rå data fra ESP32 enheder via MQTT
    2. Validering: Sikrer at data er indenfor tilladte grænser
    3. Persistens: Gemmer straks data i SQLite (via database.py) for sikkerhed
    4. Live View: Opdaterer intern hukommelse (data_opbevaring) til WebSockets
    5. Batching: Grupperer målinger for at opnå mindre trafik

Protokol:
    - Broker: Mosquitto på localhost (192.168.4.1)
    - QoS 1 (At Least Once): Vi accepterer dubletter, men aldrig datatab
    - Topics: sensor/temperatur, sensor/luftfugtighed, sensor/batteri,
              vindue/status, vindue/kommando, fejlbesked

Arkitektur:
    ESP32 (MQTT Pub) -> [MQTT Broker] -> [MQTT Klient (Sub)]
    -> Validering -> Database + Intern hukommelse

Fejlhåndtering:
    - Netværk: Automatisk reconnection med backoff
    - Data: Ugyldige målinger (f.eks. temp 200°C) fejlhåndteres og logges
    - JSON: Korrupte pakker fanges uden at crashe tråden

Threading:
    Vi kører som daemon thread for at sikre non-blocking funktionalitet.
    Hovedprogrammet kan fortsætte mens vi håndterer MQTT data i baggrunden.

Note:
    Dette modul kræver en kørende Mosquitto broker på MQTT_BROKER_HOST.
    Start broker med: sudo systemctl start mosquitto
"""

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


# Konfiguration af regex patterns

FLOAT_VALIDERING: Pattern[str] = re.compile(r'^-?\d+(\.\d+)?$')
"""
Regex pattern til validering af float værdier.

Tillader:
    - Positive tal: "42", "3.14"
    - Negative tal: "-10", "-273.15"
    - Decimaler: "0.5", "100.0"

Afviser:
    - Bogstaver: "abc", "12a"
    - Uægte tal: "12.3.4"
"""

POSITIV_VALIDERING: Pattern[str] = re.compile(r'^\d+$')
"""
Regex pattern til validering af positive heltal.

Bruges til procenter (0-100) og positioner.

Tillader:
    - Positive heltal: "0", "42", "100"

Afviser:
    - Negative: "-5"
    - Decimaler: "12.5"
    - Bogstaver: "abc"
"""

# Konfiguration af ESP32 sensor ID

ESP32_SENSOR_ID = 'esp32_sensor'
"""
Hardkodet ID for vores udendørs ESP32 sensor (DHT11).

I en større løsning ville ESP32 sende sit ID med i alle pakker.
P.t. har vi kun én udesensor så vi hardcoder dens ID her.

Note:
    Dette skal matche ENHEDS_ID fra ESPsensor.py
"""

# Konfiguration af batching

BATCH_TIMEOUT = 2.0
"""
Maximum ventetid i sekunder før en ufuldstændig batch sendes.

Vi venter på at få temp, fugt og bat før vi opdaterer frontend,
men hvis det tager over 2 sekunder sender vi det vi har.
Dette gør at vi ikke blokerer opdateringer for evigt hvis én
sensor fejler.
"""

# Konfiguration af reconnection

MAX_FORBINDELSES_FORSØG = 5
"""
Maximum antal forbindelsesforsøg før vi giver op.

Efter 5 fejlede forsøg antages det at broker er nede og
systemet logger fejlen.
"""

GENFORSØGS_DELAY = 5
"""Sekunder at vente mellem forbindelsesforsøg."""

KEEPALIVE_INTERVAL = 60
"""
Sekunder mellem keepalive ping til broker.

Hvis broker ikke hører fra os i 1.5 * KEEPALIVE_INTERVAL (90 sekunder)
antager den at forbindelsen er død og lukker den.
"""

# Validerings funktioner

def valider_værdi(
    værdi: Any,
    min_værdi: Optional[float] = None,
    max_værdi: Optional[float] = None
) -> Optional[float]:
    """
    Konverterer og validerer en sensor måling.
    
    Hvis en sensor går i stykker, kan den finde på at sende ekstremværdier
    (f.eks. -999 eller NaN). Denne funktion filtrerer støj fra så vi
    ikke gemmer urealistiske værdier.
    
    Args:
        værdi: Rå værdi fra MQTT payload (kan være str, int, float)
        min_værdi: Minimum tilladt værdi. None = ingen nedre grænse
        max_værdi: Maksimum tilladt værdi. None = ingen øvre grænse
    
    Returns:
        Valideret float værdi, eller None hvis ugyldigt
    
    Validation Steps:
        1. Konverter til string og trim whitespace
        2. Tjek format med regex (tillader negative og decimaler)
        3. Konverter til float
        4. Tjek min/max grænser
    
    Eksempler:
        valider_værdi("23.5", -40, 85) -> 23.5
        valider_værdi(" 12.3 ", 0, 100) -> 12.3  # Trimmer whitespace
        valider_værdi("150", 0, 100) -> None     # Over max
        valider_værdi("abc", 0, 100) -> None     # Ikke et tal
        valider_værdi("-273.15", -300, 100) -> -273.15  # Negative OK
    
    Note:
        Returnerer None ved fejl i stedet for at raise exception.
        Dette gør det lettere at håndtere ugyldige data fra ESP32 ved at
        springe individuelle målinger over uden at afvise hele payload'en.
    """
    if værdi is None:
        return None
    
    # Rens for mellemrum og konverter til string
    værdi_str = str(værdi).strip()
    
    # Tjek format med regex
    if not FLOAT_VALIDERING.match(værdi_str):
        return None
    
    try:
        # Konverter til float
        nummer = float(værdi_str)
        
        # Tjek grænser
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
    """
    Validering specifikt til procenter (0-100) og heltal.
    
    Bruges til fugtighed, batteri og motor positioner hvor
    vi kun accepterer hele tal uden decimaler.
    
    Args:
        værdi: Rå værdi fra MQTT payload
        min_værdi: Minimum tilladt værdi (standard: 0)
        max_værdi: Maksimum tilladt værdi (standard: 100)
    
    Returns:
        Valideret int-værdi, eller None hvis ugyldigt
    
    Validation Steps:
        1. Konverter til string og trim whitespace
        2. Tjek format med regex (kun positive heltal)
        3. Konverter til int
        4. Tjek min/max grænser
    
    Eksempler:
        valider_heltal("85", 0, 100) -> 85
        valider_heltal(" 42 ", 0, 100) -> 42  # Trimmer whitespace
        valider_heltal("150", 0, 100) -> None  # Over max
        valider_heltal("12.5", 0, 100) -> None  # Decimaler ikke tilladt
        valider_heltal("-5", 0, 100) -> None   # Negative ikke tilladt
    
    Note:
        Default range 0-100 passer til de fleste use cases i vores tilfælde:
        - Fugtighed: 0-100%
        - Batteri: 0-100%
        - Motor position bruger custom grænser
    """
    if værdi is None:
        return None
    
    # Rens og konverter til string
    værdi_str = str(værdi).strip()
    
    # Tjek format med regex (kun positive heltal)
    if not POSITIV_VALIDERING.match(værdi_str):
        return None
    
    try:
        # Konverter til integer
        nummer = int(værdi_str)
        
        # Tjek grænser
        if nummer < min_værdi or nummer > max_værdi:
            return None
        
        return nummer
        
    except (ValueError, TypeError):
        return None


# MQTT Klient klasse

class MQTTKlient(threading.Thread):
    """
    Trådbaseret MQTT subscriber.
    
    Vi kører uafhængigt af hovedprogrammet for at sikre at vi aldrig
    misser en besked, selvom RPi'en har travlt med andet (f.eks. database
    writes eller WebSocket broadcasts).
    
    Threading Model:
        - Daemon thread: Lukker automatisk når main program stopper
        - Non-blocking: Hovedprogrammet kan fortsætte mens vi kører MQTT
        - Thread-safe: Paho's loop_start() håndterer concurrency internt
    
    Lifecycle:
        1. __init__(): Konfigurer callbacks og state
        2. start(): Start tråden (kalder run())
        3. run(): Forbind til broker og kør event loop
        4. on_connect(): Subscribe til topics når forbindelse etableres
        5. on_message(): Håndter indkommende beskeder
        6. stop(): Luk forbindelse og stop tråd
    
    State Management:
        - self.forbundet: Tracker om vi har aktiv broker forbindelse
        - self.kører: Er et "Flag" der kontrollerer vores main loop (tilstand)
        - self._sensor_batch: Tracker hvilke målinger vi mangler før update
    
    Batching Strategy:
        ESP32 sender temp, fugt og bat som 3 separate beskeder.
        Vi venter på alle 3 inden vi opdaterer frontend for at reducere
        WebSocket traffic. Hvis det tager over BATCH_TIMEOUT sender vi
        det vi har.
    
    Note:
        Paho MQTT bruger sin egen baggrundstråd (loop_start()) til
        netværk I/O. Vores Thread wrapper (MQTTKLient-kassen) holder styr på
        reconnection og selve "livscyklusen".
    """
    
    def __init__(self) -> None:
        """
        Initialiserer vores MQTT klient og konfigurerer callbacks.
        
        Opsætning:
            - Daemon thread: Lukker automatisk med main program
            - Paho callbacks: on_connect og on_message
            - State flags: forbundet, kører
            - Batching state: Tracker hvilke målinger vi har modtaget
        
        Note:
            Vi forbinder ikke til broker endnu - det sker i run().
            Dette tillader opbygning uden at blokere.
        """
        super().__init__(daemon=True, name="MQTT-Klient")
        
        # Paho MQTT client opsætning
        self.klient: mqtt.Client = mqtt.Client()
        self.klient.on_connect = self.on_connect
        self.klient.on_message = self.on_message
        
        # Tilstands-flags
        self.forbundet: bool = False
        self.kører: bool = True
        
        # Callback til WebSockets (sættes fra main.py)
        self._websocket_callback: Optional[Callable[[str], None]] = None
        """
        Er en privat instansvariabel, der opbevarer funktionen, som vi kalder,
        når ny data skal sendes videre til WebSockets.
        """
        
        # Batching state: Tracker hvilke sensor målinger vi har modtaget
        self._sensor_batch: Dict[str, bool] = {
            'temp': False,
            'fugt': False,
            'bat': False
        }
        """Er en privat instansvariabel der opretter em tjekliste for vores batching"""

        self._sidste_batch_tid: float = 0
        """Er en privat instansvariabel der fungerer som vores "stopur" i forhold til batching"""
        
        # Debug
        print("MQTT klient initialiseret")
    
    def sæt_websocket_callback(self, callback: Callable[[str], None]) -> None:
        """
        Opretter en funktion der binder vores backend og frontend sammen med callback.
        
        Denne callback kaldes når vi har nye data klar til frontend.
        Den håndterer asynkron kommunikation mellem vores MQTT tråd og
        WebSocket event loop'en.
        
        Args:
            callback: Async funktion der tager opdaterings_type ('sensor', 'vindue', 'fejl')
        
        Eksempel:
            async def broadcast_opdatering(opdaterings_type: str):
                await websocket_manager.broadcast(opdaterings_type)
            
            mqtt_klient.sæt_websocket_callback(broadcast_opdatering)
        
        Note:
            Kaldes fra main.py efter vores WebSocket manager er startet op
            Callback skal være async da den skal indsættet i asyncio event loop.
        """
        self._websocket_callback = callback
        print("WebSocket callback konfigureret")
    
    def on_connect(
        self,
        klient: mqtt.Client,
        brugerdata: Any,    # Påtvunget fra paho
        flags: Dict[str, Any],  # Påtvunget fra paho
        returkode: int
    ) -> None:
        """
        Callback der kaldes automatisk når vi får forbindelse til broker.
        
        Denne funktion kaldes af Paho når forbindelsen er etableret.
        Vi bruger den til at subscribe til alle vores relevante topics.
        
        Args:
            klient: MQTT client instance
            brugerdata: User-defined data (bruges ikke)
            flags: Connection flags fra broker (bruges ikke)
            returkode: Connection result code (0 = success)
        
        Return Codes:
            0: Connection successful
            1: Connection refused - incorrect protocol version
            2: Connection refused - invalid client identifier
            3: Connection refused - server unavailable
            4: Connection refused - bad username or password
            5: Connection refused - not authorized
        
        Subscription Strategi:
            Vi subscriber kun når vi ved at forbindelsen er godkendt (returkode 0).
            Dette genskaber også vores subscriptions automatisk ved reconnect, da
            on_connect kaldes hver gang forbindelsen etableres.
        
        QoS Level:
            QoS 1 = "At Least Once" - Vi vil være sikre på at modtage beskeden,
            selv hvis det betyder vi får dubletter. Dubletter håndteres i
            on_message ved at tjekke timestamps.
        
        Note:
            Paho håndterer reconnection automatisk, så denne funktion
            kan kaldes flere gange i løbet af programmets levetid.
        """
        if returkode == 0:
            # Forbindelse successful
            self.forbundet = True
            
            # Subscribe til alle vores relevante topics med QoS 1
            klient.subscribe(TOPIC_SENSOR_TEMP, qos=1)
            klient.subscribe(TOPIC_SENSOR_FUGT, qos=1)
            klient.subscribe(TOPIC_SENSOR_BAT, qos=1)
            klient.subscribe(TOPIC_VINDUE_STATUS, qos=1)
            klient.subscribe(TOPIC_FEJLBESKED, qos=1)
            
            # Debug og database log
            print(f"MQTT forbundet til broker: {MQTT_BROKER_HOST}")
            db.gem_system_log(
                ENHEDS_ID,
                'MQTT',
                f"Forbundet til broker: {MQTT_BROKER_HOST}"
            )
        else:
            # Forbindelse fejlede
            self.forbundet = False
            print(f"MQTT forbindelse afvist, kode: {returkode}")
            db.gem_fejl(
                ENHEDS_ID,
                'MQTT',
                f"Forbindelse afvist, kode: {returkode}"
            )
    
    def _notificer_frontend(self, opdaterings_type: str) -> None:
        """
        Sender besked videre til WebSockets asynkront.
        
        Denne private hjælpefunktion håndterer den svære del: at planlægge og
        tilføje en async task i asyncio event loop'en fra en synkonroniseret thread.
        
        Args:
            opdaterings_type: Type af opdatering ('sensor', 'vindue', 'fejl')
        
        Implementerings detaljer:
            1. Hent den kørende asyncio event loop
            2. Tjek om loop'en stadig kører
            3. Opret en task i loop'en med vores callback
        
        Error Handling:
            Fejl logges til konsol men crasher ikke vores MQTT tråd.
            Dette sikrer at en fejl i WebSocket ikke stopper
            modtagelse af data.
        
        Note:
            Vi logger ikke til database her for at undgå log-spamming
            ved hver opdatering.
            Da denne funktion er privat bruges der et "_" - dette 
            giver besked på, at denne funktion kun skal bruges internt i denne klasse.
        """
        if self._websocket_callback:
            try:
                import asyncio
                
                # Find den kørende event loop
                loop = asyncio.get_event_loop()
                
                # Tjek om loop stadig kører
                if loop.is_running():
                    # Tilføj task i loop'en
                    asyncio.create_task(
                        self._websocket_callback(opdaterings_type)
                    )
            except Exception as fejl:
                # Debug
                print(f"WebSocket notify fejl: {fejl}")
    
    def on_message(
        self,
        klient: mqtt.Client,    # Påtvunget af paho
        brugerdata: Any,    # Påtvunget af paho
        besked: mqtt.MQTTMessage
    ) -> None:
        """
        Hjertet i systemet: Modtager, validerer og gemmer data.
        
        Denne callback kaldes hver gang vi modtager en MQTT besked.
        Den håndterer validering, database writes og frontend updates.
        
        Args:
            klient: MQTT client instance (bruges ikke)
            brugerdata: User-defined data (bruges ikke)
            besked: MQTT message object med topic og payload
        
        Data Flow:
            1. Parse JSON payload
            2. Valider værdier baseret på topic/data type
            3. Opdater intern hukommelse for hurtig frontend adgang
            4. Gem til lokal database for historik og lagring
            5. Track batching state
            6. Send frontend update når batch er klar
        
        Error Handling:
            - JSON decode fejl: Log og skip besked
            - Validation fejl: Log ugyldig værdi og gem fejl til DB
            - Database fejl: Fanges i db.gem_(...) funktioner
            - Andre fejl: Log og fortsæt med næste besked
        
        Performance:
            Køres i MQTT's egen tråd så blokerende operationer
            (f.eks. database writes) ikke påvirker mulgiheden for at modtage andre beskeder.
        
        Note:
            Alle målinger gemmes både til den interne hukommelse og databasen.
            Intern hukommelse bruges til live view, database til lagring, historik og grafer.
        """
        try:
            # Parse besked
            emne = besked.topic
            payload_str = besked.payload.decode()
            payload = json.loads(payload_str)
            
            # Debug
            print(f"MQTT modtaget på {emne}: {payload}")
            
            # Håndtering af temperatur
            if emne == TOPIC_SENSOR_TEMP:
                # Payload forventes: {"temperatur": 22.5}
                # DHT11 range: 0 til 40°C (Dansk klima kan gå i minusgrader)
                værdi = valider_værdi(
                    payload.get('temperatur'),
                    min_værdi=-25,
                    max_værdi=40
                )
                
                if værdi is not None:
                    # 1. Opdater intern hukommelse
                    data_opbevaring.opdater_sensor_data('temperatur', værdi)
                    
                    # 2. Gem til databse
                    db.gem_sensor_data(
                        ESP32_SENSOR_ID,
                        'DHT11',
                        'temperatur',
                        værdi
                    )
                    
                    # 3. Batch tracking
                    self._sensor_batch['temp'] = True
                    self._sidste_batch_tid = time.time()
                    
                    # Debug
                    print(f"Temperatur gemt: {værdi}°C")
                else:
                    # Ugyldig værdi - log fejl
                    fejl_besked = f"Ugyldig temperatur: {payload.get('temperatur')}"
                    db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
            
            # Håndtering af luftfugtighed
            elif emne == TOPIC_SENSOR_FUGT:
                # Payload forventes: {"luftfugtighed": 60}
                # DHT11 range: 20-90% RH (men vi accepterer 0-100%)
                værdi = valider_heltal(
                    payload.get('luftfugtighed'),
                    min_værdi=0,
                    max_værdi=100
                )
                
                if værdi is not None:
                    # 1. Opdater intern hukommelse
                    data_opbevaring.opdater_sensor_data('luftfugtighed', værdi)
                    
                    # 2. Gem til database
                    db.gem_sensor_data(
                        ESP32_SENSOR_ID,
                        'DHT11',
                        'luftfugtighed',
                        værdi
                    )
                    
                    # 3. Batch tracking
                    self._sensor_batch['fugt'] = True
                    self._sidste_batch_tid = time.time()
                    
                    # Debug
                    print(f"Luftfugtighed gemt: {værdi}%")
                else:
                    fejl_besked = f"Ugyldig fugtighed: {payload.get('luftfugtighed')}"
                    db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
            
            # Håndtering af batteri
            elif emne == TOPIC_SENSOR_BAT:
                # Payload forventes: {"batteri": 85}
                # Range: 0-100% (Burde ikke kunne modtage 0, da ESP32 så ville slukke)
                værdi = valider_heltal(
                    payload.get('batteri'),
                    min_værdi=0,
                    max_værdi=100
                )
                
                if værdi is not None:
                    # 1. Opdater intern hukommelse
                    data_opbevaring.opdater_sensor_data('batteri', værdi)
                    
                    # 2. Gem til databasen
                    db.gem_sensor_data(
                        ESP32_SENSOR_ID,
                        'Power',
                        'batteri',
                        værdi
                    )
                    
                    # 3. Batch tracking
                    self._sensor_batch['bat'] = True
                    
                    # Debug
                    print(f"Batteri gemt: {værdi}%")
                    
                    # 4. Tjek om batch er komplet eller timed out
                    nu = time.time()
                    alt_modtaget = all(self._sensor_batch.values())
                    timeout = (nu - self._sidste_batch_tid > BATCH_TIMEOUT)
                    
                    if alt_modtaget or timeout:
                        # Batch klar - send opdatering til frontend
                        print("Sensor batch komplet - opdaterer frontend")
                        self._notificer_frontend('sensor')
                        
                        # Nulstil batch state
                        self._sensor_batch = {k: False for k in self._sensor_batch}
                        self._sidste_batch_tid = nu
                else:
                    fejl_besked = f"Batteri fejl: {payload.get('batteri')}"
                    db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
            
            # Håndtering af status fra vinduet
            elif emne == TOPIC_VINDUE_STATUS:
                # Payload forventes: {
                #     "status": "aaben",
                #     "position": 50,
                #     "max_position": 4096
                # }
                status = payload.get('status', 'ukendt')
                
                # Valider status string
                if status in ['aaben', 'lukket', 'ukendt']:
                    # Valider positioner
                    pos = valider_heltal(
                        payload.get('position', 0),
                        min_værdi=0,
                        max_værdi=4096  # Øvre grænse for motor steps i 28byj-48
                    )
                    max_pos = valider_heltal(
                        payload.get('max_position', 0),
                        min_værdi=0,
                        max_værdi=4096
                    )
                    
                    # Opdater intern hukommelse med komplet status
                    ny_status = {
                        'status': status,
                        'position': pos if pos is not None else 0,
                        'max_position': max_pos if max_pos is not None else 0
                    }
                    data_opbevaring.opdater_vindue_status(ny_status)
                    
                    # Gem opdatering til databasen
                    db.gem_sensor_data(
                        'esp32_vindue',
                        'Motor',
                        'position',
                        pos if pos is not None else 0
                    )
                    
                    # Debug
                    print(f"Vindue status opdateret: {status} ({pos}/{max_pos})")
                    
                    # Vindues-opdateringer haster mere end sensorer
                    # Send straks til frontend (uden batching)
                    self._notificer_frontend('vindue')
                else:
                    fejl_besked = f"Ugyldig vindue status: {status}"
                    db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
            
            # Håndtering af fejlbeskeder
            elif emne == TOPIC_FEJLBESKED:
                # Payload forventes: {
                #     "fejl": "DHT11 læsning fejlede",
                #     "enhed": "esp32_sensor"
                # }
                besked_tekst = payload.get('fejl', '')
                kilde_enhed = payload.get('enhed', 'ukendt_enhed')
                
                if besked_tekst:
                    # Opdater intern hukommelse med fejl information
                    data_opbevaring.opdater_fejl({
                        'fejl': besked_tekst,
                        'kilde': kilde_enhed,
                        'tid': time.time()
                    })
                    
                    # Gem fejl til database
                    # Vi logger fejlen under den enhed der oplevede den
                    db.gem_fejl(kilde_enhed, 'ESP32', besked_tekst)
                    
                    # Debug
                    print(f"Fejl modtaget fra {kilde_enhed}: {besked_tekst}")
                    
                    # Send straks til frontend
                    self._notificer_frontend('fejl')
        
        except json.JSONDecodeError as fejl:
            # Korrupt JSON payload
            fejl_besked = f"Ugyldig JSON modtaget: {besked.payload}"
            db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
        
        except Exception as fejl:
            # Uventet fejl
            fejl_besked = f"Uventet fejl i message handler: {fejl}"
            db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
    
    def run(self) -> None:
        """
        Hovedløkken for vores MQTT tråd.
        
        Sørger for at holde forbindelsen i live og genoprette den hvis den ryger.
        Kører indtil self.kører sættes til False eller MAX_FORBINDELSES_FORSØG nås.
        
        Reconnection strategi:
            1. Forsøg at forbinde til broker
            2. Ved fejl: Log fejl og vent GENFORSØGS_DELAY sekunder
            3. Inkrement forsøgstæller
            4. Gentag indtil MAX_FORBINDELSES_FORSØG eller success
            5. Ved success: Nulstil forsøgstæller
        
        Monitoring Loop:
            Efter successfuld forbindelse køres en overvågningsløkke der:
            - Tjekker self.kører flag hvert 2. sekund
            - Tillader graceful shutdown via stop() metoden
        
        Paho Loop:
            loop_start() starter Paho's egen baggrundstråd som håndterer:
            - Netværk I/O
            - Keepalive pings
            - Automatisk reconnection
            - Message callbacks
        
        Note:
            Denne funktion blokerer ikke main program da vi
            kører som daemon thread.
        """
        forsøg = 0
        
        # Debug
        print("MQTT klient thread startet")
        
        while self.kører and forsøg < MAX_FORBINDELSES_FORSØG:
            try:
                # Forsøg at forbinde til broker
                # debug
                print(f"Forbinder til MQTT broker: {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
                
                self.klient.connect(
                    MQTT_BROKER_HOST,
                    MQTT_BROKER_PORT,
                    KEEPALIVE_INTERVAL
                )
                
                # Start Paho's baggrundstråd
                self.klient.loop_start()
                
                # Reset forsøgstæller ved succes
                forsøg = 0
                
                print("MQTT forbindelse etableret")
                
                # Overvågningsløkke - kører indtil stop() kaldes
                while self.kører:
                    if not self.forbundet:
                        # Paho prøver selv reconnect
                        # Vi logger bare status
                        print("MQTT forbindelse tabt - venter på reconnect")
                    time.sleep(2)
            
            except Exception as fejl:
                # Forbindelse fejlede
                forsøg += 1
                fejl_besked = f"Kunne ikke forbinde (Forsøg {forsøg}/{MAX_FORBINDELSES_FORSØG}): {fejl}"
                db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
                
                # Vent før næste forsøg
                if forsøg < MAX_FORBINDELSES_FORSØG:
                    print(f"Venter {GENFORSØGS_DELAY}sekunder før næste forsøg")
                    time.sleep(GENFORSØGS_DELAY)
        
        # Check om vi ramte forsøgsgrænsen
        if forsøg >= MAX_FORBINDELSES_FORSØG:
            fejl_besked = f"Giver op efter {MAX_FORBINDELSES_FORSØG} fejlslagne forbindelsesforsøg"
            db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
    
    def publicer_kommando(self, kommando: str) -> None:
        """
        Sender kommando til vores ESP32 ved vinduet.
        
        Bruges til at kontrollere vinduet fra frontend via MQTT.
        Kommandoen sendes til TOPIC_VINDUE_KOMMANDO som vores ESP32 ved vinduet
        lytter på.
        
        Args:
            kommando: Kommando string
        
        Kommandoer:
            - 'aaben': Åbn vinduet helt
            - 'luk': Luk vinduet helt
            - 'kort_aaben': 5 minutters udluftning derefter auto-luk
            - 'manuel_aaben': Manuel åbning (1/5 af max ad gangen)
            - 'manuel_luk': Manuel lukning (1/5 af max ad gangen)
        
        Error Handling:
            - Tjekker forbindelse før afsendelse
            - Logger fejl hvis publish fejler
            - Returnerer uden at raise exception
        
        Payload Format:
            {"kommando": "aaben"}
        
        QoS:
            QoS 1 = At Least Once. Vi vil være sikre på at motoren
            modtager kommandoen, dubletter håndteres af motor controller.
        
        Note:
            Kaldes fra WebSocket endpoint når bruger trykker på knap i UI.
        """
        # Tjek om vi har forbindelse
        if not self.forbundet:
            fejl_besked = "Kan ikke sende kommando: Ingen MQTT forbindelse"
            db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
            return
        
        try:
            # Opbyg JSON payload
            payload = json.dumps({'kommando': kommando})
            
            # Publicer til topic
            print(f"Sender vindue kommando: {kommando}")
            info = self.klient.publish(
                TOPIC_VINDUE_KOMMANDO,
                payload,
                qos=1
            )
            
            # Tjek om publish lykkedes
            # info.rc er returkode (0 = success)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise Exception(f"Publish fejlkode: {info.rc}")
            
            # Log success
            db.gem_system_log(
                ENHEDS_ID,
                'MQTT',
                f"Kommando sendt: {kommando}"
            )
            print(f"Kommando sendt succesfuldt: {kommando}")
        
        except Exception as fejl:
            # Publish fejlede
            fejl_besked = f"Fejl ved afsendelse af kommando '{kommando}': {fejl}"
            db.gem_fejl(ENHEDS_ID, 'MQTT', fejl_besked)
    
    def stop(self) -> None:
        """
        Lukker gracefully og stopper vores tråd.
        
        Denne funktion kaldes når programmet lukker ned eller når
        vi manuelt vil stoppe MQTT klienten.
        
        Shutdown Sekvens:
            1. Sæt self.kører til False (stopper run() loop)
            2. Stop Paho's baggrundstråd
            3. Disconnect fra broker
            4. Log shutdown event
        
        Note:
            Dette er et "blocking call" - den venter på at tråden
            faktisk stopper. I praksis tager det op til 2 sekunder pga.
            sleep(2) i vores overvågningsløkke.
        """
        # debug
        print("Stopper MQTT klient")
        
        # Stop run() loop
        self.kører = False
        
        # Stop Paho's baggrundstråd
        self.klient.loop_stop()
        
        # Disconnect fra broker
        self.klient.disconnect()
        
        # Log shutdown
        db.gem_system_log(ENHEDS_ID, 'MQTT', "MQTT klient stoppet")
        
        # debug
        print("MQTT klient stoppet")


# Global instans

mqtt_klient: MQTTKlient = MQTTKlient()
"""
Global singleton instance af vores MQTT klient.
Det betyder at alle kører igennem den "samme" instans af denne kode
Det sikrer thread-safety da klienten, skaber en kø-kultur
til vores MQTTKlient klasse, så alle kan tilgå den, men kun en af gangen.

Importeres og bruges i main.py:
    from mqtt_client import mqtt_klient
    
    mqtt_klient.start()
    mqtt_klient.sæt_websocket_callback(broadcast_opdatering)
    mqtt_klient.publicer_kommando('aaben')
    mqtt_klient.stop()

Note:
    Initialiseres ved import men starter ikke før .start() kaldes.
    Dette tillader konfiguration (callbacks) før thread starter.
"""