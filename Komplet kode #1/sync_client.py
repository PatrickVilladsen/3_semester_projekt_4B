import threading
import time
import requests
import json
from config import REMOTE_SERVER_URL, BEARER_TOKEN, SYNC_INTERVAL, DEVICE_ID
from database import db



# Vi opretter vores klasse med threads som tillader concurrency
class SyncClient(threading.Thread):
    '''
    Her gør vi ligesom i indoor_sensor.py brug af funktioner fra Threads
    __init__ kalder konstruktøren fra "parent class" - som er (threading.Thread)
    super() sikrer at konstruktøren for "parent class" - threading.Thread kaldes først, før vi kører videre'''
    def __init__(self):
        '''daemon=True skaber en daemon-tråd som gør at vi kan lukke koden
        selvom at denne tråd stadig kører - det hjælper med vores graceful shutdown.
        name= gør at vi giver tråden et navn, så vi nemt kan finde fejl i error handling'''
        super().__init__(daemon=True, name="SyncClient")
        # Vi sætter tråden til at køre - den sættes til False i main.py når programmet slukkes
        self.running = True
        self.retry_count = 0
    
    # Vi definerer vores "run"
    def run(self):
        db.log_event('SYNC_CLIENT', f"Sync Client startet - Sender til remote server hvert {SYNC_INTERVAL}. sek.")
        
        # Vi giver en buffer for at etablere netværksforbindelse
        time.sleep(30)
        
        # Loop der har et formål - at synkronisere data med remote serveren
        while self.running:
            try:
                # Defineres lidt længere nede
                self.sync_data()
            except Exception as e:
                # fejl til error logs i db
                db.log_error('SYNC_CLIENT', f"Fejl i run-loop: {str(e)}")
            
            # Ved fejl med connection laver exponential backoff så vi ikke spammer serveren imens den har problemer
        if self.retry_count > 0:
            exponential_wait = SYNC_INTERVAL * (2 ** min(self.retry_count, 3))
            # Max
            wait_time = min(3600, exponential_wait)
            time.sleep(wait_time)
        else:
            time.sleep(SYNC_INTERVAL)
    
    # Her er hvordan vi synkronisere dataen
    def sync_data(self):
        
        # Først henter vi det data fra databasen som ikke er synkroniseret
        data = db.get_unsynced_data()
        
        # Ryd op i gammel data hver gang vi synkroniserer
        try:
            db.cleanup_old_data(days=7)
        except Exception as e:
            db.log_error('SYNC_CLIENT', f"Cleanup fejl: {e}")

        # Hvis der ikke er noget nyt ikke-synkroniseret data bliver der returneret til sleep
        if not data['sensor_data'] and not data['errors'] and not data['system_logs']:
            return
        
        # tæller hvor mange rækker vi har sendt afsted - kan bruges til at spotte nedbrud
        count = len(data['sensor_data']) + len(data['errors']) + len(data['system_logs'])
        
        # Vi pakker nu alt data'en ned i et dictionary til Json
        payload = {
            'sensor_data': data['sensor_data'],
            'errors': data['errors'],
            'system_logs': data['system_logs'],
            'device_id': DEVICE_ID
        }
        
        # Nu skal vi sende det afsted til vores remote server
        try:
            # Vi forspørger at sende data og vi kommer så med information om hvor og hvad vi vil sende, samt vores bearer token og hvad det er vi sender
            response = requests.post(
                REMOTE_SERVER_URL,
                json=payload,
                # Headers er lavet efter de "færdselsregler" som forskellige frameworks følger
                headers={
                    'Authorization': f'Bearer {BEARER_TOKEN}',
                    'Content-Type': 'application/json'
                },
                # Hvis vi ikke får respons laver vi timeout efter 30 sekunder så vi ikke går i stå
                timeout=30
            )
            
            # Nu tjekker vi det svar vi modtager fra remote serveren
            # 200 er koden for modtaget
            if response.status_code == 200:
                # Da serveren har anerkendt at dataen er modtaget ønsker vi at markerer vores data vi sendte som synkroniseret.
                # derfor laver vi en liste med de ID'er som blev synkroniseret
                sensor_ids = [row['id'] for row in data['sensor_data']]
                error_ids = [row['id'] for row in data['errors']]
                log_ids = [row['id'] for row in data['system_logs']]
                
                # Vi sender listen videre til databasen som retter det til i databasen
                db.mark_as_synced(sensor_ids, error_ids, log_ids)

                self.retry_count = 0
                
                # Vi gemmer i databasen at vi fik synkroniseret et x-antal rækker
                db.log_event('SYNC_CLIENT', f"Upload til remote server fuldført! {count} rækker blev sendt!.")
            
            else:
                # error log til database hvis vi ikke kunne uploade men fik forbindelse
                db.log_error('SYNC_CLIENT', f"Fejl i upload til remote server: ({response.status_code}): {response.text}")
        
        # Fejl til hvis vi slet ikke fik forbindelse til serveren
        except requests.exceptions.RequestException as e:
            # Gemmer lokalt og sendes så med når vi har forbindelse igen
            db.log_error('SYNC_CLIENT', f"Network error: {str(e)}")
            pass

            self.retry_count += 1
            db.log_error('SYNC_CLIENT', f"Problem med synkronisering med remote server. Forsøg: {self.retry_count}): {str(e)}")

    # Her definerer vi så stop som bruges ved graceful shutdown
    def stop(self):
        self.running = False
        db.log_event('SYNC_CLIENT', 'Sync_client stoppet.')

# Global Singleton Instance - opretter og sikrer at vi kun har en enkelt sync_client tråd
sync_client = SyncClient()