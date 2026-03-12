# Ramon Solis | March 04, 2026
# Meraki Web Monitor: TV Mode (QR-MFA + Auto-Login + Pulsing Glow + Rotating AP Alerts + SSID Donut)
# -----------------------------------------------------------------------
import os
import sys
import pyotp
import meraki
import webbrowser
import qrcode
import re
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

def format_usage(kb_val):
    if not kb_val: return "0 GB"
    try:
        kb = float(kb_val)
        mb = kb / 1024
        gb = mb / 1024
        tb = gb / 1024
        if tb >= 1: return f"{tb:.2f} TB"
        if gb >= 1: return f"{gb:.2f} GB"
        if mb >= 1: return f"{mb:.2f} MB"
        return f"{kb:.2f} KB"
    except:
        return "0 GB"

def parse_speed_value(p):
    raw = p.get('linkNegotiation', {}).get('speed') or p.get('speed')
    if raw is None: return 0
    if isinstance(raw, (int, float)): return int(raw)
    
    s = str(raw).lower()
    match = re.search(r'\b(\d+)\b', s)
    if match:
        val = int(match.group(1))
        if 'g' in s: val *= 1000
        return val
    return 0

def extract_speed(ports):
    # Always prefer eth0 as the primary uplink if present
    for p in ports:
        if p.get('name') == 'eth0':
            val = parse_speed_value(p)
            if val > 0: return val
            
    # Fallback to the first port that returns a valid speed
    for p in ports:
        val = parse_speed_value(p)
        if val > 0: return val
    return 0

# --- Optimized Data Logic ---
def get_monitor_stats():
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            f_clients = executor.submit(dashboard.organizations.getOrganizationClientsOverview, ORG_ID, timespan=28800)
            f_devs = executor.submit(dashboard.organizations.getOrganizationDevicesStatuses, ORG_ID, total_pages='all')
            f_nets = executor.submit(dashboard.organizations.getOrganizationNetworks, ORG_ID, total_pages='all')
            f_eth = executor.submit(dashboard.wireless.getOrganizationWirelessDevicesEthernetStatuses, ORG_ID, total_pages='all')
            f_ssids = executor.submit(dashboard.organizations.getOrganizationSummaryTopSsidsByUsage, ORG_ID, timespan=28800)
            
            f_top_clients = executor.submit(dashboard.organizations.getOrganizationSummaryTopClientsByUsage, ORG_ID, timespan=28800)
            f_top_devices = executor.submit(dashboard.organizations.getOrganizationSummaryTopDevicesByUsage, ORG_ID, timespan=28800)
            f_top_models = executor.submit(dashboard.organizations.getOrganizationSummaryTopDevicesModelsByUsage, ORG_ID, timespan=28800)
            
            client_overview = f_clients.result()
            devs = f_devs.result()
            nets = f_nets.result()
            
            try: eth_statuses = f_eth.result()
            except Exception: eth_statuses = []

        # Map speeds using the new ultra-resilient parser
        ap_speeds = {}
        for ap in eth_statuses:
            serial = ap.get('serial')
            speed = extract_speed(ap.get('ports', []))
            if speed > 0:
                ap_speeds[serial] = speed

        stats = {
            'ap': {'online': 0, 'offline': 0, 'alerting': 0, 'repeater': 0}, 
            'sw': {'up': 0, 'dn': 0},
            'cam': {'up': 0, 'dn': 0}, 
            'sen': {'up': 0, 'dn': 0},
            'nets': len(nets), 
            'clients': client_overview.get('counts', {}).get('total', 0), 
            'slow_ap_count': 0,
            'scroller_alerts': [], 
            'ssid_list': [],
            'wireless_total': 0,
            'wireless_total_str': "0",
            'clients_str': "0",
            'top_clients': [],
            'top_ap_clients': [],
            'top_ap_usage': [],
            'top_models': [],
            'total_data': "0 GB"  
        }

        # Dynamically pull total data transferred
        try:
            total_usage_kb = client_overview.get('usage', {}).get('total', 0)
            if total_usage_kb:
                stats['total_data'] = format_usage(total_usage_kb)
            else:
                fallback_kb = sum(m.get('usage', {}).get('total', 0) for m in f_top_models.result())
                stats['total_data'] = format_usage(fallback_kb) if fallback_kb > 0 else "0 GB"
        except Exception:
            pass

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

        try: 
            top_clients_data = f_top_clients.result()
            for c in top_clients_data[:10]:
                usage = c.get('usage', {}).get('total', 0)
                stats['top_clients'].append({'name': c.get('name') or c.get('mac') or 'Unknown', 'usage': format_usage(usage)})
        except Exception: pass

        try: 
            top_devices_data = f_top_devices.result()
            top_by_clients = sorted(top_devices_data, key=lambda x: x.get('clients', {}).get('counts', {}).get('total', 0), reverse=True)
            for d in top_by_clients[:10]:
                stats['top_ap_clients'].append({'name': d.get('name') or d.get('mac') or 'Unknown', 'clients': d.get('clients', {}).get('counts', {}).get('total', 0)})
            
            top_by_usage = sorted(top_devices_data, key=lambda x: x.get('usage', {}).get('total', 0), reverse=True)
            for d in top_by_usage[:10]:
                stats['top_ap_usage'].append({'name': d.get('name') or d.get('mac') or 'Unknown', 'usage': format_usage(d.get('usage', {}).get('total', 0))})
        except Exception: pass

        model_inventory = {}
        for d in devs:
            mod = d.get('model', 'Unknown')
            if mod not in model_inventory:
                model_inventory[mod] = {'count': 0, 'usage': 0}
            model_inventory[mod]['count'] += 1
            
        try:
            for m in f_top_models.result():
                mod = m.get('model', 'Unknown')
                if mod in model_inventory:
                    model_inventory[mod]['usage'] = m.get('usage', {}).get('total', 0)
        except Exception: pass

        sorted_models = sorted(model_inventory.items(), key=lambda x: x[1]['count'], reverse=True)
        for mod, data in sorted_models[:10]:
            stats['top_models'].append({
                'model': mod,
                'count': data['count'],
                'usage': format_usage(data['usage'])
            })

        # Process Device Statuses & Scroller Alerts
        for d in devs:
            status = d.get('status', '').lower()
            ptype = d.get('productType', '')
            name_or_mac = d.get('name') or d.get('mac')
            serial = d.get('serial')

            # Global Scroller Alerts
            if status == 'offline':
                stats['scroller_alerts'].append(f"🔴 OFFLINE {ptype.upper()}: {name_or_mac} is currently DOWN")
            elif status == 'alerting':
                stats['scroller_alerts'].append(f"⚠️ ALERTING {ptype.upper()}: {name_or_mac} needs attention")

            if ptype == 'wireless':
                if status == 'offline': 
                    stats['ap']['offline'] += 1
                else:
                    speed = ap_speeds.get(serial)
                    is_mesh = (speed is None or speed == 0)

                    # Tally mutually exclusive top row UI boxes
                    if status == 'alerting':
                        stats['ap']['alerting'] += 1
                    elif is_mesh:
                        stats['ap']['repeater'] += 1
                    else:
                        stats['ap']['online'] += 1
                    
                    # Uncoupled scroller alerts for Mesh and Slow Speeds
                    if is_mesh:
                        stats['scroller_alerts'].append(f"📡 MESH AP: {name_or_mac} is operating as a Repeater")
                        
                    if speed is not None and 0 < speed < 1000:
                        upstream = get_upstream_port(serial) if stats['slow_ap_count'] < 10 else "(Fetch skipped)"
                        stats['scroller_alerts'].append(f"⚠️ SLOW AP: {name_or_mac} is running at {speed} Mbps! ({upstream})")
                        stats['slow_ap_count'] += 1

            elif ptype == 'switch': 
                stats['sw']['up'] += (1 if status in ['online', 'alerting'] else 0)
                stats['sw']['dn'] += (1 if status == 'offline' else 0)
            elif ptype == 'camera': 
                stats['cam']['up'] += (1 if status in ['online', 'alerting'] else 0)
                stats['cam']['dn'] += (1 if status == 'offline' else 0)
            elif ptype == 'sensor': 
                stats['sen']['up'] += (1 if status in ['online', 'alerting'] else 0)
                stats['sen']['dn'] += (1 if status == 'offline' else 0)

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
    <meta http-equiv="refresh" content="600"> 
    <title>Meraki Monitor | TV Edition</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { background-color: #0b0e14; color: #e1e1e1; font-family: 'Segoe UI', sans-serif; margin: 0; padding: 0; overflow: hidden; height: 100vh; display: flex; flex-direction: column; }
        
        .header { background: #1c2331; padding: 10px 40px; border-bottom: 3px solid #2980b9; display: flex; justify-content: space-between; align-items: center; flex-shrink: 0; }
        h1 { margin: 0; color: #3498db; font-size: 2em; text-transform: uppercase; letter-spacing: 2px; }
        .header-sub { color: #95a5a6; font-size: 0.9em; }
        
        .header-clock-container { text-align: right; margin-right: 20px; display: flex; flex-direction: column; justify-content: center; }
        #live-date { color: #95a5a6; font-size: 1em; font-weight: bold; }
        #live-clock { color: #f1c40f; font-size: 1.6em; font-family: 'Consolas', monospace; font-weight: bold; }
        
        .status-pill { background: #000; border: 2px solid #2ea043; border-radius: 50px; padding: 5px 25px; display: flex; align-items: center; gap: 12px; box-shadow: 0 0 15px rgba(46, 160, 67, 0.4); }
        .glow-dot { width: 14px; height: 14px; background-color: #2ea043; border-radius: 50%; box-shadow: 0 0 10px #2ea043; animation: pulse 2s infinite; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.3; } 100% { opacity: 1; } }

        /* General Box Animations */
        .card, .sum-box {
            background: #161b22;
            border-radius: 12px;
            border: 2px solid rgba(0, 174, 239, 0.3);
            animation: glow-pulse-blue 5s ease-in-out infinite;
            display: flex;
            justify-content: space-around;
            align-items: center;
        }

        @keyframes glow-pulse-blue {
            0% { box-shadow: 0 0 5px rgba(0, 174, 239, 0.1); border-color: rgba(0, 174, 239, 0.3); }
            50% { box-shadow: 0 0 20px rgba(0, 174, 239, 0.6); border-color: rgba(0, 174, 239, 0.8); }
            100% { box-shadow: 0 0 5px rgba(0, 174, 239, 0.1); border-color: rgba(0, 174, 239, 0.3); }
        }

        .flashing-box {
            animation: glow-pulse-red 1.5s ease-in-out infinite !important;
        }
        @keyframes glow-pulse-red {
            0% { box-shadow: 0 0 5px rgba(255, 0, 0, 0.2); border-color: rgba(255, 0, 0, 0.3); }
            50% { box-shadow: 0 0 30px rgba(255, 0, 0, 0.9); border-color: rgba(255, 0, 0, 1); }
            100% { box-shadow: 0 0 5px rgba(255, 0, 0, 0.2); border-color: rgba(255, 0, 0, 0.3); }
        }

        .container { flex-grow: 1; max-width: 100%; margin: 0; padding: 15px; display: flex; flex-direction: column; gap: 15px; width: 100%; box-sizing: border-box; }
        .summary-wrap { display: flex; gap: 15px; width: 100%; box-sizing: border-box; }

        /* Split Top Containers Layout */
        .group { display: flex; flex-direction: column; justify-content: space-between; align-items: center; height: 100%; width: 100%; }
        .up-dn-labels { display: flex; width: 100%; gap: 15px; font-weight: bold; color: #8b949e; margin-bottom: 8px; font-size: 15px; }
        .up-dn-labels span { flex: 1; text-align: center; } 
        .box-container { display: flex; width: 100%; flex-grow: 1; gap: 15px; align-items: center; justify-content: center; }
        .stat-box { flex: 1; height: 80%; display: flex; align-items: center; justify-content: center; font-size: 56px; font-weight: bold; border: 4px solid #000; border-radius: 12px; }
        
        .bg-up { background-color: #2ea043; color: white; } 
        .bg-dn { background-color: #ff0000; color: white; }
        
        /* Middle Row */
        .mid-row { flex: 1.2; margin: 0; }
        .mid-row .sum-box { height: 100%; flex-direction: column; }
        .sum-val { font-size: 48px; font-weight: bold; color: #ffffff; margin: 0; }

        .donut-static { width: 150px; height: 150px; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin-bottom: 5px; box-sizing: border-box; }
        .donut-aqua { border: 12px solid #2ac9c5; }
        .donut-yellow { border: 12px solid #f1c40f; }
        .donut-green { border: 12px solid #2ea043; }
        .donut-red-flash { border: 12px solid #ff0000; animation: pulse-red-donut 1.5s infinite; }
        
        @keyframes pulse-red-donut {
            0% { box-shadow: 0 0 10px rgba(255,0,0,0.5), inset 0 0 10px rgba(255,0,0,0.5); }
            50% { box-shadow: 0 0 30px rgba(255,0,0,1), inset 0 0 30px rgba(255,0,0,1); }
            100% { box-shadow: 0 0 10px rgba(255,0,0,0.5), inset 0 0 10px rgba(255,0,0,0.5); }
        }

        /* Bottom Row Gadgets */
        .bot-row { flex: 1.8; margin: 0; }
        .bot-row .sum-box { height: 100%; padding: 15px; box-sizing: border-box; justify-content: flex-start; flex-direction: column; }
        .gadget-title { text-align: center; color: #e1e1e1; font-weight: bold; margin-bottom: 8px; font-size: 14px; }
        .table-container { width: 100%; font-size: 11px; color: #e1e1e1; text-align: left; border-collapse: collapse; }
        .table-container th { color: #58a6ff; font-weight: normal; padding-bottom: 4px; border-bottom: 1px solid #30363d; }
        .table-container td { padding: 3px 0; border-bottom: 1px solid #21262d; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 130px; }
        .table-container td.right, .table-container th.right { text-align: right; }

        /* Scroller */
        .scroller-wrapper { margin: 0 auto; width: 70%; height: 40px; border-radius: 6px; display: flex; align-items: center; justify-content: center; text-align: center; background: #161b22; flex-shrink: 0; }
        .scroller-ok { border: 2px solid rgba(46, 160, 67, 0.3); animation: glow-pulse-green 5s ease-in-out infinite; }
        @keyframes glow-pulse-green {
            0% { box-shadow: 0 0 5px rgba(46, 160, 67, 0.2); border-color: rgba(46, 160, 67, 0.3); }
            50% { box-shadow: 0 0 20px rgba(46, 160, 67, 0.8); border-color: rgba(46, 160, 67, 1); }
            100% { box-shadow: 0 0 5px rgba(46, 160, 67, 0.2); border-color: rgba(46, 160, 67, 0.3); }
        }

        .scroller-alert { border: 2px solid rgba(255, 0, 0, 0.3); animation: glow-pulse-red 1.5s ease-in-out infinite; }
        #ap-alert-text { font-size: 20px; font-weight: bold; color: yellow; white-space: nowrap; }
        .flash-effect { animation: text-flash 0.6s ease-in-out; }
        @keyframes text-flash { 0% { opacity: 1; color: #FFFFFF; } 25% { opacity: 0; color: #FFFFFF; } 50% { opacity: 1; color: #FFFFFF; } 75% { opacity: 0; color: #FFFFFF; } 100% { opacity: 1; color: yellow; } }
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
                    if (scrollerAlerts[currentIndex].includes("OFFLINE")) {
                        alertTextElement.style.color = "#ff4a4a";
                    } else if (scrollerAlerts[currentIndex].includes("MESH")) {
                        alertTextElement.style.color = "#a8d5ff";
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
                        datasets: [{ data: ssidData.map(d => d.count), backgroundColor: ssidData.map(d => d.color), borderWidth: 0, cutout: '75%' }]
                    },
                    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { enabled: true } }, animation: { duration: 1500, animateScale: true } }
                });
            }
        });
    </script>
</head>
<body onload="updateTime()">
    <div class="header">
        <div><h1>Meraki Dashboard | Live Monitor</h1><div class="header-sub">Ramon Solis © 2026 | Auto-Refresh Every 10 Minutes</div></div>
        <div class="header-clock-container"><div id="live-date"></div><div id="live-clock">00:00:00 AM</div></div>
        <div class="status-pill"><div class="glow-dot"></div><span style="color:#2ea043;font-weight:bold;font-size:1.3em;">ON</span></div>
    </div>
    
    <div class="container">
        
        <div style="display: flex; gap: 5px; width: 100%; box-sizing: border-box; flex: 1;">
            
            <div class="card" style="flex: 1.6; margin: 0; padding: 15px 10px;">
                <div class="group">
                    <div class="up-dn-labels" style="font-size: 13px;">
                        <span style="color: #2ea043;">ONLINE</span>
                        <span style="color: #ff0000;">DOWN</span>
                        <span style="color: #f1c40f;">ALERT</span>
                        <span style="color: #95a5a6;">MESH</span>
                    </div>
                    <div class="box-container" style="gap: 8px;">
                        <div class="stat-box bg-up" style="font-size: 40px; height: 90px;">{{ stats.ap.online }}</div>
                        <div class="stat-box bg-dn" style="font-size: 40px; height: 90px;">{{ stats.ap.offline }}</div>
                        <div class="stat-box" style="font-size: 40px; height: 90px; background-color: #f1c40f; color: #000; border-color: #000;">{{ stats.ap.alerting }}</div>
                        <div class="stat-box" style="font-size: 40px; height: 90px; background-color: #95a5a6; color: #fff; border-color: #000;">{{ stats.ap.repeater }}</div>
                    </div>
                    <div style="margin-top:10px;font-weight:bold;font-size:1.1em;color:#58a6ff;">Access Points</div>
                </div>
            </div>

            {% for label, d in [('Switches', stats.sw), ('Cameras', stats.cam), ('Sensors', stats.sen)] %}
            <div class="card" style="flex: 1; margin: 0; padding: 15px 10px;">
                <div class="group">
                    <div class="up-dn-labels"><span>UP</span><span>DOWN</span></div>
                    <div class="box-container">
                        <div class="stat-box bg-up" style="font-size: 40px; height: 90px;">{{ d.up }}</div>
                        <div class="stat-box bg-dn" style="font-size: 40px; height: 90px;">{{ d.dn }}</div>
                    </div>
                    <div style="margin-top:10px;font-weight:bold;font-size:1.1em;color:#58a6ff;">{{ label }}</div>
                </div>
            </div>
            {% endfor %}

        </div>

        <div class="summary-wrap mid-row">
            <div class="sum-box" style="flex: 2; padding: 15px; box-sizing: border-box; justify-content: flex-start; align-items: center;">
                <div style="width: 100%; text-align: left; margin-bottom: 10px;">
                    <span style="color: #ffffff; font-weight: bold; font-size: 14px;">Total Meraki Networks</span>
                </div>
                <div style="display: flex; flex-grow: 1; align-items: center; justify-content: center;">
                    <div class="donut-static donut-aqua">
                        <div class="sum-val" style="font-size: 60px;">{{ stats.nets }}</div>
                    </div>
                </div>
            </div>

            <div class="sum-box" style="flex: 4; padding: 15px; box-sizing: border-box; justify-content: flex-start; align-items: flex-start;">
                <div style="width: 100%; display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                    <span style="color: #ffffff; font-weight: bold; font-size: 14px;">Wireless Client by SSID | Last 8 Hours</span>
                    <span style="color: #8b949e; cursor: pointer;">⋮</span>
                </div>
                {% if stats.ssid_list %}
                <div style="display: flex; align-items: center; justify-content: space-evenly; width: 100%; height: 100%;">
                    <div style="position: relative; width: 150px; height: 150px;">
                        <canvas id="ssidChart"></canvas>
                        <div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -45%); font-size: 30px; font-weight: bold; color: white;">
                            {{ stats.wireless_total_str }}
                        </div>
                    </div>
                    <div style="display: flex; flex-direction: column; gap: 8px; width: 140px;">
                        {% for ssid in stats.ssid_list %}
                        <div style="display: flex; align-items: center; justify-content: space-between; font-size: 12px; font-weight: bold; color: #e1e1e1;">
                            <div style="display: flex; align-items: center; gap: 6px;">
                                <div style="width: 10px; height: 10px; border-radius: 50%; background-color: {{ ssid.color }};"></div>
                                <span style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 85px;" title="{{ ssid.name }}">{{ ssid.name }}</span>
                            </div>
                            <span>{{ "{:,}".format(ssid.count) }}</span>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                {% else %}
                <div style="display: flex; align-items: center; justify-content: center; height: 100%; width: 100%; color: #8b949e; font-size: 16px;">
                    <div style="text-align: center;">
                        <div style="font-size: 40px; color: white; margin-bottom: 5px;">{{ stats.clients_str }}</div>
                        Total Clients<br>(SSID Breakdown Unavailable)
                    </div>
                </div>
                {% endif %}
            </div>
            
            <div class="sum-box {% if stats.slow_ap_count > 0 %} flashing-box {% endif %}" style="flex: 3; padding: 15px; box-sizing: border-box; justify-content: flex-start; align-items: center;">
                <div style="width: 100%; text-align: left; margin-bottom: 10px;">
                    <span style="color: #ffffff; font-weight: bold; font-size: 14px;">AP Running under < 1 Gbps Speed</span>
                </div>
                <div style="display: flex; flex-grow: 1; align-items: center; justify-content: center;">
                    <div class="donut-static {% if stats.slow_ap_count > 0 %} donut-red-flash {% else %} donut-yellow {% endif %}">
                        <div style="font-size: 60px; font-weight: bold; color: white;">{{ stats.slow_ap_count }}</div>
                    </div>
                </div>
            </div>
        </div>

        <div class="summary-wrap bot-row">
            <div class="sum-box" style="flex: 1;">
                <div class="gadget-title">Top 10 Clients by Data Usage</div>
                <table class="table-container">
                    <tr><th>Description</th><th class="right">Usage</th></tr>
                    {% for c in stats.top_clients %}
                    <tr><td title="{{ c.name }}">{{ c.name }}</td><td class="right">{{ c.usage }}</td></tr>
                    {% endfor %}
                </table>
            </div>

            <div class="sum-box" style="flex: 1;">
                <div class="gadget-title">Top 10 APs by Connected Clients</div>
                <table class="table-container">
                    <tr><th>Name</th><th class="right"># Clients</th></tr>
                    {% for a in stats.top_ap_clients %}
                    <tr><td title="{{ a.name }}">{{ a.name }}</td><td class="right">{{ a.clients }}</td></tr>
                    {% endfor %}
                </table>
            </div>

            <div class="sum-box" style="flex: 1;">
                <div class="gadget-title">Top 10 APs by Data Usage</div>
                <table class="table-container">
                    <tr><th>Name</th><th class="right">Usage</th></tr>
                    {% for a in stats.top_ap_usage %}
                    <tr><td title="{{ a.name }}">{{ a.name }}</td><td class="right">{{ a.usage }}</td></tr>
                    {% endfor %}
                </table>
            </div>

            <div class="sum-box" style="flex: 1;">
                <div class="gadget-title">Device Models Inventory</div>
                <table class="table-container">
                    <tr><th>Model</th><th class="right"># Devs</th><th class="right">Usage</th></tr>
                    {% for m in stats.top_models %}
                    <tr><td title="{{ m.model }}">{{ m.model }}</td><td class="right">{{ m.count }}</td><td class="right">{{ m.usage }}</td></tr>
                    {% endfor %}
                </table>
            </div>

            <div class="sum-box" style="flex: 1; padding: 15px; box-sizing: border-box; flex-direction: column; justify-content: flex-start; align-items: center;">
                <div class="gadget-title" style="width: 100%; text-align: center; margin-bottom: 10px;">Organization Total Data Transferred</div>
                <div style="display: flex; flex-grow: 1; align-items: center; justify-content: center; width: 100%;">
                    <div class="donut-static donut-green" style="width: 160px; height: 160px; border-width: 16px; margin: 0;">
                        <div style="font-size: 32px; font-weight: bold; color: white; text-align: center;">{{ stats.total_data }}</div>
                    </div>
                </div>
            </div>
        </div> 

        <div class="scroller-wrapper {% if stats.scroller_alerts %} scroller-alert {% else %} scroller-ok {% endif %}" style="margin-top: 25px; margin-bottom: 10px;">
            {% if stats.scroller_alerts %}
                <div id="ap-alert-text"></div>
            {% else %}
                <div id="ap-alert-text" style="color: #4DE81A;">
                    All Organization Hardware is ONLINE and Negotiating at Optimal Speeds ✓
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
