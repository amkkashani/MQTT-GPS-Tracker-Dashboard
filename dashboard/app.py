import threading
from flask import Flask, render_template, jsonify, make_response, request
import paho.mqtt.client as mqtt
from datetime import datetime
from collections import deque
from flask import request, redirect, url_for, session, flash
from flask_bcrypt import Bcrypt
import json, threading
import csv, os
import json

app = Flask(__name__, static_folder='static')

# === MQTT session ===
mqtt_server_port = 1883
CLIENT_ID = "dashboard_mqtt_hub"

# === Hardcoded credentials ===
USERNAME = "admin"
PASSWORD = "admin"
app.secret_key = "supersecretkey"  # needed for session management

# Get absolute path to the folder this script is in
script_dir = os.path.dirname(os.path.abspath(__file__))

# store messages per device
device_messages = {}
devices_csv_path = {} # IMEI:CSV_Path
added_devices = []
device_locations = {}  # global dict: {imei: {"lat": .., "lon": ..}}

message_history = deque(maxlen=10)

# Globals for MQTT client and current config
client = None
current_config = {"ip": None, "port": None, "topic": None}

# global event and result container
connect_event = threading.Event()
connect_result = {"success": False, "msg": ""}

def create_csv_log(csv_path):
    # === Ensure CSV has a header row ===
    if not os.path.exists(csv_path):
        with open(csv_path, mode="w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["Topic", "Hour", "Minute", "Seccond", "lat", "lon", "Alt", "Battery Level","Lock Status","Temperature","RSSI","MSG Counter","Is MSG Queued"])


def on_connect(c, userdata, flags, rc):
    if rc == 0:
        print("✅ Connected to MQTT Broker!")
        for imei in added_devices:
            topic = f"truck/{imei}/status"
            c.subscribe(topic)
            print(f"🔔 DEBUG: Subscribed to {userdata['topic']}")
            print(f"📡 Subscribed to {topic}")
        connect_result["success"] = True
        connect_result["msg"] = "Connected successfully"
    else:
        print(f"❌ Failed to connect: {rc}")
        connect_result["success"] = False
        connect_result["msg"] = f"Failed to connect, return code {rc}"
    connect_event.set()


def on_message(client, userdata, msg):
    print(f"📩 DEBUG: Received on {msg.topic}: {msg.payload.decode()}")
    
    payload_str = msg.payload.decode().strip()
    IMEI = msg.topic.split("/")[1] if len(msg.topic.split("/")) > 1 else "unknown"

    print("debug: IMEI: ", IMEI)
    
    # Remove braces if present
    if payload_str.startswith("{") and payload_str.endswith("}"):
        payload_str = payload_str[1:-1].strip()

    print("debug: payload: ", payload_str)

    # Split into list by comma
    parts = [p.strip() for p in payload_str.split(",")]

    # Map to named variables
    HH, MM, SS = parts[0], parts[1], parts[2]
    lat, lon, Alt = parts[3], parts[4], parts[5]
    Batt, Lock, Temp = parts[6], parts[7], parts[8]
    RSSI, Cnt, Queued = parts[9], parts[10], parts[11]

    if Lock == 'L': Lock = 'Locked'
    elif Lock == 'U': Lock = 'Unlocked'
    else: Lock = 'Undifiend Lock Msg'

    Batt = int(Batt)
    if Batt == 10:
        Batt = f'100'
    else:
        Batt = f'{Batt*10} ~ {(Batt+1)*10}'

    # Store for webpage log
    message = {
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "topic": msg.topic,
        "HH": HH,
        "MM": MM,
        "SS": SS,
        "lat": float(lat),
        "lon": float(lon),
        "Alt": float(Alt),
        "Batt": Batt,
        "Lock Status": Lock,
        "Temperature": float(Temp),
        "RSSI": int(RSSI),
        "Cnt": int(Cnt),
        "isQueued": int(Queued)
    }

    message_history.appendleft(message)
    if IMEI not in devices_csv_path:
        added_devices.append(IMEI)
        devices_csv_path[IMEI] = os.path.join(script_dir, IMEI + "_history.csv")
        device_messages[IMEI] = deque(maxlen=10)
        # create_csv_log(devices_csv_path[IMEI])
        device_locations[IMEI] = deque(maxlen=5)

    try:
        if "lat" in message and "lon" in message:
            device_locations[IMEI].appendleft({"lat": message["lat"], "lon": message["lon"]})
    except Exception as e:
        print(f"⚠️ Could not parse GPS from {payload_str}: {e}")
    
    device_messages[IMEI].appendleft(message)
    print(f"DEBUG device: IMEA {IMEI}")
    print(f"DEBUG device: added_devices {added_devices}")
    print("debug: message: ", device_messages[IMEI][0])

    # Append to CSV
    # with open(devices_csv_path[IMEI], mode="a", newline="") as file:
    #     writer = csv.writer(file)
    #     writer.writerow([
    #         msg.topic, HH, MM, SS, lat, lon, Alt, Batt, Lock, Temp, RSSI, Cnt, Queued
    #     ])

    
def start_mqtt(ip, port, topic):
    global client, current_config, connect_event, connect_result

    if client is None:
        client = mqtt.Client(CLIENT_ID)
        client.on_connect = on_connect
        client.on_message = on_message

        current_config = {"ip": ip, "port": port, "topic": topic}

        connect_event.clear()
        connect_result = {"success": False, "msg": ""}

        try:
            client.connect(ip, int(port), 60)
            client.loop_start()
        except Exception as e:
            print(f"❌ MQTT connection error: {e}")
            return False, f"Connection error: {e}"

        connected = connect_event.wait(timeout=5)

        if not connected:
            return False, "Connection timed out"

    client.subscribe(topic)
    return True, f"Subscribed to {topic}"

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        if username == USERNAME and password == PASSWORD:
            # Store in session
            session["logged_in"] = True
            if not session.get("logged_in"):
                return redirect(url_for("login"))
            return redirect(url_for("dashboard"))
        else:
            return render_template("login.html", error="Invalid username or password")

    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    gps_point = [35.776215087404076, 51.47687022102022]
    if message_history:
        current_msg = message_history[0]
        if 'lat' in current_msg and 'lon' in current_msg:
            gps_point = [current_msg['lat'], current_msg['lon']]
    return render_template("index.html", gps_point=gps_point)

@app.route('/data/<IMEI>')
def data_for_device(IMEI):
    msgs = list(device_messages.get(IMEI, []))
    response = make_response(jsonify(msgs))
    response.headers['Cache-Control'] = 'no-store'
    return response

@app.route("/device_location/<IMEI>")
def device_location(IMEI):
    if IMEI in device_locations and len(device_locations[IMEI]) > 0:
        latest = device_locations[IMEI][0]  # most recent location
        return jsonify({
            "success": True,
            "lat": latest["lat"],
            "lon": latest["lon"]
        })
    return jsonify({"success": False, "msg": "No location yet"})

@app.route('/connect', methods=['POST'])
def connect():
    data = request.get_json()
    IMEI = data.get("IMEI")

    if not (IMEI):
        return jsonify({"status": "error", "message": "Your device IMEI code is required"}), 400
    
    topic = f'truck/{IMEI}/status'
    # success, msg = start_mqtt('localhost', mqtt_server_port, topic)
    # success, msg = start_mqtt('185.215.244.182', mqtt_server_port, topic)
    success, msg = start_mqtt('94.182.137.200', mqtt_server_port, topic)
    status = "connected" if success else "error"
    return jsonify({"status": status, "message": msg})

@app.route("/publish/<IMEI>/<cmd_type>", methods=["POST"])
def publish_command(IMEI, cmd_type):

    print("DEBUG command: ", IMEI, cmd_type)
    data = request.get_json()
    
    if cmd_type == "lock":
        msg = data.get("command")  # lock_open / lock_close
        topic = f"truck/{IMEI}/command/lock"
    elif cmd_type == "wit":
        msg = data.get("wait_time")  # e.g., "30"
        topic = f"truck/{IMEI}/command/config/wit"
    elif cmd_type == "rfid":
        msg = data.get("rfid")  # e.g., On/OFF
        topic = f"truck/{IMEI}/command/config/rfid"
    else:
        return jsonify({"success": False, "msg": "Unknown command"}), 400
    
    msg = '{' + str(msg) + '}'
    print("DEBUG command: ", msg, topic)
    print("DEBUG command: devices", added_devices)

    # Use your MQTT manager or global client
    try:
        if IMEI in added_devices:  # if using your previous global client
            print("DEBUG command: imei in device messages")
            client.publish(topic, msg)
            return jsonify({"success": True, "msg": f"Published {msg} to {topic}"})
        else:
            print("DEBUG command: imei NOT in device messages")
            return jsonify({"success": False, "msg": "IMEI not connected"}), 400
    except Exception as e:
        print("EXEPTION")
        return jsonify({"success": False, "msg": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, debug=True)

