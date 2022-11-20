
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/")
def hello():
    return "Hello World!"

@app.route("/update", methods=['POST'])
def update():
    content = request.json
    print(content)
    return jsonify({})

if __name__ == "__main__":
    app.run(debug=True)