import sqlite3
import threading
from datetime import datetime, timedelta
from contextlib import contextmanager

'''
Vi opretter en klasse til databasen. Vores database er en sqlite database, hvilket betyder
at vi gemmer vores logs i en database fil på vores lokale server.
Vi bruger thread safety til filen, så der kun kan redigeres i den af en af gangen.'''
class Database:
    
    '''Her opretter vi så selve databasen'''
    def __init__(self, db_path: str = "sensor_data.db"):
        self.db_path = db_path
        self.lock = threading.Lock()
        #Her tjekker vi om databasen findes og ellers opretter vi den
        self._init_database()
    
    '''
    Vi benytter contextmanager til vores kode som sørger for at "processer" ikke
    fanges i databasen så den ikke kan tilgås af andre pga. vores thread safety mekanisme
    hele funktionalitet af contextmanager beskrives i dokumentations filen.
    Den åbner op for at vi kan bruge with og yield uden at have oprettet en klasse først.'''
    @contextmanager
    # Her er fremgangsmåden for at skabe forbindelse til databasen
    def get_connection(self):
        # Her defineres reglerne for at skabe forbindelse til databasen.
        # Med "check_same_thread=False" siger f.eks. at kun en tråd må tilgå ad gangen.
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        '''
        Med sqlite3.row gør vi at når vi henter data fra databasen, så bliver det gjort i navne-værdier
        i stedet for tal som den selv ville generere. Det gør at det er nemmere at tilføje kolonner
        i databasen. Der står mere information med eksempel inde på dokumentations filen.'''
        conn.row_factory = sqlite3.Row
        try:
            #Med Yield for en tråd besked på at vente med at fortsætte indtil contextmanager giver lov
            yield conn
            #Hvis ændringenen gik fint igennem gemmes det i databasen
            conn.commit()
        # Hvis der skete en fejl under ændringer af databasen laver vi en rollback
        except:
            # rollback gør at databasen gendannes som den var før at der blev skrevet i den
            conn.rollback()
            # Vi "raiser" fejlen og fortæller at der skete en fejl til dem som kaldte db.get_connection()
            raise
        # Med finally fortæller vi, at "inden du smutter" så skal du lige -
        finally:
            # "Lukke din forbindelse så den næste kan komme til"
            conn.close()
    
    # her opretter vi en intern funktion
    def _init_database(self):
        # Her gennemgår vi det vi lavede før med contextmanageren - så kun en kan tilgå af gangen
        # samt at fejl bliver håndteret. - derfor vi kan bruge "with"
        with self.get_connection() as conn:
            '''
            Nu har tråden fået adgang til databasen, men får at vide hvad den skal lave herinde,
            så får den en cursor, som fortæller at "du har nu et skriveredskab"'''
            cursor = conn.cursor()
            
            # Med det skriveredskab giver vi nu besked med .execute på hvad der skal skrives.
            # her skal der laves en table til sensor data
            '''
            Vi opretter en table hvis det ikke allerede findes.
            Vi fortæller hvilke informationer der skal udfyldes
            som er (id, timestamp, source, data_type, value og synced)
            Id, Timestamp og synced bliver automatisk udfyldt hvis tråden ikke gør det'''
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sensor_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    source TEXT NOT NULL,
                    data_type TEXT NOT NULL,
                    value REAL,
                    synced INTEGER DEFAULT 0
                )
            ''')
            
            # Så en til errors
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    source TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    synced INTEGER DEFAULT 0
                )
            ''')

            # Og til sidst en med system logs
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS system_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    source TEXT NOT NULL,
                    message TEXT NOT NULL,
                    synced INTEGER DEFAULT 0
                )
            ''')
    
    # Her kommer funktionerne til at indskrive i vores databaser

    # Først har vi vores log_sensor table
    # Her ønsker vi 3 værdier da vi selv udfylder resten
    def log_sensor(self, source: str, data_type: str, value: float):
        try:
            # Vi har lock så det kun er en tråd der kan tilgå af gangen
            with self.lock:
                # De får adgang til databasen og bliver sat i yield hvis der er kø
                with self.get_connection() as conn:
                    # De skriver nu deres data ind - VALUES (?, ?, ?) beskytter mod SQL-injektion
                    # Jeg forklarer mere i dokumentation_info hvordan det beskytter os
                    conn.execute(
                        'INSERT INTO sensor_data (source, data_type, value) VALUES (?, ?, ?)',
                        (source, data_type, value)
                    )
        # Hvis der sker en fejl her ønsker vi at koden kører videre, da dette er en database fejl.
        # Det vil skabe et loop af fejl i databasen som vi ikke ønsker, derfor bruger vi pass
        except:
            pass
    
    # Samme koncept - nu bare vores error logs
    def log_error(self, source: str, error_message: str):
        try:
            with self.lock:
                with self.get_connection() as conn:
                    conn.execute(
                        'INSERT INTO errors (source, error_message) VALUES (?, ?)',
                        (source, error_message)
                    )
        except Exception as e:
            pass

    # Samme koncept - nu bare vores event logs
    def log_event(self, source: str, message: str):
        """Gem en hændelse/kommando (Info log) - NY FUNKTION"""
        try:
            with self.lock:
                with self.get_connection() as conn:
                    conn.execute(
                        'INSERT INTO system_logs (source, message) VALUES (?, ?)',
                        (source, message)
                    )
        except Exception as e:
            pass
    
    # Nu skal vi så hente data fra databasen så vi kan sende det videre til vores remote server
    def get_unsynced_data(self):
        # Vi starter med at tilgå databasen gennem vores contextmanager
        with self.get_connection() as conn:
            # Vi får igen arbejdsredskabet
            cursor = conn.cursor()
            
            # Nu henter vi alt fra sensor_data table'en som vi ikke har markeret som synkroniseret
            cursor.execute('SELECT * FROM sensor_data WHERE synced = 0')
            # Vi pakker hver række (row) som er synced = 0 i sensor_data table'en ind som et dictionary
            # fetchall gør at vi henter alle rækkerne
            sensor_data = [dict(row) for row in cursor.fetchall()]
            
            # Her er det så fra error table
            cursor.execute('SELECT * FROM errors WHERE synced = 0')
            errors = [dict(row) for row in cursor.fetchall()]

            # og så fra system logs table
            cursor.execute('SELECT * FROM system_logs WHERE synced = 0')
            system_logs = [dict(row) for row in cursor.fetchall()]
            
            # Til sidst returnerer vi så en dictionary over de 3 nye lister
            # Og hver liste indeholder så det antal dictionaries der svar til hvor
            # mange rækker i databasen der var markeres med "synced = 0"
            return {
                'sensor_data': sensor_data,
                'errors': errors,
                'system_logs': system_logs
            }
    
    # Nu skal vi så markerer den data vi lige har returneret som synced - sync_client.py har netop behandlet det
    def mark_as_synced(self, sensor_ids: list, error_ids: list, log_ids: list = None):
        # Hvis vi ikke får en liste, så opretter vi en tom liste, så koden ikke crasher da den forventer en liste at behandle
        if log_ids is None:
            log_ids = []

        # Nu skal vi så igang med at ændre synced feltet
        # Vi benytter selvfølgelig en lås, så vi ikke går ind før der er plads
        with self.lock:
            # Vi får adgang via contextmanager
            with self.get_connection() as conn:
                # Vi tjekker om listen indeholdt sensor_ids - hvis den gjorde fortsætter vi
                if sensor_ids:
                    '''
                    Nu bliver det lidt tricky - vi laver en string med placeholders igen fordi at vi ikke ønsker
                    At forkerte værdi skal kunne udføre en SQL injection
                    Der for laver vi en string med antallet af værdier som vi modtager fra listen.
                    Altså en liste der ser sådan her ud [1, 6, 10] ville gøre placeholder til en string
                    der ser sådan her ud "?, ?, ?"'''
                    placeholders = ','.join('?' * len(sensor_ids))
                    '''
                    Nu kan vi så rette databasen. Vi siger at vi skal ændre synced til 1 på "?, ?, ?
                    Hvor så at sensor_ids er data over de id'er der skal rettes - feks. id 1, 6, 10
                    Dette gør at i stedet for at der ville kunne udføres en sql injection hvis nu at
                    det ene id var en SQL kommando, så tager den i stedet og kigger efter id'et da den nu ved at det er data'''
                    conn.execute(f'UPDATE sensor_data SET synced = 1 WHERE id IN ({placeholders})', sensor_ids)
                
                # Samme koncept bare med error_ids
                if error_ids:
                    placeholders = ','.join('?' * len(error_ids))
                    conn.execute(f'UPDATE errors SET synced = 1 WHERE id IN ({placeholders})', error_ids)

                # Samme koncept bare med log_ids
                if log_ids:
                    placeholders = ','.join('?' * len(log_ids))
                    conn.execute(f'UPDATE system_logs SET synced = 1 WHERE id IN ({placeholders})', log_ids)
    
    # Nu skal vi så slette rækkerne fra vores database tables som er ældre end 7 dage
    
    # Vi opretter en funktion og sætter days til at være 7
    def cleanup_old_data(self, days: int = 7):
        
        # Først finder vi vores cuf-off dato, som så er 7 dage siden fra dags dato
        cutoff = datetime.now() - timedelta(days=days)
        
        try:
            # Vi skal havde adgang, så vi er de eneste der skriver
            with self.lock:
                # Vi får plads af contextmanager
                with self.get_connection() as conn:
                    # Her sletter vi så fra sensor_data table hvor at timestamp er før ? som er vores placeholder for cutoff
                    # Vi ønsker også at synced er 1 da vi ikke vil slette data som remote serveren ikke har modtaget
                    conn.execute('DELETE FROM sensor_data WHERE timestamp < ? AND synced = 1', (cutoff,))
                    
                    # Samme koncept med error table
                    conn.execute('DELETE FROM errors WHERE timestamp < ? AND synced = 1', (cutoff,))
    
                    # og med system table
                    conn.execute('DELETE FROM system_logs WHERE timestamp < ? AND synced = 1', (cutoff,))
                    
        except:
            # Da vi ikke ønsker at låse databasen i et loop siger vi bare pass ved fejl
            pass

# Global Singleton Instance - opretter og sikrer at vi kun har en enkelt database tråd
db = Database()