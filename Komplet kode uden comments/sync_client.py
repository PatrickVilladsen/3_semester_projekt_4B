

import threading
import time
import requests
from typing import Dict, Any, List

from config import (
    REMOTE_SERVER_URL,
    BEARER_TOKEN,
    SYNKRONISERINGS_INTERVAL,
    ENHEDS_ID
)
from database import db


class SyncKlient(threading.Thread):

    
    def __init__(self) -> None:

        super().__init__(daemon=True, name="SyncKlient")
        
        self.kører: bool = True
        
        self.forsøg_tæller: int = 0
        
        print("Sync klient initialiseret")
    
    def run(self) -> None:

        db.gem_system_log(
            ENHEDS_ID,
            'SyncClient',
            f"Sync tråd startet. Interval: {SYNKRONISERINGS_INTERVAL}s"
        )
        
        print(f"Sync klient startet - Server: {REMOTE_SERVER_URL}")
        
        time.sleep(30)
        
        while self.kører:
            try:
                self.sync_data()
                
            except Exception as fejl:
                db.gem_fejl(ENHEDS_ID, 'SyncClient', f"Uventet fejl i main-loop: {fejl}")
                print(f"Sync klient fejl: {fejl}")
            
            if self.forsøg_tæller > 0:
                eksponent = min(self.forsøg_tæller, 3)
                
                ny_tid = SYNKRONISERINGS_INTERVAL * (2 ** eksponent)
                
                vent_tid = min(2400, ny_tid)
                
                print(f"Backoff aktiv: Venter {vent_tid}s (Forsøg {self.forsøg_tæller})")
            else:
                vent_tid = SYNKRONISERINGS_INTERVAL
            
            time.sleep(vent_tid)
    
    def sync_data(self) -> None:

        
        data = db.hent_usynkroniseret_data()
        
        har_sensor_data = bool(data['sensor_data'])
        har_fejl = bool(data['fejl_logs'])
        har_logs = bool(data['system_logs'])
        
        if not (har_sensor_data or har_fejl or har_logs):
            return
        
        try:
            db.ryd_gammel_data(dage=7)
        except Exception as fejl:
            db.gem_fejl(ENHEDS_ID, 'SyncClient', f"Oprydning fejlede: {fejl}")
        
        payload = {
            'enheds_id': ENHEDS_ID,
            'sensor_data': data['sensor_data'],
            'fejl_logs': data['fejl_logs'],
            'system_logs': data['system_logs']
        }
        
        antal_rækker = len(data['sensor_data']) + len(data['fejl_logs']) + len(data['system_logs'])
        
        print(f"Forsøger upload af {antal_rækker} rækker")
        
        try:
            respons = requests.post(
                REMOTE_SERVER_URL,
                json=payload,
                headers={
                    'Authorization': f'Bearer {BEARER_TOKEN}',
                    'Content-Type': 'application/json'
                },
                timeout=30
            )
            
            if respons.status_code == 200:
                
                sensor_ids = [r['id'] for r in data['sensor_data']]
                fejl_ids = [r['id'] for r in data['fejl_logs']]
                log_ids = [r['id'] for r in data['system_logs']]
                
                db.markér_som_synkroniseret(sensor_ids, fejl_ids, log_ids)
                
                self.forsøg_tæller = 0
                
                db.gem_system_log(
                    ENHEDS_ID,
                    'SyncClient',
                    f"Upload succes: {antal_rækker} rækker synkroniseret"
                )
                
                print(f"Upload succesfuldt: {antal_rækker} rækker")
                
            else:
                self.forsøg_tæller += 1
                
                fejl_tekst = respons.text[:200]
                db.gem_fejl(
                    ENHEDS_ID,
                    'SyncClient',
                    f"Server afviste ({respons.status_code}): {fejl_tekst}"
                )
                
                print(f"Upload fejlede: {respons.status_code}")
        
        except requests.exceptions.RequestException as fejl:
            self.forsøg_tæller += 1
            
            db.gem_fejl(
                ENHEDS_ID,
                'SyncClient',
                f"Netværksfejl (Forsøg {self.forsøg_tæller}): {str(fejl)}"
            )
            
            print(f"Netværksfejl: {fejl}")
    
    def stop(self) -> None:

        print("Stopper sync klient")
        self.kører = False
        db.gem_system_log(ENHEDS_ID, 'SyncClient', 'Lukker ned med graceful shutdown')



sync_klient: SyncKlient = SyncKlient()
