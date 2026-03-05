# Ramon Solis | March 04, 2026
# Meraki Web Monitor: TV Mode (QR-MFA + Auto-Login + Pulsing Glow + Rotating AP Alerts + SSID Donut)
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
    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, filename)

# --- Environment Discovery & Auto-Login Flow ---
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

API_KEY = get_config("MERAKI_API_KEY", "Meraki API Key")
ORG_ID = get_config("MERAKI_ORG_ID", "Meraki Organization ID")

# --- MFA Setup with QR Code ---
MFA_SECRET = os.getenv("MFA_SECRET")
if not MFA_SECRET:
    print("\n" + "="*50)
    print("🛡️  MFA INITIAL SETUP - SCAN THE QR CODE")
    print("="*50)
    new_secret = pyotp.random_base32()
    totp = pyotp.TOTP(new_secret)
    provisioning_url = totp.provisioning_uri(name="RamonSolis", issuer_name="MerakiMonitor")
    
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
app.permanent_session_lifetime = timedelta(days=30)

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
    return "Switch/Port Data Unavailable"

def format_k(num):
    if num > 9999:
        val = round(num / 1000.0, 1)
        return f"{int(val)}K" if val.is_integer() else f"{val}K"
    return "{:,}".format(num)

# --- Optimized Data Logic ---
def get_monitor_stats():
    try:
        with ThreadPoolExecutor(max_workers=5) as executor:
            f_clients = executor.submit(dashboard.organizations.getOrganizationClientsOverview, ORG_ID)
            f_devs = executor.submit(dashboard.organizations.getOrganizationDevicesStatuses, ORG_ID, total_pages='all')
            f_nets = executor.submit(dashboard.organizations.getOrganizationNetworks, ORG_ID, total_pages='all')
            f_eth = executor.submit(dashboard.wireless.getOrganizationWirelessDevicesEthernetStatuses, ORG_ID, total_pages='all')
            f_ssids = executor.submit(dashboard.organizations.getOrganizationSummaryTopSsidsByUsage, ORG_ID, timespan=28800)
            
            client_overview = f_clients.result()
            devs = f_devs.result()
            nets = f_nets.result()
            
            try: eth_statuses = f_eth.result()
            except Exception: eth_statuses = []

        ap_speeds = {}
        for ap in eth_statuses:
            serial = ap.get('serial')
            ports = ap.get('ports', [])
            for p in ports:
                if p.get('name') == 'eth0':
                    ap_speeds[serial] = p.get('linkNegotiation', {}).get('speed', 0)

        stats = {
            'ap': {'up': 0, 'dn': 0}, 'sw': {'up': 0, 'dn': 0},
            'cam': {'up': 0, 'dn': 0}, 'sen': {'up': 0, 'dn': 0},
            'nets': len(nets), 
            'clients': client_overview.get('counts', {}).get('total', 0), 
            'slow_ap_count': 0,
            'scroller_alerts': [], 
            'ssid_list': [],
            'wireless_total': 0,
            'wireless_total_str': "0",
            'clients_str': "0"
        }

        try:
            ssid_usage = f_ssids.result()
            colors = ['#2ac9c5', '#b34882', '#f1c40f', '#4263eb', '#95a5a6'] 
            
            if ssid_usage:
                ssid_usage = sorted(ssid_usage, key=lambda x: x.get('clients', {}).get('counts', {}).get('total', 0), reverse=True)
                top_ssids, other_ssids = ssid_usage[:4], ssid_usage[4:]
                
                for i, ssid_info in enumerate(top_ssids):
                    name = ssid_info.get('name') or "Unknown"
                    count = ssid_info.get('clients', {}).get('counts', {}).get('total', 0)
                    if count > 0:
                        stats['ssid_list'].append({'name': name, 'count': count, 'color': colors[i]})
                        
                other_count = sum(s.get('clients', {}).get('counts', {}).get('total', 0) for s in other_ssids)
                if other_count > 0:
                    stats['ssid_list'].append({'name': 'Other', 'count': other_count, 'color': colors[4]})
                    
                stats['wireless_total'] = sum(s['count'] for s in stats['ssid_list'])
        except Exception as e:
            print(f"Warning: Could not fetch/parse SSID data: {e}")

        if stats['wireless_total'] == 0:
            stats['wireless_total'] = stats['clients']

        stats['wireless_total_str'] = format_k(stats['wireless_total'])
        stats['clients_str'] = format_k(stats['clients'])

        for d in devs:
            status = d.get('status', '').lower()
            ptype = d.get('productType', '')
            is_up = 1 if status == 'online' else 0
            is_dn = 1 if status == 'offline' else 0

            if ptype == 'wireless':
                stats['ap']['up'] += is_up
                stats['ap']['dn'] += is_dn
                ap_name = d.get('name') or d.get('mac')
                ap_serial = d.get('serial')

                # Check if offline
                if status == 'offline':
                    stats['scroller_alerts'].append(f"🔴 OFFLINE AP: {ap_name} is currently DOWN")
                
                # Check if online but slow
                elif status == 'online':
                    speed = ap_speeds.get(ap_serial)
                    if speed is not None and 0 < speed < 1000:
                        upstream = get_upstream_port(ap_serial) if stats['slow_ap_count'] < 5 else "(Fetch skipped - high volume)"
                        stats['scroller_alerts'].append(f"⚠️ SLOW AP: {ap_name} is running at {speed} Mbps! ({upstream})")
                        stats['slow_ap_count'] += 1

            elif ptype == 'switch': stats['sw']['up'] += is_up; stats['sw']['dn'] += is_dn
            elif ptype == 'camera': stats['cam']['up'] += is_up; stats['cam']['dn'] += is_dn
            elif ptype == 'sensor': stats['sen']['up'] += is_up; stats['sen']['dn'] += is_dn

        return stats
    except Exception as e:
        print(f"Stats Error: {e}")
        return None

# --- UI Template ---
DARK_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="1800"> 
    <title>Meraki Monitor | TV Edition</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
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

        /* General Box Animations - Smooth 5s Pulse */
        .card, .sum-box {
            background: #161b22;
            border-radius: 15px;
            border: 2px solid rgba(0, 174, 239, 0.3);
            animation: glow-pulse-blue 5s ease-in-out infinite;
            display: flex;
            justify-content: space-around;
            align-items: center;
        }

        @keyframes glow-pulse-blue {
            0% { box-shadow: 0 0 5px rgba(0, 174, 239, 0.1); border-color: rgba(0, 174, 239, 0.3); }
            50% { box-shadow: 0 0 25px rgba(0, 174, 239, 0.7); border-color: rgba(0, 174, 239, 0.9); }
            100% { box-shadow: 0 0 5px rgba(0, 174, 239, 0.1); border-color: rgba(0, 174, 239, 0.3); }
        }

        .flashing-box {
            animation: glow-pulse-red 1.5s ease-in-out infinite !important;
        }
        @keyframes glow-pulse-red {
            0% { box-shadow: 0 0 5px rgba(255, 0, 0, 0.2); border-color: rgba(255, 0, 0, 0.3); }
            50% { box-shadow: 0 0 35px rgba(255, 0, 0, 0.9); border-color: rgba(255, 0, 0, 1); }
            100% { box-shadow: 0 0 5px rgba(255, 0, 0, 0.2); border-color: rgba(255, 0, 0, 0.3); }
        }

        .container { max-width: 1450px; margin: 30px auto; padding: 0 20px; }
        .card { height: 220px; margin-bottom: 40px; }
        .group { text-align: center; }
        .up-dn-labels { display: flex; justify-content: center; gap: 35px; font-weight: bold; color: #8b949e; margin-bottom: 8px; }
        .box-container { display: flex; gap: 15px; }
        .stat-box { width: 110px; height: 110px; line-height: 110px; font-size: 36px; font-weight: bold; border: 3px solid #000; border-radius: 10px; }
        .bg-up { background-color: #2ea043; color: white; } .bg-dn { background-color: #ff0000; color: white; }
        
        .summary-wrap { display: flex; justify-content: space-between; gap: 20px; width: 100%; box-sizing: border-box; }
        .sum-box { height: 240px; flex-direction: column; }
        .sum-val { font-size: 55px; font-weight: bold; color: #ffffff; margin: 0; }
        .sum-label { color: #8b949e; font-size: 20px; font-weight: bold; margin-top: 10px; }

        .donut-static {
            width: 140px;
            height: 140px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-bottom: 5px;
            box-sizing: border-box;
        }
        .donut-aqua { border: 12px solid #2ac9c5; }
        .donut-yellow { border: 12px solid #f1c40f; }
        .donut-red-flash {
            border: 12px solid #ff0000;
            animation: pulse-red-donut 1.5s infinite;
        }
        
        @keyframes pulse-red-donut {
            0% { box-shadow: 0 0 10px rgba(255,0,0,0.5), inset 0 0 10px rgba(255,0,0,0.5); }
            50% { box-shadow: 0 0 35px rgba(255,0,0,1), inset 0 0 35px rgba(255,0,0,1); }
            100% { box-shadow: 0 0 10px rgba(255,0,0,0.5), inset 0 0 10px rgba(255,0,0,0.5); }
        }

        /* Scroller Wrapper - Contextual Glow */
        .scroller-wrapper {
            margin: 40px auto;
            width: 70%;
            height: 40px;
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            text-align: center;
            background: #161b22;
        }

        .scroller-ok { 
            border: 2px solid rgba(46, 160, 67, 0.3);
            animation: glow-pulse-green 5s ease-in-out infinite; 
        }
        @keyframes glow-pulse-green {
            0% { box-shadow: 0 0 5px rgba(46, 160, 67, 0.2); border-color: rgba(46, 160, 67, 0.3); }
            50% { box-shadow: 0 0 25px rgba(46, 160, 67, 0.8); border-color: rgba(46, 160, 67, 1); }
            100% { box-shadow: 0 0 5px rgba(46, 160, 67, 0.2); border-color: rgba(46, 160, 67, 0.3); }
        }

        .scroller-alert { 
            border: 2px solid rgba(255, 0, 0, 0.3);
            animation: glow-pulse-red 1.5s ease-in-out infinite; 
        }

        #ap-alert-text { 
            font-size: 22px; 
            font-weight: bold; 
            color: yellow; 
            white-space: nowrap; 
        }
        
        .flash-effect { animation: text-flash 0.6s ease-in-out; }
        @keyframes text-flash {
            0%   { opacity: 1; color: #FFFFFF; }
            25%  { opacity: 0; color: #FFFFFF; }
            50%  { opacity: 1; color: #FFFFFF; }
            75%  { opacity: 0; color: #FFFFFF; }
            100% { opacity: 1; color: yellow; }
        }
    </style>
    <script>
        function updateTime() {
            const now = new Date();
            document.getElementById('live-date').innerText = now.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: '2-digit' });
            document.getElementById('live-clock').innerText = now.toLocaleTimeString('en-US', { hour12: true, hour: '2-digit', minute: '2-digit', second: '2-digit' });
        }
        setInterval(updateTime, 1000);

        document.addEventListener('DOMContentLoaded', () => {
            const scrollerAlerts = {{ stats.scroller_alerts | tojson | safe }};
            const alertTextElement = document.getElementById('ap-alert-text');
            if (scrollerAlerts && scrollerAlerts.length > 0) {
                let currentIndex = 0;
                function rotateAlerts() {
                    alertTextElement.classList.remove('flash-effect');
                    void alertTextElement.offsetWidth; 
                    alertTextElement.innerText = scrollerAlerts[currentIndex];
                    // Make text red if it's an offline alert, otherwise keep it yellow
                    if (scrollerAlerts[currentIndex].includes("OFFLINE")) {
                        alertTextElement.style.color = "#ff4a4a";
                    } else {
                        alertTextElement.style.color = "yellow";
                    }
                    alertTextElement.classList.add('flash-effect');
                    currentIndex = (currentIndex + 1) % scrollerAlerts.length;
                }
                rotateAlerts();
                setInterval(rotateAlerts, 3500); 
            }

            const ssidData = {{ stats.ssid_list | tojson | safe }};
            if (ssidData && ssidData.length > 0) {
                const ctx = document.getElementById('ssidChart').getContext('2d');
                new Chart(ctx, {
                    type: 'doughnut',
                    data: {
                        labels: ssidData.map(d => d.name),
                        datasets: [{
                            data: ssidData.map(d => d.count),
                            backgroundColor: ssidData.map(d => d.color),
                            borderWidth: 0,
                            cutout: '75%'
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { display: false }, tooltip: { enabled: true } },
                        animation: { duration: 1500, animateScale: true }
                    }
                });
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
            
            <div class="group">
                <div class="up-dn-labels"><span>ONLINE</span><span>OFFLINE</span></div>
                <div class="box-container">
                    <div class="stat-box bg-up">{{ stats.ap.up }}</div>
                    <div class="stat-box bg-dn">{{ stats.ap.dn }}</div>
                </div>
                <div style="margin-top:15px;font-weight:bold;font-size:1.2em;color:#58a6ff;">Access Points</div>
            </div>

            {% for label, d in [('Switches', stats.sw), ('Cameras', stats.cam), ('Sensors', stats.sen)] %}
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
            <div class="sum-box" style="flex: 3; justify-content: center;">
                <div class="donut-static donut-aqua">
                    <div class="sum-val">{{ stats.nets }}</div>
                </div>
                <div class="sum-label">Total Meraki Networks</div>
            </div>

            <div class="sum-box" style="flex: 4; padding: 20px; box-sizing: border-box; justify-content: flex-start; align-items: flex-start;">
                <div style="width: 100%; display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                    <span style="color: #e1e1e1; font-weight: bold; font-size: 16px;">Wireless Client by SSID | Last 8 Hours</span>
                    <span style="color: #8b949e; cursor: pointer;">⋮</span>
                </div>
                
                {% if stats.ssid_list %}
                <div style="display: flex; align-items: center; justify-content: space-evenly; width: 100%;">
                    <div style="position: relative; width: 150px; height: 150px;">
                        <canvas id="ssidChart"></canvas>
                        <div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -45%); font-size: 36px; font-weight: bold; color: white;">
                            {{ stats.wireless_total_str }}
                        </div>
                    </div>
                    <div style="display: flex; flex-direction: column; gap: 10px; width: 150px;">
                        {% for ssid in stats.ssid_list %}
                        <div style="display: flex; align-items: center; justify-content: space-between; font-size: 14px; font-weight: bold; color: #e1e1e1;">
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <div style="width: 10px; height: 10px; border-radius: 50%; background-color: {{ ssid.color }};"></div>
                                <span style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 90px;" title="{{ ssid.name }}">{{ ssid.name }}</span>
                            </div>
                            <span>{{ "{:,}".format(ssid.count) }}</span>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                {% else %}
                <div style="display: flex; align-items: center; justify-content: center; height: 100%; width: 100%; color: #8b949e; font-size: 18px;">
                    <div style="text-align: center;">
                        <div style="font-size: 40px; color: white; margin-bottom: 5px;">{{ stats.clients_str }}</div>
                        Total Clients<br>(SSID Breakdown Unavailable)
                    </div>
                </div>
                {% endif %}
            </div>
            
            <div class="sum-box {% if stats.slow_ap_count > 0 %} flashing-box {% endif %}" style="flex: 3; justify-content: center;">
                <div class="donut-static {% if stats.slow_ap_count > 0 %} donut-red-flash {% else %} donut-yellow {% endif %}">
                    <div style="font-size: 55px; font-weight: bold; color: white;">{{ stats.slow_ap_count }}</div>
                </div>
                <div class="sum-label">AP Running under < 1 Gbps Speed</div>
            </div>
        </div>

        <div class="scroller-wrapper {% if stats.scroller_alerts %} scroller-alert {% else %} scroller-ok {% endif %}">
            {% if stats.scroller_alerts %}
                <div id="ap-alert-text"></div>
            {% else %}
                <div id="ap-alert-text" style="color: #4DE81A;">
                    All Access Points are ONLINE and negotiating at > 1 Gbps+ ✓
                </div>
            {% endif %}
        </div>
    </div>
</body>
</html>
"""

# --- Web Routes ---
@app.route('/')
def index():
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
            session.permanent = True
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
