from flask import Flask, render_template
import json

app = Flask(__name__)

dummy_json = '{"Temperature":23, "Humidity": 10}'

def read_humidity(json_data):
    data = json.loads(json_data)
    return data["Humidity"]

@app.route("/")
def index():
    return render_template('index.html')

@app.route("/data")
def data():
    humidity_data = read_humidity(dummy_json)
    return render_template('data.html', humidity_data = humidity_data)

if __name__ == "__main__":
    app.run()