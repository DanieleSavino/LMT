import os
import time
import json
import psutil
from datetime import datetime
from lmtformat import pack_telemetry_frame

# --- IMPORT THE ACTUAL LMU SHARED MEMORY LIBRARY ---
try:
    from pyLMUSharedMemory import SimInfo
except ImportError:
    print("Error: Could not import pyLMUSharedMemory. Please ensure it is in your project/environment.")
    SimInfo = None

LMU_PROCESS_NAME = "LeMansUltimate.exe"
TICK_RATE_HZ = 60
POLL_INTERVAL = 1.0 / TICK_RATE_HZ

# Navigate up from core/ to the root directory
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")

class LMUSharedMemoryAdapter:
    def __init__(self):
        if SimInfo is None:
            raise RuntimeError("pyLMUSharedMemory is missing.")
        
        # Initialize the shared memory map connection
        self.info = SimInfo()
        self.connected = True 

    def read_current_telemetry(self):
        # Trigger the map update to pull the latest frame from the game engine
        if hasattr(self.info, 'UpdateTelemetryInfo'):
            self.info.UpdateTelemetryInfo()
            
        telemetry = self.info.telemetryInfo()
        
        # Ensure the vehicle array is populated (game is loaded into the track)
        if telemetry.mNumVehicles == 0:
            return {
                "tc_active": False,
                "abs_active": False,
                "slips": (0.0, 0.0, 0.0, 0.0)
            }

        # Player vehicle is typically at index 0
        player_car = telemetry.mVehicles[0]
        
        # Extract TC and ABS (names vary slightly based on pyLMUSharedMemory version)
        # Using getattr with defaults in case the properties are slightly different in your header
        tc = getattr(player_car, 'mTC', False) or getattr(player_car, 'mTractionControl', False)
        abs_active = getattr(player_car, 'mABS', False) or getattr(player_car, 'mAntiLockBrakes', False)
        
        # Wheel mapping: 0=FL, 1=FR, 2=RL, 3=RR
        # We extract the slip or grip variable (e.g., mGripFract or mSlipAngle)
        wheels = player_car.mWheels
        slips = (
            getattr(wheels[0], 'mGripFract', 0.0),
            getattr(wheels[1], 'mGripFract', 0.0),
            getattr(wheels[2], 'mGripFract', 0.0),
            getattr(wheels[3], 'mGripFract', 0.0)
        )
        
        return {
            "tc_active": bool(tc),
            "abs_active": bool(abs_active),
            "slips": slips
        }
        
    def close(self):
        # pyLMUSharedMemory typically auto-closes when garbage collected, 
        # but we can reset the SimInfo object to be safe.
        self.info = None

def is_lmu_running() -> bool:
    for proc in psutil.process_iter(['name']):
        try:
            if proc.info['name'] and proc.info['name'].lower() == LMU_PROCESS_NAME.lower():
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False

def inject_to_config(new_file_path: str):
    config = {"active_telemetry": []}
    
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                config = json.load(f)
        except json.JSONDecodeError:
            print("Warning: config.json was corrupted. Creating a new one.")

    if "active_telemetry" not in config:
        config["active_telemetry"] = []
        
    rel_path = os.path.relpath(new_file_path, PROJECT_ROOT)
    config["active_telemetry"].append(rel_path)
    
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=4)
        
    print(f"[Config] Injected new telemetry file: {rel_path}")

def run_telemetry_session(memory_reader: LMUSharedMemoryAdapter):
    print("[Capture] LMU detected. Hooking shared memory and recording...")
    session_buffer = bytearray()
    
    while is_lmu_running():
        loop_start_time = time.time()
        
        try:
            data = memory_reader.read_current_telemetry()
        except Exception as e:
            # Prevent the daemon from crashing if shared memory temporarily drops
            print(f"Memory read error: {e}")
            data = {"tc_active": False, "abs_active": False, "slips": (0.0, 0.0, 0.0, 0.0)}
            time.sleep(1.0) # Back off slightly on error
        
        packed_frame = pack_telemetry_frame(
            timestamp=loop_start_time,
            tc=data["tc_active"],
            abs_active=data["abs_active"],
            slips=data["slips"]
        )
        
        session_buffer.extend(packed_frame)
        
        elapsed = time.time() - loop_start_time
        sleep_time = POLL_INTERVAL - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

    print("[Capture] Session ended. Preparing to flush data to disk.")
    
    if len(session_buffer) == 0:
        print("[Capture] No data was recorded. Skipping save.")
        return

    now = datetime.now()
    # Saves to ./telemetry_data/YYYY-MM-DD/
    date_dir = os.path.join(PROJECT_ROOT, "telemetry_data", now.strftime("%Y-%m-%d"))
    os.makedirs(date_dir, exist_ok=True)
    
    file_path = os.path.join(date_dir, now.strftime("%H-%M-%S") + ".lmt")
    
    with open(file_path, 'wb') as f:
        f.write(session_buffer)
        
    print(f"[IO] Successfully saved {len(session_buffer)} bytes to {file_path}")
    inject_to_config(file_path)

def main():
    print(f"Starting LiveSlip Daemon. Polling at {TICK_RATE_HZ}Hz.")
    print("Waiting for Le Mans Ultimate to launch...")
    
    while True:
        if is_lmu_running():
            try:
                memory_reader = LMUSharedMemoryAdapter()
                run_telemetry_session(memory_reader)
            except Exception as e:
                print(f"Failed to hook session: {e}")
            finally:
                if 'memory_reader' in locals():
                    memory_reader.close()
                    
            print("Waiting for next session to start...")
            
        time.sleep(3.0)

if __name__ == "__main__":
    main()
