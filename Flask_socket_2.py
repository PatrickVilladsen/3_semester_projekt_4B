from flask import Flask 
from flask import render_template 
from flask_socketio import 
SocketIO, emit
import pigpio 

BUTTON_GPIO_RUN = none 

pi = pigpio.pi()
app = Flask(__name__)
socketio = SocketIO(app)

def tilstand():
    button_state = pi.read(BUTTON_GPIO_PIN)
    socketio.emit('button_state'. button_state)

@socketio.on('connect')
def connect():
    tilstand()

def cbf(gpio, level, tick):

pi.callback(BUTTON_GPIO_RUN,, pigpio.EITHER_EDGE, cbf)

@app.route('/')
def index():
    return render_template('Øvelse2_socket_knap.html')
tilstand=pi.read(BUTTON_GPIO_RUN))

if __name__ == '__main__':
    socketio.run(app, host="0.0.0.0", debug=True )