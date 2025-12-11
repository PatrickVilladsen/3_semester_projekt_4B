import threading
import time
import bme680
from sensor_data import data_opbevaring
from database import db
from climate_controller import climate_controller
from config import BME680_MAALINGS_INTERVAL
from typing import Optional 

'''
Vi opretter en klasse til sensoren som gør brug af pythons "Thread" for concurrency.
Det betyder også at alle objekter der laves ud fra klassen kører med threads.
Threads giver også mulighed for at styrer funktionaliteten med f.eks. .start()
og vores graceful shutdown fra main.py'''
class BME680Sensor(threading.Thread):

    '''
    Her bruger vi netop funktionalitet fra Thread -
    __init__ kalder konstruktøren fra "parent class" - som er (threading.Thread)
    super() sikrer at konstruktøren for "parent class" - threading.Thread kaldes først, før vi kører videre'''
    def __init__(self):
        '''daemon=True skaber en daemon-tråd som gør at vi kan lukke koden
        selvom at denne tråd stadig kører - det hjælper med vores graceful shutdown.
        name= gør at vi giver tråden et navn, så vi nemt kan finde fejl i error handling'''
        super().__init__(daemon=True, name="BME680-Sensor")
        # Vi sætter tråden til at køre - den sættes til False i main.py når programmet slukkes
        self.running = True
        # Placeholder for en instansvariabel - defineres i sensor_setup()
        self.sensor = None
        # Også en placeholder for en instansvariabel - defineres i main.py og bruges når data skal videre til app.py
        self._websocket_callback = None
        # Samme situation som  _websocket_callback
        self._mqtt_client = None  # Reference til MQTT client

    # Vi opretter en metode der giver os mulighed for at definere callback-adressen senere i main.py
    # Denne metode kaldes for en "Setter" og har som job at sætte værdien af en intern variabel.
    def set_websocket_callback(self, callback):
        self._websocket_callback = callback
    
    # Er ligeledes en Setter - for mere information omkring setter tjek dokumentation_info filen
    def set_mqtt_client(self, mqtt_client):
        self._mqtt_client = mqtt_client
    
    # Vi har nu metode der sætter vores sensor op med et forventet output med enten True eller False
    # Dette er for nemt at afgøre om det blev sat op korrekt eller om der opstod fejl.
    def sensor_setup(self) -> bool:
        addresses = [
            (bme680.I2C_ADDR_PRIMARY, "0x76 (Primary)"),
            (bme680.I2C_ADDR_SECONDARY, "0x77 (Secondary)")
        ]
        
        for addr, addr_name in addresses:
            try:
                self.sensor = bme680.BME680(addr)
                
                # Her sætter vi at vi vil "oversample" - det betyder at vi på en måling laver flere målinger
                # og bruger gennemsnittet af dem som vores værdi - ved fugt 2 målinger, ved tmperatur 8 målinger.
                self.sensor.set_humidity_oversample(bme680.OS_2X)
                self.sensor.set_temperature_oversample(bme680.OS_8X)
                # Her bruges et IIR-filter (infinite Impulse Response) - som bruges til at glatte vores data ud
                # Det tager højde for tidligere målinger og gør at vores "kurve" for datamålinger glattes ud og ikke har store spring
                self.sensor.set_filter(bme680.FILTER_SIZE_3)
                
                # Her opsættes gasmåleren på sensoren
                # Vi akriverer gasmåling
                self.sensor.set_gas_status(bme680.ENABLE_GAS_MEAS)
                # Hvor varm varmeelementet skal være i grader
                self.sensor.set_gas_heater_temperature(320)
                # Varmes i 150ms pr. målig for at undgå overophedning
                self.sensor.set_gas_heater_duration(150)
                # Hvis vi ville køre forskellige indstillinger kunne vi indstille flere profiler
                self.sensor.select_gas_heater_profile(0)
                
                # Retun med True da vi ønskede et Bool -svar
                return True
                
            except Exception:
                continue
        
        # Gem error log i db
        db.log_error('BME680', 'Sensor kunne ikke starte på I2C')
        return False
    
    # Her sker alt det sjove - her læser vi data og vi bruger callback
    def aflæs_sensor(self):
        try:
            #her læser vi temperatur og fugtighed fra sensoren
            if self.sensor.get_sensor_data():
                temp = self.sensor.data.temperature
                hum = self.sensor.data.humidity
                
                # Vi vil kun have data fra gas når varmeelementet er varmet op så vi får brugbar data
                if self.sensor.data.heat_stable:
                    gas = self.sensor.data.gas_resistance
                else:
                    gas = None
                
                # Opdaterer vores data_opbevaring med vores nye værdier (gas gemmes i ohm)
                data_opbevaring.update_bme680_data(temp, hum, gas)
                
                # Log til database (som før)
                db.log_sensor('BME680', 'temperature', temp)
                db.log_sensor('BME680', 'humidity', hum)
                if gas is not None:
                    db.log_sensor('BME680', 'gas', gas)
                
                # Vurder hvad der skal ske med de nye værdier - defineres lidt længere nede
                self._evaluate_climate(temp, hum, gas)
                
                # Her sender vi besked til vores websocket callback om at der er ny data at modtage
                if self._websocket_callback:
                    try:
                        self._websocket_callback('bme680')
                    except Exception as e:
                        db.log_error('BME680', f"WebSocket callback fejl: {e}")
        # Gem i db ved fejl
        except Exception as e:
            db.log_error('BME680', f"Sensor aflæsning fejl: {e}")
    
    # Den her metode samler alt data og beslutter hvad der skal gøres
    def _evaluate_climate(self, indoor_temp: float, indoor_humidity: float, indoor_gas: Optional[float]):
        
        # Tjekker om der er MQTT forbindelse, ellers er det spild at gøre mere
        if self._mqtt_client is None:
            return

        try:
            # Henter alt ude data fra dht11, alt inde data fra bme680 og nuværende vinduesstatus
            # Her hentes all_data dictionary'et
            all_data = data_opbevaring.get_all_data()
            outdoor_data = all_data.get('sensor', {})
            window_data = all_data.get('vindue', {})
            
            # her henter vi så data fra sensor dictionary'et fra all_data dictionary'et
            outdoor_temp = outdoor_data.get('temperature')
            outdoor_humidity = outdoor_data.get('humidity')
            window_status = window_data.get('status', 'ukendt')
            
            # Hvis vi mangler outdoor data sendes der fejl, da vi ikke kan lave en vurdering
            if outdoor_temp is None or outdoor_humidity is None:
                db.log_error('CLIMATE_CONTROLLER', "Manglende udedata. Klima evaluering afbrudt.")
                return
            
            '''
            Her modtager vi en kommando og et grundlag fra climate_controller.py baseret på
            den evaluering som den har foretaget af dataen.
            Vi sender så kommandoen ud på mqtt tråden, som gennem main.py bliver vidregivet til
            mqtt.py filen og behandles der'''
            command, reason = climate_controller.vurder_klima(
                indoor_temp=indoor_temp,
                indoor_humidity=indoor_humidity,
                indoor_gas=indoor_gas,
                outdoor_temp=outdoor_temp,
                outdoor_humidity=outdoor_humidity,
                window_status=window_status
            )
            
            # Sender kommandoen ud hvis den er modtaget
            if command:
                
                # Her publisher vi den nye kommando
                self._mqtt_client.publish_command(command)
                
                # Vi registrerer at vi netop har sendt den angivne kommando, så vi ikke sender den flere gange
                climate_controller.save_command(command)
                
                # Gemmes til db
                db.log_event('CLIMATE_CONTROLLER', f"Kommando sendt: {command} - {reason}")
        
        except Exception as e:
            # errorlog til DB
            db.log_error('BME680', f"Evalueringsfejl: {e}")

    #Her er funktionen som kører målingerne efter vores tidsintervaller
    def run(self):
        if not self.sensor_setup():
            return
        
        while self.running:
            try:
                self.aflæs_sensor()
                time.sleep(BME680_MAALINGS_INTERVAL)
                
            # ved fejl 
            except Exception as e:
                db.log_error('BME680', f"Aflæsningsfejl: {e}")
                time.sleep(5)
    
    # Slukker for sensoren når der stoppes
    def stop(self):
        self.running = False