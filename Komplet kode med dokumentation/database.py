"""
SQLite Database modul til lagring af sensor data, stauts, logs samt overblik
over synkroniseringsstatus med remote serveren.

Dette modul implementerer en trådsikker SQLite database wrapper der håndterer:
- Sensor data logging (temperatur, fugtighed, batteri, gas)
- Fejl logging fra alle systemkomponenter
- System events logging (kommandoer, status ændringer)
- Synkroniserings tracking til remote PostgreSQL server
- Automatisk oprydning af gammel data (>7 dage)
- Historisk dataudtræk til grafer

Arkitektur:
    - Thread Safety: Mutex lås omkring alle database operationer
    - Context Manager: Automatisk commit/rollback
    - "Parameterized Queries": SQL injection beskyttelse ved brug af placeholders
    - ACID Compliance: Enten lykkedes hele opdateringen, ellers bliver ændringerne fjernet
    - Indexing: System-performance på målt_klokken og synkroniseret

Database Skema (Matcher vores remote server):
    sensor_data: id, enheds_id, målt_klokken, kilde, data_type, værdi, synkroniseret
    fejl_logs: id, enheds_id, målt_klokken, kilde, fejl_besked, synkroniseret
    system_logs: id, enheds_id, målt_klokken, kilde, besked, synkroniseret

Thread Safety:
    Alle skrive-metoder bruger self.lås til at sikre at kun en tråd
    kan skrive ad gangen. Dette forhindrer "Database is locked" fejl.

Context Manager:
    with db.hent_forbindelse() as conn:
        conn.execute("INSERT ...")
    
    Sikrer automatisk commit ved succes og rollback ved fejl.

SQL Injection Beskyttelse:
    Brug altid placeholders:
    Sikkert:   execute("INSERT ... VALUES (?)", (værdi,))
    Usikkert:  execute(f"INSERT ... VALUES ({værdi})")
"""

import sqlite3
import threading
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Generator, List, Dict, Any, Optional, Tuple
import os


class Database:
    """
    Trådsikker SQLite database wrapper.
    
    Gemmer data lokalt indtil det kan synkroniseres til remote server.
    """
    
    def __init__(self, database_sti: str = "sensor_data.db") -> None:
        """
        Initialiserer database og opretter skema.
        
        Args:
            database_sti: Sti til database filen (sensor_data.db)
        """
        self.database_sti: str = database_sti
        self.lås: threading.Lock = threading.Lock()
        
        # Debug
        print(f"Database initialiseret: {os.path.abspath(database_sti)}")
        
        # Opret skema
        self._initialiser_database()
    
    @contextmanager
    def hent_forbindelse(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager der giver en database forbindelse.
        
        Håndterer automatisk commit/rollback og lukning af forbindelse.
        """
        # check_same_thread=False fordi vi bruger mutex lock i stedet
        forbindelse = sqlite3.connect(self.database_sti, check_same_thread=False)
        forbindelse.row_factory = sqlite3.Row
        
        try:
            yield forbindelse
            forbindelse.commit()
        except Exception:
            forbindelse.rollback()
            raise
        finally:
            forbindelse.close()
    
    def _initialiser_database(self) -> None:
        """
        Opretter tabeller og indexes hvis de ikke findes.
        
        Bruger "IF NOT EXISTS" så det er sikkert at køre ved hver opstart.
        """
        with self.lås:
            with self.hent_forbindelse() as forbindelse:
                markør = forbindelse.cursor()
                
                # Sensor data tabel
                markør.execute('''
                    CREATE TABLE IF NOT EXISTS sensor_data (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        enheds_id TEXT NOT NULL,
                        målt_klokken DATETIME DEFAULT CURRENT_TIMESTAMP,
                        kilde TEXT NOT NULL,
                        data_type TEXT NOT NULL,
                        værdi REAL,
                        synkroniseret INTEGER DEFAULT 0
                    )
                ''')
                
                # Fejl logs tabel
                markør.execute('''
                    CREATE TABLE IF NOT EXISTS fejl_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        enheds_id TEXT NOT NULL,
                        målt_klokken DATETIME DEFAULT CURRENT_TIMESTAMP,
                        kilde TEXT NOT NULL,
                        fejl_besked TEXT NOT NULL,
                        synkroniseret INTEGER DEFAULT 0
                    )
                ''')
                
                # System logs tabel
                markør.execute('''
                    CREATE TABLE IF NOT EXISTS system_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        enheds_id TEXT NOT NULL,
                        målt_klokken DATETIME DEFAULT CURRENT_TIMESTAMP,
                        kilde TEXT NOT NULL,
                        besked TEXT NOT NULL,
                        synkroniseret INTEGER DEFAULT 0
                    )
                ''')
                
                # Indexes for performance
                markør.execute('''
                    CREATE INDEX IF NOT EXISTS idx_sensor_tid
                    ON sensor_data(målt_klokken)
                ''')
                markør.execute('''
                    CREATE INDEX IF NOT EXISTS idx_fejl_tid
                    ON fejl_logs(målt_klokken)
                ''')
                markør.execute('''
                    CREATE INDEX IF NOT EXISTS idx_log_tid
                    ON system_logs(målt_klokken)
                ''')
                
                # Synkroniserings indexes
                markør.execute('''
                    CREATE INDEX IF NOT EXISTS idx_sensor_synk
                    ON sensor_data(synkroniseret)
                ''')
                markør.execute('''
                    CREATE INDEX IF NOT EXISTS idx_fejl_synk
                    ON fejl_logs(synkroniseret)
                ''')
                markør.execute('''
                    CREATE INDEX IF NOT EXISTS idx_log_synk
                    ON system_logs(synkroniseret)
                ''')
                
                # Enheds ID index
                markør.execute('''
                    CREATE INDEX IF NOT EXISTS idx_sensor_enhed
                    ON sensor_data(enheds_id)
                ''')
                # debug
                print("Database skema verificeret")
    
    def gem_sensor_data(
        self,
        enheds_id: str,
        kilde: str,
        data_type: str,
        værdi: float
    ) -> None:
        """
        Logger en sensor måling der skal til at skrives ind i databasen.
        
        Args:
            enheds_id: ID på enheden (f.eks. 'esp32_sensor')
            kilde: Sensoren (f.eks. 'DHT11', 'BME680')
            data_type: Typen ('temperatur', 'luftfugtighed', 'batteri', 'gas')
            værdi: Målingen som float
        """
        try:
            with self.lås:
                with self.hent_forbindelse() as forbindelse:
                    forbindelse.execute(
                        'INSERT INTO sensor_data '
                        '(enheds_id, kilde, data_type, værdi, synkroniseret) '
                        'VALUES (?, ?, ?, ?, 0)',
                        (enheds_id, kilde, data_type, værdi)
                    )
        except Exception:
            # ignorer fejl for at undgå crashes
            pass
    
    def gem_fejl(
        self,
        enheds_id: str,
        kilde: str,
        fejl_besked: str
    ) -> None:
        """
        Logger en fejlbesked der skal til at skrives ind i databasen.
        
        Args:
            enheds_id: Enheden der oplevede fejlen
            kilde: Komponenten (f.eks. 'MQTT', 'BME680')
            fejl_besked: Beskrivelse af fejlen
        """
        try:
            with self.lås:
                with self.hent_forbindelse() as forbindelse:
                    forbindelse.execute(
                        'INSERT INTO fejl_logs '
                        '(enheds_id, kilde, fejl_besked, synkroniseret) '
                        'VALUES (?, ?, ?, 0)',
                        (enheds_id, kilde, fejl_besked)
                    )
        except Exception:
            pass
    
    def gem_system_log(
        self,
        enheds_id: str,
        kilde: str,
        besked: str
    ) -> None:
        """
        Logger et system event der skal til at skrives ind i databasen.
        
        Bruges til vigtige events som ikke er fejl (startup, kommandoer, etc.)
        
        Args:
            enheds_id: Enheden der genererede eventet
            kilde: Komponenten (f.eks. 'Main', 'ClimateCtrl')
            besked: Hvad der skete
        """
        try:
            with self.lås:
                with self.hent_forbindelse() as forbindelse:
                    forbindelse.execute(
                        'INSERT INTO system_logs '
                        '(enheds_id, kilde, besked, synkroniseret) '
                        'VALUES (?, ?, ?, 0)',
                        (enheds_id, kilde, besked)
                    )
        except Exception:
            pass
    
    def hent_usynkroniseret_data(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Henter data der mangler at blive uploadet til remote server.
        
        Returnerer data i format der matcher remote API payload.
        """
        with self.hent_forbindelse() as forbindelse:
            markør = forbindelse.cursor()
            
            # Hent usynkroniseret data fra alle tabeller
            markør.execute(
                'SELECT * FROM sensor_data WHERE synkroniseret = 0'
            )
            sensor_data = [dict(række) for række in markør.fetchall()]
            
            markør.execute(
                'SELECT * FROM fejl_logs WHERE synkroniseret = 0'
            )
            fejl = [dict(række) for række in markør.fetchall()]
            
            markør.execute(
                'SELECT * FROM system_logs WHERE synkroniseret = 0'
            )
            logs = [dict(række) for række in markør.fetchall()]
            
            # Debug
            total = len(sensor_data) + len(fejl) + len(logs)
            if total > 0:
                print(f"Henter {total} usynkroniserede rækker")
            
            return {
                'sensor_data': sensor_data,
                'fejl_logs': fejl,
                'system_logs': logs
            }
    
    def markér_som_synkroniseret(
        self,
        sensor_ids: Optional[List[int]] = None,
        fejl_ids: Optional[List[int]] = None,
        log_ids: Optional[List[int]] = None
    ) -> None:
        """
        Markerer rækker som uploadet efter succesfuld synkronisering.
        
        Kaldes af sync_client når remote server har bekræftet modtagelse.
        """
        if sensor_ids is None:
            sensor_ids = []
        if fejl_ids is None:
            fejl_ids = []
        if log_ids is None:
            log_ids = []
        
        with self.lås:
            with self.hent_forbindelse() as forbindelse:
                
                # Opdater sensor data
                if sensor_ids:
                    pladsholdere = ','.join('?' * len(sensor_ids))
                    forbindelse.execute(
                        f'UPDATE sensor_data SET synkroniseret = 1 '
                        f'WHERE id IN ({pladsholdere})',
                        sensor_ids
                    )
                    print(f"Markeret {len(sensor_ids)} sensor rækker")
                
                # Opdater fejl
                if fejl_ids:
                    pladsholdere = ','.join('?' * len(fejl_ids))
                    forbindelse.execute(
                        f'UPDATE fejl_logs SET synkroniseret = 1 '
                        f'WHERE id IN ({pladsholdere})',
                        fejl_ids
                    )
                    print(f"Markeret {len(fejl_ids)} fejl rækker")
                
                # Opdater logs
                if log_ids:
                    pladsholdere = ','.join('?' * len(log_ids))
                    forbindelse.execute(
                        f'UPDATE system_logs SET synkroniseret = 1 '
                        f'WHERE id IN ({pladsholdere})',
                        log_ids
                    )
                    print(f"Markeret {len(log_ids)} log rækker")
    
    def hent_datahistorik(
        self,
        data_type: str,
        dage: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Henter historisk data til grafer.
        
        Args:
            data_type: Typen ('temperatur', 'luftfugtighed', 'batteri', 'gas')
            dage: Hvor langt tilbage (default: 7)
        
        Returns:
            Liste af målinger sorteret efter tid
        """
        cutoff = datetime.now() - timedelta(days=dage)
        
        with self.hent_forbindelse() as forbindelse:
            markør = forbindelse.cursor()
            markør.execute('''
                SELECT målt_klokken, værdi, kilde, enheds_id
                FROM sensor_data
                WHERE data_type = ? AND målt_klokken >= ?
                ORDER BY målt_klokken ASC
            ''', (data_type, cutoff))
            
            return [dict(række) for række in markør.fetchall()]
    
    def ryd_gammel_data(self, dage: int = 7) -> Tuple[int, int, int]:
        """
        Sletter gammel synkroniseret data for at spare plads.
        
        Sletter kun data der er ældre end 'dage' (7) OG er synkroniseret.
        Dette sikrer at vi aldrig sletter data uden backup.
        
        Returns:
            Antal slettede rækker: (sensor, fejl, logs)
        """
        cutoff = datetime.now() - timedelta(days=dage)
        
        try:
            with self.lås:
                with self.hent_forbindelse() as forbindelse:
                    markør = forbindelse.cursor()
                    
                    # Slet gammel sensor data
                    markør.execute(
                        'DELETE FROM sensor_data '
                        'WHERE målt_klokken < ? AND synkroniseret = 1',
                        (cutoff,)
                    )
                    sensor = markør.rowcount
                    
                    # Slet gamle fejl
                    markør.execute(
                        'DELETE FROM fejl_logs '
                        'WHERE målt_klokken < ? AND synkroniseret = 1',
                        (cutoff,)
                    )
                    fejl = markør.rowcount
                    
                    # Slet gamle logs
                    markør.execute(
                        'DELETE FROM system_logs '
                        'WHERE målt_klokken < ? AND synkroniseret = 1',
                        (cutoff,)
                    )
                    logs = markør.rowcount
                    
                    print(f"Cleanup: Slettet {sensor + fejl + logs} rækker")
                    return (sensor, fejl, logs)
        
        except Exception as e:
            print(f"Cleanup fejl: {e}")
            return (0, 0, 0)


# Global instans
db: Database = Database()
"""
Global database instans brugt af hele applikationen.
"Falsk" singleton instans - der kan oprettes nye instanser med denne instans

Import:
    from database import db
    db.gem_sensor_data('esp32_sensor', 'DHT11', 'temperatur', 22.5)
"""