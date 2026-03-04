# Ramon Solis | March 04, 2026
# Meraki Web Monitor: TV Mode (QR-MFA + Auto-Login + Snake Border + Rotating AP Scroller)
# -----------------------------------------------------------------------
import os
import sys
import pyotp
import meraki
import webbrowser
import qrcode
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template_string, request, redirect, url_for, session
from datetime import datetime, timedelta
from dotenv import load_dotenv, set_key

# --- Path Handling for Compiled EXE ---
def get_config_path(filename):
    # If the application is run as a bundled executable
    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    # If the application is run as a normal Python script
    else:
        base_path = os.path.abspath(".")
    
    return os.path.join(base_path, filename)

# --- 1 & 2: Environment Discovery & Auto-Login Flow ---
env_path = get_config_path(".env")
if not os.path.exists(env_path):
    with open(env_path, "w") as f: f.write("")

load_dotenv(env_path)

def get_config(key, prompt):
    value = os.getenv(key)
    if not value or not value.strip():
        print(f"\n[?] Configuration Missing: {key}")
        value = input(f"Enter {prompt}: ").strip()
        set_key(env_path, key, value)
    return value

# Auto-login if present, otherwise ask in terminal
API_KEY = get_config("MERAKI_API_KEY", "Meraki API Key")
ORG_ID = get_config("MERAKI_ORG_ID", "Meraki Organization ID")

# --- 3: MFA Setup with QR Code ---
MFA_SECRET = os.getenv("MFA_SECRET")
if not MFA_SECRET:
    print("\n" + "="*50)
    print("🛡️  MFA INITIAL SETUP - SCAN THE QR CODE")
    print("="*50)
    new_secret = pyotp.random_base32()
    totp = pyotp.TOTP(new_secret)
    provisioning_url = totp.provisioning_uri(name="RamonSolis", issuer_name="MerakiMonitor")
    
    # Generate and show QR Code
    qr = qrcode.make(provisioning_url)
    qr.show() 
    
    print(f"Manual Key: {new_secret}")
    while True:
        confirm_code = input("\nEnter 6-digit code from App to verify: ").strip()
        if totp.verify(confirm_code):
            set_key(env_path, "MFA_SECRET", new_secret)
            MFA_SECRET = new_secret
            print("[+] MFA Linked! Launching Dashboard...")
            break
        print("[!] Invalid code. Please try again.")

# --- API & Flask Initialization ---
APP_PORT = int(os.getenv("PORT", 8080))
dashboard = meraki.DashboardAPI(API_KEY, suppress_logging=True)

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.permanent_session_lifetime = timedelta(days=30) # Persist session for 30 days

# Helper to fetch switch port if possible
def get_upstream_port(serial):
    try:
        lldp = dashboard.devices.getDeviceLldpCdp(serial)
        if 'ports' in lldp and 'eth0' in lldp['ports']:
            port_info = lldp['ports']['eth0'].get('cdp', {}) or lldp['ports']['eth0'].get('lldp', {})
            switch = port_info.get('deviceId', port_info.get('systemName', 'Unknown Switch'))
            port = port_info.get('portId', 'Unknown Port')
            return f"Switch: {switch} | Port: {port}"
    except:
        pass
    return "Switch/Port Data Unavailable (Offline)"

# --- Optimized Data Logic with Fixed Stat Boxes ---
def get_monitor_stats():
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            f_clients = executor.submit(dashboard.organizations.getOrganizationClientsOverview, ORG_ID)
            f_devs = executor.submit(dashboard.organizations.getOrganizationDevicesStatuses, ORG_ID, total_pages='all')
            f_nets = executor.submit(dashboard.organizations.getOrganizationNetworks, ORG_ID, total_pages='all')
            
            client_overview = f_clients.result()
            devs = f_devs.result()
            nets = f_nets.result()

        stats = {
            'ap': {'up': 0, 'dn': 0}, 'sw': {'up': 0, 'dn': 0},
            'cam': {'up': 0, 'dn': 0}, 'sen': {'up': 0, 'dn': 0},
            'nets': len(nets), 
            'clients': client_overview.get('counts', {}).get('total', 0), 
            'issues': 0,
            'down_aps': [] # List for the scroller
        }

        issue_statuses = {'offline', 'alerting', 'dormant', 'unreachable'}
        down_ap_count = 0

        for d in devs:
            status = d.get('status', '').lower()
            ptype = d.get('productType', '')
            
            is_up = 1 if status == 'online' else 0
            is_dn = 1 if status == 'offline' else 0

            if ptype == 'wireless':
                stats['ap']['up'] += is_up
                stats['ap']['dn'] += is_dn
                if status in issue_statuses:
                    stats['issues'] += 1
                
                # Collect DOWN AP data
                if status == 'offline':
                    ap_name = d.get('name') or d.get('mac')
                    ap_serial = d.get('serial')
                    
                    # Fetch port info (limit to first 5 to prevent massive API delays during outages)
                    if down_ap_count < 5:
                        upstream = get_upstream_port(ap_serial)
                    else:
                        upstream = "Switch/Port Data Delayed (High Outage Volume)"
                    
                    stats['down_aps'].append(f"{ap_name} + {upstream}")
                    down_ap_count += 1

            elif ptype == 'switch': stats['sw']['up'] += is_up; stats['sw']['dn'] += is_dn
            elif ptype == 'camera': stats['cam']['up'] += is_up; stats['cam']['dn'] += is_dn
            elif ptype == 'sensor': stats['sen']['up'] += is_up; stats['sen']['dn'] += is_dn

        return stats
    except Exception as e:
        print(f"Stats Error: {e}")
        return None

# --- UI Template (Snake Border + Dark Mode + Rotating AP Flasher) ---
DARK_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="1800"> <title>Meraki Monitor | TV Edition</title>
    <style>
        body { background-color: #0b0e14; color: #e1e1e1; font-family: 'Segoe UI', sans-serif; margin: 0; overflow: hidden; }
        .header { background: #1c2331; padding: 15px 40px; border-bottom: 3px solid #2980b9; display: flex; justify-content: space-between; align-items: center; }
        h1 { margin: 0; color: #3498db; font-size: 2.2em; text-transform: uppercase; letter-spacing: 2px; }
        
        .header-clock-container { text-align: right; margin-right: 20px; display: flex; flex-direction: column; justify-content: center; }
        #live-date { color: #95a5a6; font-size: 1.1em; font-weight: bold; }
        #live-clock { color: #f1c40f; font-size: 1.8em; font-family: 'Consolas', monospace; font-weight: bold; }
        
        .status-pill { background: #000; border: 2px solid #2ea043; border-radius: 50px; padding: 5px 25px; display: flex; align-items: center; gap: 12px; box-shadow: 0 0 15px rgba(46, 160, 67, 0.4); }
        .glow-dot { width: 14px; height: 14px; background-color: #2ea043; border-radius: 50%; box-shadow: 0 0 10px #2ea043; animation: pulse 2s infinite; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.3; } 100% { opacity: 1; } }

        /* Snake Border Animation */
        .card, .sum-box {
            position: relative;
            background: #161b22;
            border-radius: 15px;
            overflow: hidden;
            display: flex;
            justify-content: space-around;
            align-items: center;
            z-index: 0;
        }
        .card::before, .sum-box::before {
            content: '';
            position: absolute;
            width: 150%; height: 150%;
            background: conic-gradient(transparent, #00AEEF, transparent, transparent);
            animation: rotate-border 5s linear infinite;
            z-index: -2;
        }
        .card::after, .sum-box::after {
            content: '';
            position: absolute;
            inset: 4px;
            background: #161b22;
            border-radius: 12px;
            z-index: -1;
        }
        @keyframes rotate-border { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

        /* Flashing Red Snake Border for Issues */
        .flashing-box::before {
            background: conic-gradient(transparent, #ff0000, transparent, transparent) !important;
            animation: rotate-border 1.5s linear infinite !important;
        }

        .container { max-width: 1450px; margin: 30px auto; padding: 0 20px; }
        .card { height: 220px; margin-bottom: 40px; }
        .group { text-align: center; position: relative; z-index: 2; }
        .up-dn-labels { display: flex; justify-content: center; gap: 50px; font-weight: bold; color: #8b949e; margin-bottom: 8px; }
        .box-container { display: flex; gap: 15px; }
        .stat-box { width: 110px; height: 110px; line-height: 110px; font-size: 36px; font-weight: bold; border: 3px solid #000; border-radius: 10px; }
        .bg-up { background-color: #2ea043; color: white; } .bg-dn { background-color: #ff0000; color: white; }
        
        .summary-wrap { display: flex; justify-content: center; gap: 30px; }
        .sum-box { width: 380px; height: 240px; flex-direction: column; }
        .sum-val { font-size: 70px; font-weight: bold; color: #2ea043; position: relative; z-index: 2; }
        .sum-alert { font-size: 70px; font-weight: bold; color: #fff; background: #ff0000; border-radius: 12px; padding: 10px 40px; display: inline-block; position: relative; z-index: 2; }
        .sum-label { color: #8b949e; font-size: 22px; font-weight: bold; margin-top: 10px; position: relative; z-index: 2; }

        /* --- New Single AP Flasher Box --- */
        .scroller-wrapper {
            margin: 40px auto;
            width: 70%;
            height: 40px;
            background: #ff0000; 
            border: 5px solid #00FFFF; 
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 0 20px rgba(46, 160, 67, 0.3);
            text-align: center;
        }
        
        #ap-alert-text {
            font-size: 22px;
            font-weight: bold;
            color: yellow;
            white-space: nowrap;
        }

        /* Flash animation: Blinks red/invisible quickly, then settles to solid black */
        .flash-effect {
            animation: text-flash 0.6s ease-in-out;
        }
        
        @keyframes text-flash {
            0%   { opacity: 1; color: #FFFFFF; }
            25%  { opacity: 0; color: #FFFFFF; }
            50%  { opacity: 1; color: #FFFFFF; }
            75%  { opacity: 0; color: #FFFFFF; }
            100% { opacity: 1; color: black; }
        }

    </style>
    <script>
        function updateTime() {
            const now = new Date();
            document.getElementById('live-date').innerText = now.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: '2-digit' });
            document.getElementById('live-clock').innerText = now.toLocaleTimeString('en-US', { hour12: true, hour: '2-digit', minute: '2-digit', second: '2-digit' });
        }
        setInterval(updateTime, 1000);

        // --- JavaScript Logic for Rotating Down APs ---
        document.addEventListener('DOMContentLoaded', () => {
            const downAps = {{ stats.down_aps | tojson | safe }};
            const alertTextElement = document.getElementById('ap-alert-text');
            
            if (downAps && downAps.length > 0) {
                let currentIndex = 0;

                function rotateAP() {
                    alertTextElement.classList.remove('flash-effect');
                    void alertTextElement.offsetWidth; // Reflow
                    alertTextElement.innerText = "⚠️ " + downAps[currentIndex];
                    alertTextElement.classList.add('flash-effect');
                    currentIndex = (currentIndex + 1) % downAps.length;
                }

                rotateAP();
                setInterval(rotateAP, 3000);
            }
        });
    </script>
</head>
<body onload="updateTime()">
    <div class="header">
        <div><h1>Meraki Dashboard | Live Monitor</h1><div style="color:#95a5a6">Ramon Solis © 2026 | Auto-Refresh Every 30 Minutes</div></div>
        <div class="header-clock-container"><div id="live-date"></div><div id="live-clock">00:00:00 AM</div></div>
        <div class="status-pill"><div class="glow-dot"></div><span style="color:#2ea043;font-weight:bold;font-size:1.3em;">ON</span></div>
    </div>
    
    <div class="container">
        <div class="card">
            {% for label, d in [('Access Points', stats.ap), ('Switches', stats.sw), ('Cameras', stats.cam), ('Sensors', stats.sen)] %}
            <div class="group">
                <div class="up-dn-labels"><span>UP</span><span>DOWN</span></div>
                <div class="box-container">
                    <div class="stat-box bg-up">{{ d.up }}</div>
                    <div class="stat-box bg-dn">{{ d.dn }}</div>
                </div>
                <div style="margin-top:15px;font-weight:bold;font-size:1.2em;color:#58a6ff;">{{ label }}</div>
            </div>
            {% endfor %}
        </div>

        <div class="summary-wrap">
            <div class="sum-box">
                <div class="sum-val">{{ stats.nets }}</div>
                <div class="sum-label">Total Meraki Networks</div>
            </div>
            <div class="sum-box">
                <div class="sum-val">{{ "{:,}".format(stats.clients) }}</div>
                <div class="sum-label">Total Unique Clients</div>
            </div>
            <div class="sum-box {% if stats.issues > 0 %} flashing-box {% endif %}">
                <div class="sum-alert">{{ stats.issues }}</div>
                <div class="sum-label">Meraki AP with Issues</div>
            </div>
        </div>

        <div class="scroller-wrapper" {% if not stats.down_aps %} style="background-color: #4DE81A; border-color: #4DE81A;" {% endif %}>
            {% if stats.down_aps %}
                <div id="ap-alert-text"></div>
            {% else %}
                <div id="ap-alert-text" style="color: white;">
                    All Access Points are Online ✓
                </div>
            {% endif %}
        </div>
    </div> </body>
</html>
"""

# --- Web Routes ---
@app.route('/')
def index():
    # Force MFA check first
    if not session.get('auth'): return redirect(url_for('login'))
    
    stats = get_monitor_stats()
    if not stats:
        return "Error fetching data from Meraki API. Check terminal.", 500
        
    return render_template_string(DARK_TEMPLATE, stats=stats)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error_msg = ""
    if request.method == 'POST':
        if pyotp.TOTP(MFA_SECRET).verify(request.form.get('code').strip()):
            session.permanent = True # Cookie survives browser refreshes
            session['auth'] = True
            return redirect(url_for('index'))
        else:
            error_msg = "<p style='color:#ff0000; font-weight:bold;'>Invalid Code. Please try again.</p>"
            
    return f'''<body style="background:#0b0e14;color:white;text-align:center;padding-top:100px;font-family:sans-serif;">
           <h2>🛡️ Meraki Dashboard | TV Mode Access</h2>
           <form method="post">
           <p>Enter MFA Code:</p>
           {error_msg}
           <input name="code" type="text" autocomplete="off" style="font-size:30px;width:160px;text-align:center;background:#161b22;color:white;border:1px solid #30363d;" autofocus><br><br>
           <button type="submit" style="padding:10px 25px;font-weight:bold;cursor:pointer;background:#2ea043;color:white;border:none;border-radius:5px;">Unlock Monitor</button>
           </form></body>'''

if __name__ == '__main__':
    if not os.environ.get("WERKZEUG_RUN_MAIN"):
        webbrowser.open(f"http://127.0.0.1:{APP_PORT}")
    app.run(host='0.0.0.0', port=APP_PORT, debug=False)
