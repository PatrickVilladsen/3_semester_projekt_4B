import sqlite3
import threading
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Generator, List, Dict, Any, Optional, Tuple
import os


class Database:
    
    def __init__(self, database_sti: str = "sensor_data.db") -> None:

        self.database_sti: str = database_sti
        self.lås: threading.Lock = threading.Lock()
        
        print(f"Database initialiseret: {os.path.abspath(database_sti)}")
        
        self._initialiser_database()
    
    @contextmanager
    def hent_forbindelse(self) -> Generator[sqlite3.Connection, None, None]:
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
        with self.lås:
            with self.hent_forbindelse() as forbindelse:
                markør = forbindelse.cursor()

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
                
                markør.execute('''
                    CREATE INDEX IF NOT EXISTS idx_sensor_enhed
                    ON sensor_data(enheds_id)
                ''')

                print("Database skema verificeret")
    
    def gem_sensor_data(
        self,
        enheds_id: str,
        kilde: str,
        data_type: str,
        værdi: float
    ) -> None:
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
            pass
    
    def gem_fejl(
        self,
        enheds_id: str,
        kilde: str,
        fejl_besked: str
    ) -> None:

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

        with self.hent_forbindelse() as forbindelse:
            markør = forbindelse.cursor()
            
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

        if sensor_ids is None:
            sensor_ids = []
        if fejl_ids is None:
            fejl_ids = []
        if log_ids is None:
            log_ids = []
        
        with self.lås:
            with self.hent_forbindelse() as forbindelse:
                
                if sensor_ids:
                    pladsholdere = ','.join('?' * len(sensor_ids))
                    forbindelse.execute(
                        f'UPDATE sensor_data SET synkroniseret = 1 '
                        f'WHERE id IN ({pladsholdere})',
                        sensor_ids
                    )
                    print(f"Markeret {len(sensor_ids)} sensor rækker")
                
                if fejl_ids:
                    pladsholdere = ','.join('?' * len(fejl_ids))
                    forbindelse.execute(
                        f'UPDATE fejl_logs SET synkroniseret = 1 '
                        f'WHERE id IN ({pladsholdere})',
                        fejl_ids
                    )
                    print(f"Markeret {len(fejl_ids)} fejl rækker")
                
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
        cutoff = datetime.now() - timedelta(days=dage)
        
        try:
            with self.lås:
                with self.hent_forbindelse() as forbindelse:
                    markør = forbindelse.cursor()
                    
                    markør.execute(
                        'DELETE FROM sensor_data '
                        'WHERE målt_klokken < ? AND synkroniseret = 1',
                        (cutoff,)
                    )
                    sensor = markør.rowcount
                    
                    markør.execute(
                        'DELETE FROM fejl_logs '
                        'WHERE målt_klokken < ? AND synkroniseret = 1',
                        (cutoff,)
                    )
                    fejl = markør.rowcount
                    
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

db: Database = Database()