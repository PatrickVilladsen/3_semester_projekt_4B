from flask import Flask, render_template, request 
from flask_socketio import SocketIO, emit 
import requests 
import datetime 

#Create the flask appplication object 
app = Flask(__name__)

#Insert the api key from our own api
api_key = '??????'

@app.route("/")
def test() : 
    return render_template('index.html', utc_dt=datetime.datetime.utcnow())
#app.run starts the flask server 
if __name__ == "__main__": 
    app.run(debug=True) 




    