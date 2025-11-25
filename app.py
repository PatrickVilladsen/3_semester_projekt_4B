from flask import Flask, render_template
import json
import sqlite3
from time import sleep

app = Flask(__name__)

dummy_json = '{"Temperature":23, "Humidity": 10}'

def read_humidity(json_data):
    data = json.loads(json_data)
    return data["Humidity"]

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