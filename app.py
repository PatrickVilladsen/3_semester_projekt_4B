from flask import Flask, render_template
from apiflask import APIFlask, Schema, HTTPTokenAuth
from apiflask.fields import Integer, String
import json
import sqlite3
from time import sleep
import secrets

app = APIFlask(__name__)

class DHT11Data(Schema):
     datetime = String(required=True)
     temperature = Integer(required=True)
     humidity = Integer(required=True)

def read_json():
     with open("data.json") as json_file:
        json_data = json.load(json_file)
        return json_data

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

@app.get('/DHT11_data')
def get_dht11_data():
     """
     Returns the contents of data.json
     """
     return read_json()

@app.route("/")
def index():
    """
    Home page
    """
    return render_template('index.html')

@app.route("/data")
def data():
    """
    Funktion der viser humidity data på hjemmeside
    """
    all_data = get_data(10)
    return render_template('data.html', all_data = all_data)

if __name__ == "__main__":
    app.run()