from datetime import datetime
from typing import Optional, Tuple
from config import THRESHOLDS

# Vores vinduesstyring som er baseret på data fra udeklima, samt data fra indeklimaet
# Vi opretter en klasse til vores vinduesstyring
class ClimateController:
    
    # Opsætter vi vores "indstillinger" som skal bruges senere til at vurderer vinduesstyringen
    def __init__(self):
        # Data kan findes inde på config.py
        self.temp_low = THRESHOLDS['temp']['limit_low']
        self.temp_high = THRESHOLDS['temp']['limit_high']
        self.temp_max = THRESHOLDS['temp']['max']
        
        self.hum_low = THRESHOLDS['humidity']['limit_low']
        self.hum_high = THRESHOLDS['humidity']['limit_high']
        self.hum_max = THRESHOLDS['humidity']['max']
        
        self.gas_low = THRESHOLDS['gas']['limit_line']     
        self.gas_too_low = THRESHOLDS['gas']['min']             
        
        # Grænser til hvornår vi ikke ønsker vinduet åbent for længe
        self.outdoor_temp_low = 10     
        self.outdoor_humidity_high = 85 
        
        # Vi holder styr på vores kort_aaben kommando så der er en cooldown, så der ikke er konstant åbent
        self.last_command_time: Optional[datetime] = None
        self.last_command: Optional[str] = None
        self.kort_aaben_cooldown = 15 * 60
        self.normal_cooldown = 30 * 60
    
    # Her definerer vi kom der kan sendes en ny besked til ESP32'eren ved vinduet om at åbne eller lukke vinduet
    def _kan_sende_command(self) -> bool:
        #Tjek om der kan sendes en ny kommando baseret på cooldown
        if self.last_command_time is None:
            return True
        # Nu skal vi se hvor lang tid der er gået siden vi sidst sendte en besked
        tid_gaaet = (datetime.now() - self.last_command_time).total_seconds()
        # Vi tjekker hvilken kommando der har har været tale om
        if self.last_command == 'kort_aaben':
            return tid_gaaet >= self.kort_aaben_cooldown
        return tid_gaaet >= self.normal_cooldown
    
    # Vi definerer funktionen for når vejret er skidt udenfor
    def _udenfor_skidt_tjek(self, outdoor_temp: float, outdoor_humidity: float) -> bool:
        return outdoor_temp < self.outdoor_temp_low or outdoor_humidity > self.outdoor_humidity_high
    
    # Vi skal her vurderer indeklimaet og udeklimaet for at vurderer hvad der skal ske
    # Vi starter med at hente værdierne
    def vurder_klima(
        self,
        indoor_temp: float,
        indoor_humidity: float,
        indoor_gas: Optional[float],
        outdoor_temp: float,
        outdoor_humidity: float,
        window_status: str
    ) -> Tuple[Optional[str], Optional[str]]:
        
        # Hvis vi ikke har modtaget data, sætter vi værdi til none, det betyder at der ikke gøres noget
        if None in [indoor_temp, indoor_humidity, outdoor_temp, outdoor_humidity]:
            return None, None
        # Hvis det ikke må sendes kommando returnerer vi none, none og der sker altså ikke noget
        if not self._kan_sende_command():
            return None, None
        
        # Nu skal vi opsætte vores vurderingssystem
        # Her definerer vi hvad for varm og for fugtigt er
        # for varmt er når indetemperaturen er over det vi definerer som varmt
        too_hot = indoor_temp > self.temp_high
        # for fugtigt er når luftfugtigheden er over det som vi definerer som for fugtigt
        too_humid = indoor_humidity > self.hum_high
        
        # Her tjekker vi så luftkvaliteten
        very_bad_air = indoor_gas is not None and indoor_gas < self.gas_too_low
        bad_air = indoor_gas is not None and indoor_gas < self.gas_low
        
        # definerer at bare en af de 3 værdier skal være sande for at vi har dårlig luftkvaliet
        bad_air_quality = too_hot or too_humid or bad_air
        
        # Her er logikken for om vinduet skal ændres
        if window_status == 'aaben':
            
            # Vi ønsker en temperatur der er højere end low, men lavere end high 
            temp_ok = self.temp_low <= indoor_temp <= self.temp_high
            # Samme princip her
            hum_ok = self.hum_low <= indoor_humidity <= self.hum_high
            # Og her handler det bare om at vi ikke vil ramme vores lavere grænse
            gas_ok = indoor_gas is None or indoor_gas > self.gas_low
            
            # Hvis vores indeklima er perfekt kan vi lukke vinduet
            if temp_ok and hum_ok and gas_ok:
                # Sendes tilbage til der den blev kaldt fra
                return 'luk', "Indeklima optimalt"
            
            # Hvis vores temperatur bliver for koldt indenfor lukker vi vinduet
            if indoor_temp < self.temp_low:
                return 'luk', f"Lukker pga kulde - {indoor_temp}°C"

            # Hvis ingen af disse rammer, så kan vi godt lade vinduet så åbent for nu
            return None, None
            
        # Hvis vinduet er lukket og indeklimaet er fint gøres der ikke noget
        if not bad_air_quality:
            return None, None
            
        # Her tjekekr vi om udeklimaet kan løse indeklimaet
        
        outdoor_temp_better = too_hot and (outdoor_temp < indoor_temp)
        outdoor_hum_better = too_humid and (outdoor_humidity < indoor_humidity)
        # luften er i vores tilfælde som udgangspunkt altid bedre - vi har ikke nogen VOC eller co2 måler udenfor
        outdoor_gas = bad_air 
        
        # Hvis ingen af dem er bedre end indeklimaet gør vi ikke noget
        if not (outdoor_temp_better or outdoor_hum_better or outdoor_gas):
            return None, None

        
        # Hvis luften er meget dårlig skal vi have åbnet vinduet
        if very_bad_air:
            return 'aaben', f"Meget dårligt indeklima: {int(indoor_gas)}Ω - Skal lufte ud"

        # Her definerer vi grunde til at skulle åbne vinduet
        reasons = []
        # Hvis det er for varmt og udendørs temperatur er bedre
        if too_hot and outdoor_temp_better: reasons.append(f"Temp høj")
        # Hvis luftfugtigheden er for høj og den er bedre udenfor
        if too_humid and outdoor_hum_better: reasons.append(f"Fugt høj")
        # Hvis der er for meget gas i rummet
        if bad_air: reasons.append(f"Luft dårlig")
        
        # her tilføjes alle grundene til en samlet string
        reason_str = ", ".join(reasons)
        
        # Her tager vi og forsikrer at vejret ikke er for dårligt udenfor til at det giver mening at lufte ud
        if self._udenfor_skidt_tjek(outdoor_temp, outdoor_humidity):
            return 'kort_aaben', f"Kort udluftning - vejret er dårligt udenfor: {reason_str}"
        else:
            return 'aaben', f"Åbner vindue: {reason_str}"
        #Vi gemmer oplysninger for hver gang vi sender en kommando
    def save_command(self, command: str):
        self.last_command = command
        self.last_command_time = datetime.now()

# infinity loop
climate_controller = ClimateController()