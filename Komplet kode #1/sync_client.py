"""
Remote Server sync_client til robust data-replikering og synkronisering.

Dette modul implementerer "Store-and-Forward"-strategi til lagring af vore data.
Det fungerer som bindeleddet mellem den lokale server (RPi5) og
vores remote server (Linux maskine med PostgreSQL database).

Hovedansvarsområder:
    1. Replikering: Flytter data fra lokal SQLite til remote PostgreSQL
    2. Resiliens: Håndterer netværksudfald uden datatab
    3. Husholdning: Sørger for at lokal serveren ikke fyldes op (data-oprydning)
    4. Feedback: Opdaterer lokal 'synkroniseret' status baseret på remote server-svar

Arkitektur: Store-and-Forward
    I stedet for at sende data direkte fra sensor til remote server over det store internet
    (hvilket er sårbart), gemmes alt data først lokalt i database.py.
    SyncKlient tager derefter over og sender data i "batches".
    
    Fordele:
    - Intet datatab hvis internettet ryger i 1 time eller 1 uge
    - Effektiv udnyttelse af båndbredde (sender mange målinger i en enkelt HTTP request)
    - Reducerer strømforbrug ved ikke at holde forbindelsen oprettet konstant

Retry Strategi: Eksponentiel Backoff
    Hvis serveren er nede, eller netværket fejler, nytter det ikke at hamre løs.
    Vi bruger en "Backoff" algoritme der gradvist øger ventetiden:
    
    Formel: Ventetid = SYNC_INTERVAL * (2 ^forsøg_tæller)
    
    - Forsøg 1: 5 minutter (300 sekunder)
    - Forsøg 2: 10 minutter (600 sekunder)
    - Forsøg 3: 20 minutter (1200 sekunder)
    - Forsøg 4+: 40 minutter (Capped ved 40 minutter (2400 sekunder))
    
    Dette beskytter remote serveren for yderligere overbelastning, end det
    den i forvejen døjer med.

Sikkerhed:
    - Bearer Token: Vi autentificerer os med en secret token i headeren
    - Datavalidering: Vi stoler kun på serverens '200 OK' svar før vi
      markerer data som sendt lokalt
    - Vi ville kunne få udvidet med TLS certifikat til at benytte HTTPS

Ydeevne:
    - Batching: Vi sender flere målinger ad gangen.
    - Non-blocking: Kører i sin egen tråd, så sensor-målinger ikke forsinkes
      af langsom forbindelse
"""

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
    """
    Trådbaseret synkroniserings-agent.
    
    Denne klasse kører som en 'Daemon Thread' i baggrunden. Det betyder,
    at den "lever" sit eget liv parallelt med hovedprogrammet, men automatisk
    bliver lukket ned, hvis hovedprogrammet stopper (f.eks. ved genstart).
    
    Designsopbygning: Worker Thread
        Tråden sover det meste af tiden (sleep). Når den vågner, udfører den
        et stykke arbejde (upload), og lægger sig til at sove igen.

    Det kaldes en agent da en arbejder selvstændigt (sin egen tråd), 
    reagerer på ændringer omkring den (behandler netværksfejl med eksponentiel backoff)
    og træffer egne belutninger (Når den vågner henter den aktivt data for at synkronisere det)
    """
    
    def __init__(self) -> None:
        """
        Konfigurerer tråden og opsætter tilstands-variabler.
        
        Sætter navn på tråden (godt til debugging) og markerer den som daemon.
        """
        super().__init__(daemon=True, name="SyncKlient")
        
        # 'kører' fungerer som en kill-switch til while-løkken
        self.kører: bool = True
        
        # Holder styr på hvor mange gange vi har fejlet i træk (til vores backoff)
        self.forsøg_tæller: int = 0
        
        # Debug
        print("Sync klient initialiseret")
    
    def run(self) -> None:
        """
        Trådens hoveddel (Main Loop).
        
        Denne metode kaldes automatisk, når man skriver sync_klient.start().
        Den kører i en uendelig løkke, indtil self.kører sættes til False.
        
        Flow:
            1. Opstart: Vent 30 sekunder (Lad netværk/DHCP komme på plads)
            2. Arbejde: Kald self.sync_data()
            3. Hvile: Beregn ventetid baseret på succes/fejl og sov
        """
        # Log opstart i system loggen
        db.gem_system_log(
            ENHEDS_ID,
            'SyncClient',
            f"Sync tråd startet. Interval: {SYNKRONISERINGS_INTERVAL}s"
        )
        
        # Debug
        print(f"Sync klient startet - Server: {REMOTE_SERVER_URL}")
        
        # "Warm-up" pause. Så RPi5 services kan starte op.
        time.sleep(30)
        
        while self.kører:
            try:
                # Udfør arbejdet
                self.sync_data()
                
            except Exception as fejl:
                # Vi benytter "Catch-all" da et crash af main-loop kan forårsage
                # at uploads stopper indtil genstart.
                db.gem_fejl(ENHEDS_ID, 'SyncClient', f"Uventet fejl i main-loop: {fejl}")
                print(f"Sync klient fejl: {fejl}")
            
            # Backoff logik
            if self.forsøg_tæller > 0:
                # Hvis vi har fejl, øger vi ventetiden eksponentielt (2^x)
                # min(x, 3) sikrer at vi max ganger med 2^3 = 8
                eksponent = min(self.forsøg_tæller, 3)
                
                # Beregn ny tid: Standard * Faktor
                ny_tid = SYNKRONISERINGS_INTERVAL * (2 ** eksponent)
                
                # Sæt et loft på 40 minutter (2400s), så vi ikke ender med at vente i flere dage
                vent_tid = min(2400, ny_tid)
                
                # Debug
                print(f"Backoff aktiv: Venter {vent_tid}s (Forsøg {self.forsøg_tæller})")
            else:
                # Hvis alt går godt (tæller er 0), bruger vi standard intervallet
                vent_tid = SYNKRONISERINGS_INTERVAL
            
            # Sov indtil næste cyklus
            time.sleep(vent_tid)
    
    def sync_data(self) -> None:
        """
        Kerne-logikken for synkronisering.
        
        Denne funktion håndterer hele livscyklussen for en datapakke:
            1. Udvælgelse (SELECT)
            2. Pakning (JSON)
            3. Transmission (HTTP POST)
            4. Bekræftelse (UPDATE)
            5. Oprydning (DELETE)
        
        Atomaritet:
            Det vigtigste princip her er, at vi aldrig sletter eller markerer
            data som sendt, før vi har fået en 100% sikker bekræftelse (HTTP 200)
            fra serveren. Dette garanterer "At-least-once" levering.
        """
        
        # Trin 1: Hent data
        # Vi henter alt, der har synkroniseret = 0
        data = db.hent_usynkroniseret_data()
        
        # Tjek om der overhovedet er noget at sende
        har_sensor_data = bool(data['sensor_data'])
        har_fejl = bool(data['fejl_logs'])
        har_logs = bool(data['system_logs'])
        
        if not (har_sensor_data or har_fejl or har_logs):
            # Databasen er tom for nye data. Vi går tidligt tilbage til sleep
            return
        
        # Trin 2: Oprydning
        # Vi starter med at fjerne data som er ældre end 7 dage og som
        # er sykroniseret. Dette gør at vi prøver at komme af med gammel
        # data først, før vi sender ny data afsted.
        try:
            db.ryd_gammel_data(dage=7)
        except Exception as fejl:
            # Vi logger fejlen, men stopper ikke upload-forsøget
            db.gem_fejl(ENHEDS_ID, 'SyncClient', f"Oprydning fejlede: {fejl}")
        
        # Trin 3: Payload-opbygning
        # Vi samler alt i en JSON struktur. Dette reducerer overhead, da vi kun
        # skal oprette en enkelt SSL/TLS forbindelse i stedet for en pr. række
        payload = {
            'enheds_id': ENHEDS_ID,
            'sensor_data': data['sensor_data'],
            'fejl_logs': data['fejl_logs'],
            'system_logs': data['system_logs']
        }
        
        # Statistik til logs
        antal_rækker = len(data['sensor_data']) + len(data['fejl_logs']) + len(data['system_logs'])
        
        # Debug
        print(f"Forsøger upload af {antal_rækker} rækker")
        
        try:
            # Trin 4: Send data (Netværks I/O)
            # requests.post er en blokerende operation
            # timeout=30 er derfor kritisk da vi uden den kunne få tråden til at hænge fast for evigt,
            # hvis serveren accepterer forbindelsen men aldrig svarer (Zombie connection)
            respons = requests.post(
                REMOTE_SERVER_URL,
                json=payload,
                headers={
                    'Authorization': f'Bearer {BEARER_TOKEN}',
                    'Content-Type': 'application/json'
                },
                timeout=30
            )
            
            # Trin 5: Håndter Serverens Svar
            if respons.status_code == 200:
                # Succes: Serveren har modtaget og gemt data
                
                # Vi udtrækker ID'erne fra de data, vi sendte
                # Det sikrer, at vi kun markerer præcis de rækker som serveren fik,
                # i tilfælde af at nye data er kommet ind i mellemtiden
                sensor_ids = [r['id'] for r in data['sensor_data']]
                fejl_ids = [r['id'] for r in data['fejl_logs']]
                log_ids = [r['id'] for r in data['system_logs']]
                
                # Opdater status lokalt (synkroniseret -> 1)
                db.markér_som_synkroniseret(sensor_ids, fejl_ids, log_ids)
                
                # Nulstil backoff-tælleren, da forbindelsen virker
                self.forsøg_tæller = 0
                
                # Log succesfuldt upload
                db.gem_system_log(
                    ENHEDS_ID,
                    'SyncClient',
                    f"Upload succes: {antal_rækker} rækker synkroniseret"
                )
                
                # Debug
                print(f"Upload succesfuldt: {antal_rækker} rækker")
                
            else:
                # Fejl fra server (4xx eller 5xx)
                # F.eks. 401 Unauthorized (forkert token) eller 500 Internal Error
                self.forsøg_tæller += 1
                
                # Vi logger serverens svartekst, så vi kan debugge det
                fejl_tekst = respons.text[:200]  # Max 200 tegn
                db.gem_fejl(
                    ENHEDS_ID,
                    'SyncClient',
                    f"Server afviste ({respons.status_code}): {fejl_tekst}"
                )
                
                # Debug
                print(f"Upload fejlede: {respons.status_code}")
        
        except requests.exceptions.RequestException as fejl:
            # Netværksfejl (Ingen forbindelse, DNS fejl, Timeout)
            self.forsøg_tæller += 1
            
            # Log netværksfejl
            db.gem_fejl(
                ENHEDS_ID,
                'SyncClient',
                f"Netværksfejl (Forsøg {self.forsøg_tæller}): {str(fejl)}"
            )
            
            # Debug
            print(f"Netværksfejl: {fejl}")
    
    def stop(self) -> None:
        """
        Graceful Shutdown.
        
        Sætter flaget 'kører' til False.
        Tråden stopper ikke øjeblikkeligt, men færdiggør sin nuværende cyklus
        (upload eller sleep) før den afslutter. Dette forhindrer korrupt data.
        """
        print("Stopper sync klient")
        self.kører = False
        db.gem_system_log(ENHEDS_ID, 'SyncClient', 'Lukker ned med graceful shutdown')


# Global instans

sync_klient: SyncKlient = SyncKlient()
"""
Global Singleton af SyncKlient.

Vi vil forhindre "Race Conditions". Hvis vi havde to sync-klienter kørende
samtidigt, ville de begge prøve at hente de samme data og sende dem
til serveren samtidig. Det ville resultere i dobbelt-data på serveren.

Ved at oprette instansen her, sikrer vi, at main.py blot importerer
denne ene instans og starter den.

Import:
    from sync_client import sync_klient
    
    sync_klient.start()
    sync_klient.stop()
"""