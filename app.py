from flask import Flask, render_template
from apiflask import APIFlask, Schema
from apiflask.fields import Integer, String
import json
import sqlite3
from time import sleep

app = APIFlask(__name__)

class DHT11Data(Schema):
     datetime = String(required=True)
     temperature = Integer(required=True)
     humidity = Integer(required=True)
"""
def read_json():
     with open("patients.yml") as yaml_file:
        yaml_data = yaml.safe_load(yaml_file)
        return yaml_data
"""

def get_data(number_of_rows):
        query = """SELECT * FROM stue ORDER BY datetime DESC;"""
        datetimes = []
        temperatures = []
        humidities = []
        try:
            conn = sqlite3.connect("database/sensor_data.db")
            cur = conn.cursor()
            cur.execute(query)
            rows = cur.fetchmany(number_of_rows)
            for row in reversed(rows):
                 datetimes.append(row[0])
                 temperatures.append(row[1])
                 humidities.append(row[2])
            return datetimes, temperatures, humidities
        except sqlite3.Error as sql_e:
            print(f"sqlite error occured: {sql_e}")
            conn.rollback()
        except Exception as e:
            print(f"Error occured: {e}")
        finally:
            conn.close()
        sleep(1)

@app.post('/add_dht11')
@app.input(DHT11Data)
def add_new_dht11_reading():
     return get_data(10)

@app.route("/")
def index():
    return render_template('index.html')

@app.route("/data")
def data():
    """
    Funktion der viser humidity data på hjemmeside
    """
    #humidity_data = read_humidity(dummy_json)
    all_data = get_data(10)
    return render_template('data.html', all_data = all_data)

if __name__ == "__main__":
    app.run()