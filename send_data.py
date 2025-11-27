import json
import sqlite3
from time import sleep
import secrets
import os


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database", "sensor_data.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def get_data(number_of_rows):
        query = """SELECT * FROM data ORDER BY datetime DESC;"""
        data=[]
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(query)
            rows = cur.fetchmany(number_of_rows)
            for row in reversed(rows):
                 

                 
                 dataraw = {
                      
                    "datetime": row[0],
                    "temperaturesIN": row[1],
                    "humiditiesIN": row[2],
                    "temperaturesOUT": row[3],
                    "humiditiesOUT": row[4],
                    "luftkvalitet": row[5],
                    "vindue_tilstand": row[6],
                    "låst":  row[7]

                 }
                 data.append(dataraw)
            return data
        except sqlite3.Error as sql_e:
            print(f"sqlite error occured: {sql_e}")
            conn.rollback()
        except Exception as e:
            print(f"Error occured: {e}")
        finally:
            conn.close()
        sleep(1)
with open("data2.json", "w") as outfile:
     json.dump(get_data(5), outfile)
