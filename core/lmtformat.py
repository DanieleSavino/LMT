import struct
import os

# Data Layout (Little-endian):
# d  : float64 (8 bytes)  - Timestamp
# ?  : bool    (1 byte)   - Traction Control Active
# ?  : bool    (1 byte)   - ABS Active
# 4f : float32 (16 bytes) - Wheel Slip (FL, FR, RL, RR)
# Total Size = 26 Bytes per frame
LMT_STRUCT_FORMAT = '<d??4f'
FRAME_SIZE = struct.calcsize(LMT_STRUCT_FORMAT)

def pack_telemetry_frame(timestamp: float, tc: bool, abs_active: bool, slips: tuple) -> bytes:
    return struct.pack(
        LMT_STRUCT_FORMAT, 
        timestamp, 
        bool(tc), 
        bool(abs_active), 
        float(slips[0]), float(slips[1]), float(slips[2]), float(slips[3])
    )

def unpack_telemetry_file(filepath: str) -> list:
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Telemetry file not found: {filepath}")

    frames = []
    with open(filepath, 'rb') as f:
        while True:
            chunk = f.read(FRAME_SIZE)
            if not chunk or len(chunk) < FRAME_SIZE:
                break
            frames.append(struct.unpack(LMT_STRUCT_FORMAT, chunk))
    return frames
