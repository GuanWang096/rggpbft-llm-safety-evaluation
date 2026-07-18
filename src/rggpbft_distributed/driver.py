"""
Driver: sends REQUEST messages to the primary node to initiate consensus rounds.
"""
import socket
import json
import time
import hashlib
import sys
import os

M = int(os.environ.get("M", "16"))
PRIMARY = int(os.environ.get("PRIMARY", "0"))
N_ROUNDS = int(os.environ.get("N_ROUNDS", "5"))
PORT_BASE = int(os.environ.get("PORT_BASE", "9000"))

print(f"Driver: M={M}, primary={PRIMARY}, rounds={N_ROUNDS}")

time.sleep(5)  # wait for all nodes to start

for rnd in range(N_ROUNDS):
    request_hash = hashlib.sha256(f"round_{rnd}".encode()).hexdigest()[:16]
    msg = json.dumps({
        "type": "REQUEST",
        "from": -1,
        "payload": {"round": rnd, "hash": request_hash}
    })

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect((f"node{PRIMARY}", PORT_BASE + PRIMARY))
        s.sendall(msg.encode())

        # Wait for REPLY
        reply_data = s.recv(65536).decode()
        reply = json.loads(reply_data)
        print(f"  Round {rnd}: {reply['result']}")
        s.close()
    except Exception as e:
        print(f"  Round {rnd}: FAILED ({e})")

    time.sleep(0.5)

print("Driver: complete.", flush=True)
time.sleep(40)  # wait for collector to finish processing
