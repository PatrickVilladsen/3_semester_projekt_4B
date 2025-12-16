

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

                    CREATE TABLE IF NOT EXISTS fejl_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        enheds_id TEXT NOT NULL,
                        målt_klokken DATETIME DEFAULT CURRENT_TIMESTAMP,
                        kilde TEXT NOT NULL,
                        fejl_besked TEXT NOT NULL,
                        synkroniseret INTEGER DEFAULT 0
                    )

                    CREATE TABLE IF NOT EXISTS system_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        enheds_id TEXT NOT NULL,
                        målt_klokken DATETIME DEFAULT CURRENT_TIMESTAMP,
                        kilde TEXT NOT NULL,
                        besked TEXT NOT NULL,
                        synkroniseret INTEGER DEFAULT 0
                    )

                    CREATE INDEX IF NOT EXISTS idx_sensor_tid
                    ON sensor_data(målt_klokken)

                    CREATE INDEX IF NOT EXISTS idx_fejl_tid
                    ON fejl_logs(målt_klokken)

                    CREATE INDEX IF NOT EXISTS idx_log_tid
                    ON system_logs(målt_klokken)

                    CREATE INDEX IF NOT EXISTS idx_sensor_synk
                    ON sensor_data(synkroniseret)

                    CREATE INDEX IF NOT EXISTS idx_fejl_synk
                    ON fejl_logs(synkroniseret)

                    CREATE INDEX IF NOT EXISTS idx_log_synk
                    ON system_logs(synkroniseret)

                    CREATE INDEX IF NOT EXISTS idx_sensor_enhed
                    ON sensor_data(enheds_id)

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
