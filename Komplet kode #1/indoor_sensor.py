"""
BME680 Indendørs Sensor modul til automatisk vindues-styring.

Dette modul implementerer:
- Kontinuerlig luftkvalitetsmåling via BME680 sensor
- Intelligent klima evaluering og vindueskontrol
- Real-time WebSocket notifications til frontend
- Thread-safe sensor aflæsning med fejlhåndtering
- Automatisk MQTT kommando-sending baseret på indeklima

Hardware:
    - Raspberry Pi 5
    - Bosch BME680 Environmental Sensor
    - I2C Bus forbindelse (Adresse: 0x76 eller 0x77)
    
BME680 Specifikationer:
    - Temperatur: -40°C til 85°C (±1°C nøjagtighed)
    - Luftfugtighed: 0-100% RH (±3% nøjagtighed)
    - Gas: VOC måling via metal-oxide heater (320°C)
    - I2C Interface

Arkitektur:
    Kører som en separat daemon-tråd for at sikre, at sensormålinger
    ikke blokerer hovedprogrammet eller netværkskommunikation.
    Integrerer direkte med climate_controller for at træffe beslutninger
    om vinduesstyring baseret på målingerne.

Data Flow:
    BME680 (I2C) -> Sensor læsning -> Data validering -> Data opbevaring
    -> Database logging -> WebSocket notification
    -> Klima evaluering (-> MQTT kommando)

Note:
    Kræver python3-smbus (hardware oversætter fra C til Python) og bme680 biblioteker.
    Sensor kræver ca. 48 timers "burn-in" for præcise gas-målinger.
    For mere præcis data, ville der skulle benyttes Bosch's IAQ værdier
    som stammer fra deres BSEC libary - var dog bøvl med at få det til at virke
    på RPi5 pga. 64-bit OS frem for 32-bit
"""

import threading
import time
import asyncio
from typing import Optional, Callable, Tuple, Any
import bme680

from sensor_data import data_opbevaring
from database import db
from climate_controller import klima_controller
from config import BME680_MÅLINGS_INTERVAL, ENHEDS_ID

class BME680Sensor(threading.Thread):
    """
    Threaded driver til Bosch BME680 environmental sensor.
    
    Denne klasse implementerer et kontinuerlig sensor læsnings-loop i en
    separat daemon tråd med automatisk klima evaluering.
    
    Hardware Konfiguration:
        Oversampling (højere værdi = mere præcis, længere måletid):
        - Temperatur: OS_8X (8 målinger per sample)
        - Luftfugtighed: OS_2X (2 målinger per sample)
        
        IIR Filter:
        - Size 3: Reducerer støj ved at vægte tidligere målinger med i output.
        
        Gas Heater:
        - 320°C i 150ms for optimal VOC detection.
    
    Livscyklus:
        1. __init__(): Opsætning af state
        2. start(): Starter tråden (kalder run())
        3. run(): Initialiserer hardware og starter loop
        4. stop(): Graceful shutdown
    
    Attributes:
        kører (bool): Kontrollerer hoved-loopet.
        sensor (bme680.BME680): Reference til hardware objektet.
    """
    
    def __init__(self) -> None:
        """
        Initialiserer BME680 sensor tråden.
        
        Opsætning:
            - Daemon thread: Lukker automatisk med hovedprogrammet.
            - Navn: "BME680-Sensor" for nemmere debugging.
            - State flags: kører = True.
        
        Dependencies:
            Callbacks til WebSocket og MQTT oprettes via setters
            for at undgå cirkulære imports og hard coupling.
        """
        super().__init__(daemon=True, name="BME680-Sensor")
        
        # Thread kontrol
        self.kører: bool = True
        
        # Sensor instans (oprettes i run -> opsæt_sensor)
        self.sensor: Optional[bme680.BME680] = None
        
        # Dependencies (indsættes senere)
        self._websocket_callback: Optional[Callable[[str], None]] = None
        self._mqtt_klient: Optional[Any] = None
        
        # Debug
        print("BME680 sensor tråd initialiseret")
    
    def sæt_websocket_callback(
        self,
        callback: Callable[[str], None]
    ) -> None:
        """
        Registrerer callback til WebSocket frontend notifikationer.
        
        Bruges til at give besked til main.py om at der er nye
        sensor data klar til at blive sendt til frontend.
        
        Args:
            callback: Async funktion der tager opdaterings_type (str)
        """
        self._websocket_callback = callback
    
    def sæt_mqtt_klient(self, mqtt_klient: Any) -> None:
        """
        Registrerer MQTT klient reference.
        
        Nødvendig for at sensoren kan sende kommandoer (f.eks. 'aaben')
        til vinduet, hvis indeklimaet bliver for dårligt.
        
        Args:
            mqtt_klient: Instans af MQTTKlient
        """
        self._mqtt_klient = mqtt_klient
    
    def opsæt_sensor(self) -> bool:
        """
        Initialiserer BME680 hardware via I2C.
        
        Forsøger automatisk at forbinde på både primær (0x76) og
        sekundær (0x77) I2C-adresse.
        
        Konfiguration:
            - Humidity Oversampling: 2X
            - Temperature Oversampling: 8X
            - Filter Size: 3
            - Gas Heater: 320°C, 150ms
            
        Returns:
            True hvis sensor blev fundet og konfigureret, ellers False.
        
        Logs:
            Skriver til system_log ved success, fejl_log ved failure.
        """
        # I2C adresser at forsøge
        adresser = [
            (bme680.I2C_ADDR_PRIMARY, "0x76 (Primær)"),
            (bme680.I2C_ADDR_SECONDARY, "0x77 (Sekundær)")
        ]
        
        for adresse, navn in adresser:
            try:
                print(f"Forsøger BME680 på {navn}...")
                self.sensor = bme680.BME680(adresse)
                
                # Konfigurer oversampling for præcision
                self.sensor.set_humidity_oversample(bme680.OS_2X)
                self.sensor.set_temperature_oversample(bme680.OS_8X)
                
                # IIR Filter til støjreduktion
                self.sensor.set_filter(bme680.FILTER_SIZE_3)
                
                # Konfigurer Gas Sensor (VOC)
                self.sensor.set_gas_status(bme680.ENABLE_GAS_MEAS)
                self.sensor.set_gas_heater_temperature(320)
                self.sensor.set_gas_heater_duration(150)
                self.sensor.select_gas_heater_profile(0)
                
                # Log success
                besked = f"BME680 initialiseret succesfuldt på {navn}"
                print(besked)
                db.gem_system_log(ENHEDS_ID, 'BME680', besked)
                return True
                
            except (OSError, RuntimeError):
                # Prøv næste adresse
                continue
            except Exception as fejl:
                db.gem_fejl(ENHEDS_ID, 'BME680', f"Init fejl på {navn}: {fejl}")
                continue
        
        # Hvis vi når her, fejlede begge adresser
        db.gem_fejl(ENHEDS_ID, 'BME680', "Kunne ikke finde BME680 sensor på I2C")
        return False
    
    def aflæs_sensor(self) -> None:
        """
        Udfører én komplet måle-cyklus.
        
        Flow:
            1. Trigger hardware måling (get_sensor_data)
            2. Læs temperatur og fugtighed
            3. Læs gas modstand (hvis heater er stabil)
            4. Gem data i hukommelse og database
            5. Evaluer indeklima (åbn/luk vindue)
            6. Notificer frontend
            
        Fejlhåndtering:
            Fanger og logger fejl uden at crashe tråden.
            Ved ustabil heater springes kun gas-målingen over.
        """
        try:
            # Trigger måling - returnerer True ved success
            if self.sensor.get_sensor_data():
                
                # Hent basismålinger
                temp = self.sensor.data.temperature
                fugt = self.sensor.data.humidity
                
                # Hent gas (kun hvis heater nåede måletemperatur)
                gas = None
                if self.sensor.data.heat_stable:
                    gas = self.sensor.data.gas_resistance
                
                # 1. Opdater intern hukommelse (til frontend)
                data_opbevaring.opdater_bme680_data(temp, fugt, gas)
                
                # 2. Gem til database
                db.gem_sensor_data(ENHEDS_ID, 'BME680', 'temperatur', temp)
                db.gem_sensor_data(ENHEDS_ID, 'BME680', 'luftfugtighed', fugt)
                
                if gas is not None:
                    db.gem_sensor_data(ENHEDS_ID, 'BME680', 'gas', gas)
                
                # 3. Evaluer indeklima og styr vindue
                self._vurder_klima(temp, fugt, gas)
                
                # 4. Notificer frontend via WebSocket
                if self._websocket_callback:
                    self._notificer_frontend()
                    
        except Exception as fejl:
            # Log fejl men fortsæt tråden
            db.gem_fejl(ENHEDS_ID, 'BME680', f"Aflæsningsfejl: {str(fejl)}")

    def _notificer_frontend(self) -> None:
        """
        Privat hjælpefunktion til at sende besked til WebSockets.
        
        Håndterer broen mellem denne tråd og asyncio event loopet.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self._websocket_callback('bme680'))
        except Exception as e:
            print(f"Kunne ikke notificere frontend: {e}")

    def _vurder_klima(
        self,
        indendørs_temp: float,
        indendørs_fugt: float,
        indendørs_gas: Optional[float]
    ) -> None:
        """
        Evaluerer om vinduet skal justeres baseret på målinger.
        
        Bruger climate_controller til at sammenligne indendørs data
        med udendørs data (fra ESP32) og træffe en beslutning.
        
        Args:
            indendørs_temp: Temperatur i °C
            indendørs_fugt: Luftfugtighed i %
            indendørs_gas: Gas modstand i Ohm (kan være None)
            
        Logic:
            1. Henter udendørs data fra data_opbevaring
            2. Beregner optimal handling (f.eks. 'aaben' hvis CO2 er høj)
            3. Sender MQTT kommando hvis nødvendigt
            4. Respekterer manuel override og cooldowns
        """
        # Kræver at vi har en MQTT forbindelse for at kunne handle
        if self._mqtt_klient is None:
            return

        try:
            # Hent nødvendig kontekst-data
            alle_data = data_opbevaring.hent_alle_data()
            ude_data = alle_data.get('sensor', {})
            vindue_data = alle_data.get('vindue', {})
            
            # Træk værdier ud
            ude_temp = ude_data.get('temperatur')
            ude_fugt = ude_data.get('luftfugtighed')
            vindue_status = vindue_data.get('status', 'ukendt')
            
            # Bed controlleren om en vurdering
            kommando, grund = klima_controller.vurder_klima(
                inden_temp=indendørs_temp,
                inden_fugt=indendørs_fugt,
                inden_gas=indendørs_gas,
                ude_temp=ude_temp,
                ude_fugt=ude_fugt,
                vindue_status=vindue_status
            )
            
            # Hvis controlleren anbefaler en handling
            if kommando:
                # Send kommandoen via MQTT
                # debug
                print(f"Auto-styring: Sender '{kommando}' (Grund: {grund})")
                self._mqtt_klient.publicer_kommando(kommando)
                
                # Registrer at vi har handlet (for cooldown timers)
                klima_controller.gem_kommando(kommando)
                
                # Log handlingen
                db.gem_system_log(
                    ENHEDS_ID, 
                    'ClimateCtrl', 
                    f"Auto-handling: {kommando} ({grund})"
                )
                
        except Exception as fejl:
            db.gem_fejl(ENHEDS_ID, 'ClimateCtrl', f"Fejl i logik: {fejl}")

    def run(self) -> None:
        """
        Hovedløkken for sensortråden.
        
        Livscyklus:
            1. Initialiser hardware (opsæt_sensor)
            2. Loop uendeligt (mens self.kører er True)
            3. Aflæs sensor
            4. Sov i BME680_MÅLINGS_INTERVAL sekunder
        
        Recovery:
            Ved uventet fejl i loopet ventes 5 sekunder før genforsøg.
        """
        # Forsøg hardware setup
        if not self.opsæt_sensor():
            print("BME680 Hardware setup fejlede - tråd stopper")
            return
            
        print(f"BME680 måling startet (Interval: {BME680_MÅLINGS_INTERVAL}s)")
        
        while self.kører:
            try:
                # Udfør arbejde
                self.aflæs_sensor()
                
                # Vent til næste måling
                time.sleep(BME680_MÅLINGS_INTERVAL)
                
            except Exception as fejl:
                # Uventet fejl i loopet
                besked = f"Uventet fejl i sensor loop: {fejl}"
                print(besked)
                db.gem_fejl(ENHEDS_ID, 'BME680', besked)
                
                # Vent lidt længere før retry ved fejl
                time.sleep(5)

    def stop(self) -> None:
        """
        Stopper tråden gracefully.
        
        Sætter kører "flaget" til False, hvilket får run loopet til 
        at afslutte efter nuværende sleep-periode.
        """
        print("Stopper BME680 sensor")
        self.kører = False
        db.gem_system_log(ENHEDS_ID, 'BME680', "Sensor tråd stoppet")

# Global instans til brug i main.py - Ikke en oprigtig Singleton instans
bme680_sensor = BME680Sensor()