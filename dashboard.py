import subprocess
import os
import signal
from flask import Flask, render_template_string, redirect, url_for

app = Flask(__name__)

# Globale Variable, um den Bot-Prozess zu speichern
bot_process = None
LOG_FILE = "bot.log" # Wir nehmen an, dein Bot schreibt in bot.log

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Rov.E Bot Control Panel</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #1a1a1a; color: #ffffff; text-align: center; padding: 50px; }
        .container { max-width: 600px; margin: 0 auto; background: #2a2a2a; padding: 30px; border-radius: 15px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
        h1 { color: #00adb5; }
        .status { font-size: 1.2rem; margin: 20px 0; padding: 10px; border-radius: 5px; }
        .online { background-color: #2e7d32; color: #fff; }
        .offline { background-color: #c62828; color: #fff; }
        .btn { display: inline-block; padding: 12px 24px; font-size: 1rem; font-weight: bold; margin: 10px; cursor: pointer; border: none; border-radius: 5px; text-decoration: none; transition: 0.3s; }
        .btn-start { background-color: #00adb5; color: white; }
        .btn-start:hover { background-color: #007a82; }
        .btn-stop { background-color: #ff5722; color: white; }
        .btn-stop:hover { background-color: #d84315; }
        .logs { text-align: left; background: #111; padding: 15px; border-radius: 5px; height: 200px; overflow-y: scroll; font-family: monospace; font-size: 0.9rem; color: #393e46; border: 1px solid #393e46; white-space: pre-wrap; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔮 Rov.E Bot Control Panel</h1>
        <p>Steuere deinen Telegram-Bot bequem über den Browser.</p>
        
        <div class="status {{ 'online' if is_running else 'offline' }}">
            Status: <strong>{{ 'ONLINE (Läuft)' if is_running else 'OFFLINE (Gestoppt)' }}</strong>
        </div>

        <a href="/start" class="btn btn-start">🚀 Bot Starten</a>
        <a href="/stop" class="btn btn-stop">🛑 Bot Stoppen</a>
        <a href="/" class="btn" style="background:#393e46; color:white;">🔄 Aktualisieren</a>

        <h3>📋 Neueste Log-Einträge:</h3>
        <div class="logs">{{ logs }}</div>
    </div>
</body>
</html>
"""

def check_bot_status():
    global bot_process
    if bot_process is None:
        return False
    # Prüfen, ob der Prozess noch aktiv ist
    return bot_process.poll() is None

def get_latest_logs():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
            return "".join(lines[-20:]) # Die letzten 20 Zeilen anzeigen
    return "Keine Log-Datei gefunden. Der Bot muss erst laufen."

@app.route('/')
def index():
    is_running = check_bot_status()
    logs = get_latest_logs()
    return render_template_string(HTML_TEMPLATE, is_running=is_running, logs=logs)

@app.route('/start')
def start_bot():
    global bot_process
    if os.path.exists("bot.py"):
        # Falls er schon läuft, nicht doppelt starten
        if not check_bot_status():
            # Wir starten den Bot und leiten Fehlermeldungen in die log-Datei um
            with open(LOG_FILE, "a", encoding="utf-8") as log:
                bot_process = subprocess.Popen(["python3", "bot.py"], stdout=log, stderr=log)
    return redirect(url_for('index'))

@app.route('/stop')
def stop_bot():
    global bot_process
    if check_bot_status():
        # Schickt das Signal zum sauberen Herunterfahren (Ctrl+C imitiert)
        bot_process.send_signal(signal.SIGINT)
        bot_process.wait()
        bot_process = None
    return redirect(url_for('index'))

if __name__ == '__main__':
    # Läuft auf Port 5000. 
    # Wenn du auf einem externen Server bist, ändere '127.0.0.1' zu '0.0.0.0'
    app.run(host='0.0.0.0', port=5000, debug=True)
