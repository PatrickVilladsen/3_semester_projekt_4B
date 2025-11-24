from flask import Flask, render_template

app = Flask(__name__)

dummy_json = '{"Temperature":23, "Humidity": 10}'

@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    app.run()