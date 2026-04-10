import asyncio
import threading
import tkinter as tk
import time
from pathlib import Path

from bleak import BleakClient, BleakScanner
from pythonosc import udp_client

# =========================
# CONFIG
# =========================
APP_TITLE = "BLE Heart Rate to OSC"
LAST_DEVICE_FILE = Path("last_device.txt")

DEFAULT_OSC_IP = "127.0.0.1"
DEFAULT_OSC_PORT = 9000

HR_CHARACTERISTIC = "00002a37-0000-1000-8000-00805f9b34fb"

# =========================
# GLOBAL STATE
# =========================
osc = udp_client.SimpleUDPClient(DEFAULT_OSC_IP, DEFAULT_OSC_PORT)

devices_list = []
lasttime = 0

reconnecting = False
connecting = False
current_client = None


# =========================
# FILE SAVE / LOAD
# =========================
def save_last_device(address):
    """Save last connected device MAC + OSC settings."""
    current_ip = ip_entry.get().strip()
    current_port = port_entry.get().strip()

    try:
        with open(LAST_DEVICE_FILE, "w", encoding="utf-8") as f:
            f.write(f"{address},{current_ip},{current_port}")
    except Exception as e:
        print(f"Error saving last device: {e}")


def load_last_device():
    """Load last connected device MAC + OSC settings."""
    try:
        with open(LAST_DEVICE_FILE, "r", encoding="utf-8") as f:
            data = f.read().strip().split(",")

            if len(data) == 3:
                return data[0], data[1], data[2]
            elif len(data) == 1:
                return data[0], None, None

    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Error loading last device: {e}")

    return None, None, None


# =========================
# OSC
# =========================
def update_osc_settings():
    """Update OSC destination."""
    global osc

    new_ip = ip_entry.get().strip()

    try:
        new_port = int(port_entry.get().strip())
        osc = udp_client.SimpleUDPClient(new_ip, new_port)
        print(f"OSC updated: {new_ip}:{new_port}")
    except ValueError:
        print("Invalid OSC port")


# =========================
# HEART RATE HANDLER
# =========================
def handle_hr(sender, data):
    """Handle incoming heart rate BLE notification."""
    global lasttime

    try:
        flags = data[0]

        if flags & 0x01:  # 16-bit BPM
            bpm = int.from_bytes(data[1:3], byteorder="little")
        else:  # 8-bit BPM
            bpm = data[1]

        lasttime = time.time()

        print("BPM:", bpm)

        if root.winfo_exists():
            root.after(0, lambda: bpm_label.config(text=f"BPM: {bpm}"))

        hr1 = bpm % 10
        hr2 = (bpm // 10) % 10
        hr3 = (bpm // 100) % 10

        try:
            osc.send_message("/avatar/parameters/HR1", hr1)
            osc.send_message("/avatar/parameters/HR2", hr2)
            osc.send_message("/avatar/parameters/HR3", hr3)
        except Exception as e:
            print(f"OSC send error: {e}")

    except Exception as e:
        print(f"HR parse error: {e}")


# =========================
# BLE SCAN / CONNECT
# =========================
async def scan_devices():
    """Scan nearby BLE devices."""
    try:
        devices = await BleakScanner.discover()
        seen = set()

        for d in devices:
            if d.address in seen:
                continue
            seen.add(d.address)

            devices_list.append(d)
            name = d.name if d.name else "UNKNOWN"

            if root.winfo_exists():
                root.after(
                    0,
                    lambda n=name, a=d.address: listbox.insert(tk.END, f"{n} - {a}")
                )

    except Exception as e:
        print(f"Scan error: {e}")


async def connect_device(address):
    """Connect to BLE heart rate device and subscribe to notifications."""
    global lasttime, reconnecting, current_client, connecting

    if connecting:
        print("Already connecting...")
        return

    connecting = True
    print("Connecting to:", address)

    try:
        if root.winfo_exists():
            root.after(0, lambda: status_label.config(
                text="BLUETOOTH STATUS: CONNECTING...",
                fg="orange"
            ))

        async with BleakClient(address, timeout=10.0) as client:
            current_client = client
            reconnecting = False
            lasttime = time.time()

            save_last_device(address)

            if root.winfo_exists():
                root.after(0, lambda: status_label.config(
                    text="BLUETOOTH STATUS: CONNECTED",
                    fg="green"
                ))

            print("Connected!")

            await client.start_notify(HR_CHARACTERISTIC, handle_hr)

            while client.is_connected:
                await asyncio.sleep(1)

    except Exception as e:
        print(f"Connection error: {e}")

        if root.winfo_exists():
            root.after(0, lambda: status_label.config(
                text="BLUETOOTH STATUS: DISCONNECTED",
                fg="red"
            ))

    finally:
        connecting = False
        current_client = None


# =========================
# THREAD HELPERS
# =========================
def start_scan():
    """Start BLE scan safely from thread."""
    devices_list.clear()

    if root.winfo_exists():
        root.after(0, lambda: listbox.delete(0, tk.END))

    asyncio.run(scan_devices())


def device_selected(event):
    """Handle device selection from list."""
    selection = listbox.curselection()

    if selection:
        index = selection[0]
        device = devices_list[index]

        print("Selected device:", device.name)
        print("MAC:", device.address)

        threading.Thread(
            target=lambda: asyncio.run(connect_device(device.address)),
            daemon=True
        ).start()


def bpmtimeout():
    """Detect missing BPM updates and trigger reconnect."""
    global lasttime, reconnecting

    while True:
        try:
            if root.winfo_exists() and lasttime != 0:
                if time.time() - lasttime > 10 and not reconnecting:
                    print("No BPM received for 10 seconds. Reconnecting...")
                    reconnecting = True

                    threading.Thread(
                        target=reconnect_device,
                        daemon=True
                    ).start()
        except:
            pass

        time.sleep(1)


def reconnect_device():
    """Reconnect to last known device."""
    global reconnecting, lasttime

    last_mac, _, _ = load_last_device()

    if not last_mac:
        reconnecting = False
        return

    max_attempts = 5

    for attempt in range(1, max_attempts + 1):
        if not root.winfo_exists():
            return

        root.after(0, lambda a=attempt: status_label.config(
            text=f"BLUETOOTH STATUS: RETRYING ({a}/{max_attempts})",
            fg="orange"
        ))

        print(f"Reconnect attempt {attempt}/{max_attempts}...")

        try:
            asyncio.run(connect_device(last_mac))

            if not reconnecting:
                print("Reconnect successful!")
                return

        except Exception as e:
            print(f"Reconnect attempt {attempt} failed: {e}")

        if attempt < max_attempts:
            time.sleep(5)

    lasttime = 0
    reconnecting = False

    print("Reconnect failed.")
    if root.winfo_exists():
        root.after(0, lambda: status_label.config(
            text="BLUETOOTH STATUS: ERROR",
            fg="red"
        ))


# =========================
# APP CLOSE
# =========================
def on_close():
    """Close app safely."""
    global root

    print("Closing app...")

    try:
        if current_client and current_client.is_connected:
            try:
                asyncio.run(current_client.disconnect())
            except Exception as e:
                print(f"Disconnect error: {e}")
    except:
        pass

    root.destroy()


# =========================
# GUI
# =========================
root = tk.Tk()
root.title(APP_TITLE)

osc_frame = tk.LabelFrame(root, text=" OSC Settings ", padx=10, pady=10)
osc_frame.pack(padx=10, pady=5, fill="x")

tk.Label(osc_frame, text="IP:").grid(row=0, column=0)
ip_entry = tk.Entry(osc_frame, width=15)
ip_entry.insert(0, DEFAULT_OSC_IP)
ip_entry.grid(row=0, column=1)

tk.Label(osc_frame, text="Port:").grid(row=0, column=2, padx=(10, 0))
port_entry = tk.Entry(osc_frame, width=8)
port_entry.insert(0, str(DEFAULT_OSC_PORT))
port_entry.grid(row=0, column=3)

apply_button = tk.Button(osc_frame, text="Apply", command=update_osc_settings)
apply_button.grid(row=0, column=4, padx=10)

listbox = tk.Listbox(root, width=50)
listbox.bind("<<ListboxSelect>>", device_selected)
listbox.pack(padx=10, pady=10)

refresh_button = tk.Button(
    root,
    text="Refresh",
    command=lambda: threading.Thread(target=start_scan, daemon=True).start()
)
refresh_button.pack(pady=5)

bpm_label = tk.Label(root, text="BPM: --", font=("Arial", 14))
bpm_label.pack()

status_label = tk.Label(
    root,
    text="BLUETOOTH STATUS: DISCONNECTED",
    font=("Arial", 12)
)
status_label.pack()

# Auto-load last config
last_mac, last_ip, last_port = load_last_device()

if last_ip and last_port:
    ip_entry.delete(0, tk.END)
    ip_entry.insert(0, last_ip)

    port_entry.delete(0, tk.END)
    port_entry.insert(0, last_port)

    update_osc_settings()

# Start background threads
threading.Thread(target=start_scan, daemon=True).start()
threading.Thread(target=bpmtimeout, daemon=True).start()

# Auto reconnect to last device
if last_mac:
    print("Auto connecting to:", last_mac)
    threading.Thread(
        target=lambda: asyncio.run(connect_device(last_mac)),
        daemon=True
    ).start()

root.protocol("WM_DELETE_WINDOW", on_close)
root.mainloop()