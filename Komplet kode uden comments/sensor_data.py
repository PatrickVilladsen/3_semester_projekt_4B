

import threading
from datetime import datetime
from typing import Set, Dict, Any, Optional, Union
from fastapi import WebSocket

class SensorData:

    
    def __init__(self) -> None:

        self.lås: threading.Lock = threading.Lock()
        
        self.sensor_data: Dict[str, Optional[Union[float, str]]] = {
            'temperatur': None,
            'luftfugtighed': None,
            'batteri': None,
            'målt_klokken': None
        }
        
        self.bme680_data: Dict[str, Optional[Union[float, str]]] = {
            'temperatur': None,
            'luftfugtighed': None,
            'gas': None,
            'målt_klokken': None
        }
        
        self.vindue_status: Dict[str, Union[str, int, None]] = {
            'status': 'ukendt',
            'position': 0,
            'max_position': 0,
            'målt_klokken': None
        }
        
        self.websocket_klienter: Set[WebSocket] = set()
        
    
    def opdater_sensor_data(
        self,
        nøgle: str,
        værdi: Optional[Union[float, int]]
    ) -> None:

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

        with self.lås:
            self.bme680_data['temperatur'] = round(temp, 1) if temp is not None else None
            self.bme680_data['luftfugtighed'] = round(fugt, 1) if fugt is not None else None
            self.bme680_data['gas'] = int(gas) if gas is not None else None
            
            self.bme680_data['målt_klokken'] = datetime.now().isoformat()
    
    def opdater_vindue_status(self, status_data: Dict[str, Any]) -> None:

        with self.lås:
            self.vindue_status.update(status_data)
            self.vindue_status['målt_klokken'] = datetime.now().isoformat()
    
    def opdater_fejl(self, fejl_data: Dict[str, Any]) -> None:

        with self.lås:
            pass

    def hent_alle_data(self) -> Dict[str, Dict[str, Any]]:

        with self.lås:
            return {
                'sensor': self.sensor_data.copy(),
                'bme680': self.bme680_data.copy(),
                'vindue': self.vindue_status.copy()
            }
    
    def tilføj_websocket_klient(self, klient: WebSocket) -> None:

        self.websocket_klienter.add(klient)
    
    def fjern_websocket_klient(self, klient: WebSocket) -> None:

        self.websocket_klienter.discard(klient)
    
    def hent_websocket_klienter(self) -> Set[WebSocket]:

        return self.websocket_klienter.copy()


data_opbevaring = SensorData()
