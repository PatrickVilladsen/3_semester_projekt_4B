"""
Central Data Storage modul til thread-safe sensor data håndtering.

Dette modul fungerer som vores interne hukommelse. Det er en centraliseret, 
in-memory dataopbevaring, hvor alle systemets tråde kan læse og skrive 
sensor data sikkert uden risiko for datakorruption.

Arkitektur:
    - Singleton Pattern: Én global instans (data_opbevaring) bruges af alle.
    - Mutex Locks: threading.Lock() beskytter delt data mod samtidige ændringer.
    - Defensive Copying: Returnerer kopieringer af data for at undgå utilsigtede ændringer udefra.
    - WebSocket Tracking: Holder styr på aktive klienter via et Set.
    - In-Memory Only: Ingen disk I/O her - Intern hukommelse.

Begrundelse for valget af Thread safety
    Når flere tråde (MQTT, BME680, WebServer) forsøger at ændre den samme variabel
    samtidigt, kan der opstå "Race Conditions".
    
    Eksempel uden lås:
    1. Tråd A læser værdi (20)
    2. Tråd B læser værdi (20)
    3. Tråd A skriver ny værdi (21)
    4. Tråd B skriver ny værdi (25) -> Tråd A's ændring ville gå tabt

    Løsning (Mutex Lock):
    Vi låser døren ind til dataen, så kun en tråd kan ændre den ad gangen.

Data Flow:
    1. Inputs: 
       - ESP32 (via MQTT) -> opdater_sensor_data
       - BME680 (via I2C tråd) -> opdater_bme680_data
       - ESP32 (via MQTT) -> opdater_vindue_status
    
    2. Storage:
       - Gemmes midlertidigt i den interne hukommelse (dictionaries)
    
    3. Outputs:
       - Frontend (via WebSocket) <- hent_alle_data
       - Remote server (via sync_client) <- hent_alle_data

Note:
    Dette modul håndterer ikke permanent lagring. Hvis strømmen går,
    forsvinder data herfra. langtidslagring håndteres af database.py.
"""

import threading
from datetime import datetime
from typing import Set, Dict, Any, Optional, Union
from fastapi import WebSocket

class SensorData:
    """
    Thread-safe in-memory storage for sensor data og WebSocket klienter.
    
    Attributter:
        lås (threading.Lock): Mutex lås til beskyttelse mod race conditions.
        sensor_data (Dict): Data fra udendørs ESP32.
        bme680_data (Dict): Data fra indendørs BME680.
        vindue_status (Dict): Status fra ESP32 ved vinduet.
        websocket_klienter (Set): Aktive frontend forbindelser.
    """
    
    def __init__(self) -> None:
        """
        Initialiserer datastrukturerne med tomme værdier (None).
        
        Vi bruger "None" som startværdi for at kunne kende forskel på 
        om data har værdien "0" eller om og vi ikke har modtaget data endnu.
        """
        # Opret mutex lock for thread safety
        self.lås: threading.Lock = threading.Lock()
        
        # ESP32 udendørs sensor data (fra MQTT)
        self.sensor_data: Dict[str, Optional[Union[float, str]]] = {
            'temperatur': None,      # °C
            'luftfugtighed': None,   # %
            'batteri': None,         # %
            'målt_klokken': None     # ISO 8601 string
        }
        
        # BME680 indendørs sensor data
        self.bme680_data: Dict[str, Optional[Union[float, str]]] = {
            'temperatur': None,      # °C
            'luftfugtighed': None,   # %
            'gas': None,             # kOhm (luftkvalitet)
            'målt_klokken': None     # ISO 8601 string
        }
        
        # ESP32 vindues kontrol status (fra MQTT)
        self.vindue_status: Dict[str, Union[str, int, None]] = {
            'status': 'ukendt',      # 'aaben', 'lukket', 'ukendt'
            'position': 0,           # Nuværende step position
            'max_position': 0,       # Max steps
            'målt_klokken': None     # ISO 8601 string
        }
        
        # WebSocket klient tracking
        # Vi bruger et Set da det automatisk håndterer unikke forbindelser
        self.websocket_klienter: Set[WebSocket] = set()
        
    
    def opdater_sensor_data(
        self,
        nøgle: str,
        værdi: Optional[Union[float, int]]
    ) -> None:
        """
        Opdaterer en enkelt værdi for udendørs sensoren (ESP32).
        
        Bruges af MQTT-klienten, som ofte modtager temperatur, fugt og batteri
        i separate beskeder.
        
        Args:
            nøgle: Navnet på feltet (f.eks. 'temperatur', 'batteri')
            værdi: Den målte værdi
            
        Thread Safety:
            Bruger "with self.lås" for at sikre en samlet opdatering.
            Altså at Tråd B ikke kan hente data før Tråd A er færdig
            med at at skrive.
        """
        with self.lås:
            if nøgle in self.sensor_data:
                self.sensor_data[nøgle] = værdi
                self.sensor_data['målt_klokken'] = datetime.now().isoformat()

    def opdater_bme680_data(
        self,
        temp: Optional[float],
        fugt: Optional[float],
        gas: Optional[float]
    ) -> None:
        """
        Opdaterer alle BME680 data på én gang.
        
        Da BME680 sensoren læses i en samlet batch, opdaterer vi hele
        strukturen samlet.
        
        Afrunding:
            - Temperatur/Fugt: 1 decimal (fx 22.5)
            - Gas: Heltal (fx 45000)
            
        Args:
            temp: Temperatur i °C
            fugt: Luftfugtighed i %
            gas: Gasmodstand i Ohm
        """
        with self.lås:
            # Vi bruger ternary operator til kun at runde hvis værdien ikke er None
            self.bme680_data['temperatur'] = round(temp, 1) if temp is not None else None
            self.bme680_data['luftfugtighed'] = round(fugt, 1) if fugt is not None else None
            self.bme680_data['gas'] = int(gas) if gas is not None else None
            
            self.bme680_data['målt_klokken'] = datetime.now().isoformat()
    
    def opdater_vindue_status(self, status_data: Dict[str, Any]) -> None:
        """
        Opdaterer status for vinduet.
        
        "Merger" de nye data ind i den eksisterende struktur. Dette tillader
        partial updates (f.eks. hvis MQTT kun sender position men ikke status).
        
        Args:
            status_data: Dictionary med de felter der skal opdateres.
        """
        with self.lås:
            self.vindue_status.update(status_data)
            self.vindue_status['målt_klokken'] = datetime.now().isoformat()
    
    def opdater_fejl(self, fejl_data: Dict[str, Any]) -> None:
        """
        Placeholder metode til fejl-tracking i hukommelsen.
        
        Lige nu logges fejl direkte til databasen via database.py, 
        men denne metode beholdes for kompatibilitet og fremtidige udvidelser
        Hvis vi fjerner den uden at gå hele koden igennem vil der opstå crashes
        """
        with self.lås:
            pass

    def hent_alle_data(self) -> Dict[str, Dict[str, Any]]:
        """
        Henter et øjebliksbillede af al systemdata.
        
        Returnerer en .copy() af dataene ("Defensive Copying").
        Dette sikrer, at hvis modtageren ændrer i de modtagne data,
        bliver originalen i data_opbevaring ikke påvirket.
        
        Returns:
            Et dictionary indeholdende kopier af 'sensor', 'bme680' og 'vindue' data.
        """
        with self.lås:
            return {
                'sensor': self.sensor_data.copy(),
                'bme680': self.bme680_data.copy(),
                'vindue': self.vindue_status.copy()
            }
    
    def tilføj_websocket_klient(self, klient: WebSocket) -> None:
        """
        Registrerer en ny aktiv WebSocket-forbindelse.
        
        Args:
            klient: FastAPI WebSocket objektet.
        """
        self.websocket_klienter.add(klient)
    
    def fjern_websocket_klient(self, klient: WebSocket) -> None:
        """
        Fjerner en WebSocket-forbindelse fra listen.
        
        Bruger "discard" i stedet for "remove", så den ikke crasher
        hvis klienten allerede er væk.
        """
        self.websocket_klienter.discard(klient)
    
    def hent_websocket_klienter(self) -> Set[WebSocket]:
        """
        Returnerer en kopi af listen over aktive klienter.
        
        Vi returnerer en kopi, så man kan gennemgå listen (f.eks. til at sende nye beskeder)
        uden at risikere "RuntimeError: Set changed size during iteration", hvis
        en klient forbinder/afbryder imens.
        """
        return self.websocket_klienter.copy()


# Global Singleton Instans
data_opbevaring = SensorData()
"""
Global singleton instans af vores interne hukommelse over vores sensor data.

Denne instans fungerer som hele systemets RAM. 
Når den importeres i andre moduler (f.eks. indoor_sensor eller climate_controller), 
garanteres det, at de alle tilgår og manipulerer det præcis samme sæt data.

Vigtighed for Data Integritet:
    Da alle læse- og skriveoperationer i denne instans er beskyttet af 
    interne Mutex låse, sikrer den delte adgang, at alle dataændringer sker 
    "samlet" (atomisk). Dette eliminerer risikoen for Race Conditions, 
    hvor forskellige tråde ser inkonsistente data.

Eksempel på import:
    from sensor_data import data_opbevaring
    
    current_data = data_opbevaring.hent_alle_data()
    data_opbevaring.opdater_vindue_status({'status': 'aaben'})
"""