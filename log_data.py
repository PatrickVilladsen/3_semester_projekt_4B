import sqlite3
from datetime import datetime
from random import randint, choice
from time import sleep
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database", "sensor_data.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def create_table():
    query = """CREATE TABLE IF NOT EXISTS data (datetime TEXT NOT NULL, temperaturIN REAL NOT NULL,
        fugtighedIN REAL NOT NULL, temperaturOUT REAL NOT NULL, fugtighedOUT REAL NOT NULL,
        luftkvalitet REAL NOT NULL, vindue_tilstand STRING NOT NULL, låst BOOLEAN NOT NULL);"""

    try:
        
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(query)
        conn.commit()
        
    except sqlite3.Error as sql_e:
        print(f"sqlite error occured: {sql_e}")
    except Exception as e:
        print(f"Error occured: {e}")
    finally:
        conn.close()

def log_dht11():
    while True:
        query = """INSERT INTO data (datetime, temperaturIN, fugtighedIN, temperaturOUT, fugtighedOUT,
             luftkvalitet, vindue_tilstand, låst) VALUES(?, ?, ?, ?, ?, ?, ?, ?)"""
        now = datetime.now()
        now = now.strftime("%d/%m/%y %H:%M:%S")
        data = (now, randint(0, 30), randint(0, 100), randint(0, 30), randint(0, 100), randint(0, 100), choice(["Lukket", "Åbent"]), choice(["True", "False"]) )

        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(query, data)
            conn.commit()
        except sqlite3.Error as sql_e:
            print(f"sqlite error occured: {sql_e}")
            conn.rollback()
        except Exception as e:
            print(f"Error occured: {e}")
        finally:
            
            conn.close()
        sleep(1)

create_table()
log_dht11()
