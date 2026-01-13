

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

    
    def __init__(self) -> None:

        super().__init__(daemon=True, name="BME680-Sensor")
        
        self.kører: bool = True
        
        self.sensor: Optional[bme680.BME680] = None
        
        self._websocket_callback: Optional[Callable[[str], None]] = None
        self._mqtt_klient: Optional[Any] = None
        
        print("BME680 sensor tråd initialiseret")
    
    def sæt_websocket_callback(
        self,
        callback: Callable[[str], None]
    ) -> None:

        self._websocket_callback = callback
    
    def sæt_mqtt_klient(self, mqtt_klient: Any) -> None:

        self._mqtt_klient = mqtt_klient
    
    def opsæt_sensor(self) -> bool:

        adresser = [
            (bme680.I2C_ADDR_PRIMARY, "0x76 (Primær)"),
            (bme680.I2C_ADDR_SECONDARY, "0x77 (Sekundær)")
        ]
        
        for adresse, navn in adresser:
            try:
                print(f"Forsøger at forbinde til BME680 på {navn}")
                self.sensor = bme680.BME680(adresse)
                
                self.sensor.set_humidity_oversample(bme680.OS_2X)
                self.sensor.set_temperature_oversample(bme680.OS_8X)
                
                self.sensor.set_filter(bme680.FILTER_SIZE_3)
                
                self.sensor.set_gas_status(bme680.ENABLE_GAS_MEAS)
                self.sensor.set_gas_heater_temperature(320)
                self.sensor.set_gas_heater_duration(150)
                self.sensor.select_gas_heater_profile(0)
                
                besked = f"BME680 initialiseret succesfuldt på {navn}"
                print(besked)
                db.gem_system_log(ENHEDS_ID, 'BME680', besked)
                return True
                
            except (OSError, RuntimeError):
                continue
            except Exception as fejl:
                db.gem_fejl(ENHEDS_ID, 'BME680', f"Init fejl på {navn}: {fejl}")
                continue
        
        db.gem_fejl(ENHEDS_ID, 'BME680', "Kunne ikke finde BME680 sensor på I2C")
        return False
    
    def aflæs_sensor(self) -> None:

        try:
            if self.sensor.get_sensor_data():
                
                temp = self.sensor.data.temperature
                fugt = self.sensor.data.humidity
                
                gas = None
                if self.sensor.data.heat_stable:
                    gas = self.sensor.data.gas_resistance
                
                data_opbevaring.opdater_bme680_data(temp, fugt, gas)
                
                db.gem_sensor_data(ENHEDS_ID, 'BME680', 'temperatur', temp)
                db.gem_sensor_data(ENHEDS_ID, 'BME680', 'luftfugtighed', fugt)
                
                if gas is not None:
                    db.gem_sensor_data(ENHEDS_ID, 'BME680', 'gas', gas)
                
                self._vurder_klima(temp, fugt, gas)
                
                if self._websocket_callback:
                    self._notificer_frontend()
                    
        except Exception as fejl:
            db.gem_fejl(ENHEDS_ID, 'BME680', f"Aflæsningsfejl: {str(fejl)}")

    def _notificer_frontend(self) -> None:

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

        if self._mqtt_klient is None:
            return

        try:
            alle_data = data_opbevaring.hent_alle_data()
            ude_data = alle_data.get('sensor', {})
            vindue_data = alle_data.get('vindue', {})
            
            ude_temp = ude_data.get('temperatur')
            ude_fugt = ude_data.get('luftfugtighed')
            vindue_status = vindue_data.get('status', 'ukendt')
            
            kommando, grund = klima_controller.vurder_klima(
                inden_temp=indendørs_temp,
                inden_fugt=indendørs_fugt,
                inden_gas=indendørs_gas,
                ude_temp=ude_temp,
                ude_fugt=ude_fugt,
                vindue_status=vindue_status
            )
            
            if kommando:
                print(f"Auto-styring: Sender '{kommando}' (Grund: {grund})")
                self._mqtt_klient.publicer_kommando(kommando)
                
                klima_controller.gem_kommando(kommando)
                
                db.gem_system_log(
                    ENHEDS_ID, 
                    'ClimateCtrl', 
                    f"Auto-handling: {kommando} ({grund})"
                )
                
        except Exception as fejl:
            db.gem_fejl(ENHEDS_ID, 'ClimateCtrl', f"Fejl i logik: {fejl}")

    def run(self) -> None:

        if not self.opsæt_sensor():
            print("BME680 Hardware setup fejlede - tråd stopper")
            return
            
        print(f"BME680 måling startet (Interval: {BME680_MÅLINGS_INTERVAL}s)")
        
        while self.kører:
            try:
                self.aflæs_sensor()
                
                time.sleep(BME680_MÅLINGS_INTERVAL)
                
            except Exception as fejl:
                besked = f"Uventet fejl i sensor loop: {fejl}"
                print(besked)
                db.gem_fejl(ENHEDS_ID, 'BME680', besked)
                
                time.sleep(5)

    def stop(self) -> None:

        print("Stopper BME680 sensor")
        self.kører = False
        db.gem_system_log(ENHEDS_ID, 'BME680', "Sensor tråd stoppet")

bme680_sensor = BME680Sensor()