"""
RGG-PBFT Consensus Node — Distributed TCP implementation.
"""
import socket, threading, json, time, sys, os, hashlib, statistics

NODE_ID = int(os.environ.get("NODE_ID", sys.argv[1] if len(sys.argv) > 1 else "0"))
M = int(os.environ.get("M", "16"))
K_G = os.environ.get("K_G", None)
K_G = int(K_G) if K_G and K_G != "None" else None
DELAY_MS = float(os.environ.get("DELAY_MS", "5"))
N_ROUNDS = int(os.environ.get("N_ROUNDS", "5"))
PORT_BASE = int(os.environ.get("PORT_BASE", "9000"))
COLLECTOR_HOST = os.environ.get("COLLECTOR_HOST", "collector")
COLLECTOR_PORT = int(os.environ.get("COLLECTOR_PORT", "9999"))
MODE = "RGG-PBFT" if K_G else "PBFT"

def get_group(nid):
    return 0 if K_G is None else nid % K_G

GROUP_ID = get_group(NODE_ID)
LEADERS = {0: 0} if K_G is None else {g: min(i for i in range(M) if i % K_G == g) for g in range(K_G)}
GLOBAL_PRIMARY = min(LEADERS.values())
IS_LEADER = NODE_ID in LEADERS.values()
IS_PRIMARY = NODE_ID == GLOBAL_PRIMARY
N_GROUP = M if K_G is None else len([i for i in range(M) if get_group(i) == GROUP_ID])

# Per-round state (lock protected)
lock = threading.Lock()
rounds = {}  # rnd -> {prepares:set, commits:set, gprep:set, gcomm:set, sent_c:bool, sent_gc:bool, client, ts}

def quorum(n):
    return 2 * ((n - 1) // 3) + 1

def node_host(nid):
    return f"node{nid}"

def send_msg(target_id, msg_type, payload):
    time.sleep(DELAY_MS / 1000.0)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((node_host(target_id), PORT_BASE + target_id))
        s.sendall(json.dumps({"type": msg_type, "from": NODE_ID, "payload": payload}).encode())
        s.close()
        return True
    except:
        return False

def to_collector(evt, data):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect((COLLECTOR_HOST, COLLECTOR_PORT))
        s.sendall(json.dumps({"node": NODE_ID, "type": evt, "data": data, "ts": time.time()}).encode())
        s.close()
        pass
    except Exception as e:
        pass

def handle(conn, addr):
    try:
        data = conn.recv(65536).decode()
        if not data: return
        msg = json.loads(data)
        mt, pl, sender = msg["type"], msg["payload"], msg["from"]
        rnd = pl["round"]
        rhash = pl["hash"]

        with lock:
            key = (rnd, rhash, pl.get("view", 0))
            if key not in rounds:
                rounds[key] = {"prepares": set(), "commits": set(),
                               "gprep": set(), "gcomm": set(), "done": False,
                               "sent_c": False, "sent_gp": False, "sent_gc": False,
                               "client": None, "ts": time.time()}
            rd = rounds[key]
            rd["ts"] = min(rd["ts"], time.time())

            if mt == "REQUEST":
                rd["client"] = conn
                to_collector("REQ", {"round": rnd})
                if IS_PRIMARY:
                    pp = {"round": rnd, "hash": rhash, "view": 0}
                    if K_G is None:
                        for nid in range(M):
                            if nid != NODE_ID: send_msg(nid, "PRE_PREPARE", pp)
                    else:
                        for lid in LEADERS.values():
                            if lid != NODE_ID: send_msg(lid, "PRE_PREPARE", pp)
                        for nid in range(M):
                            if get_group(nid) == GROUP_ID and nid != NODE_ID:
                                send_msg(nid, "PRE_PREPARE", pp)

            elif mt == "PRE_PREPARE":
                if K_G and IS_LEADER:
                    for nid in range(M):
                        if get_group(nid) == GROUP_ID and nid != NODE_ID:
                            send_msg(nid, "PRE_PREPARE", pl)
                pp = {"round": rnd, "hash": rhash, "view": 0}
                targets = [i for i in range(M) if K_G is None or get_group(i) == GROUP_ID]
                for nid in targets:
                    if nid != NODE_ID: send_msg(nid, "PREPARE", pp)

            elif mt == "PREPARE":
                rd["prepares"].add(sender)
                rd["prepares"].add(NODE_ID)  # self-vote
                q = quorum(N_GROUP)
                if len(rd["prepares"]) >= q and not rd["sent_c"]:
                    rd["sent_c"] = True
                    cp = {"round": rnd, "hash": rhash, "view": 0}
                    targets = [i for i in range(M) if K_G is None or get_group(i) == GROUP_ID]
                    for nid in targets:
                        if nid != NODE_ID: send_msg(nid, "COMMIT", cp)

            elif mt == "COMMIT":
                rd["commits"].add(sender)
                rd["commits"].add(NODE_ID)
                q = quorum(N_GROUP)
                if len(rd["commits"]) >= q:
                    if K_G and IS_LEADER and not rd["sent_gp"]:
                        rd["sent_gp"] = True
                        gp = {"round": rnd, "hash": rhash, "view": 0, "group": GROUP_ID}
                        for lid in LEADERS.values():
                            if lid != NODE_ID: send_msg(lid, "GLOBAL_PREPARE", gp)
                    if K_G is None:
                        do_commit(rnd, rd)

            elif mt == "GLOBAL_PREPARE":
                rd["gprep"].add(sender)
                rd["gprep"].add(NODE_ID)
                q = quorum(K_G)
                if len(rd["gprep"]) >= q and not rd["sent_gc"]:
                    rd["sent_gc"] = True
                    gc = {"round": rnd, "hash": rhash, "view": 0}
                    for lid in LEADERS.values():
                        if lid != NODE_ID: send_msg(lid, "GLOBAL_COMMIT", gc)

            elif mt == "GLOBAL_COMMIT":
                rd["gcomm"].add(sender)
                rd["gcomm"].add(NODE_ID)
                q = quorum(K_G)
                if len(rd["gcomm"]) >= q:
                    do_commit(rnd, rd)
                    for nid in range(M):
                        if nid != NODE_ID: send_msg(nid, "NOTIFY", {"round": rnd, "hash": rhash})

            elif mt == "NOTIFY":
                if sender in LEADERS.values():
                    do_commit(rnd, rd)

    except: pass

def do_commit(rnd, rd):
    if rd.get("done"): return
    rd["done"] = True
    pass
    lat = (time.time() - rd["ts"]) * 1000
    to_collector("DONE", {"round": rnd, "latency_ms": lat, "mode": MODE})
    cl = rd.get("client")
    if cl:
        try: cl.sendall(json.dumps({"type":"REPLY","result":"ok","round":rnd}).encode())
        except: pass

def run():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT_BASE + NODE_ID))
    srv.listen(50); srv.settimeout(90)
    to_collector("READY", {"mode": MODE, "group": GROUP_ID, "leader": IS_LEADER, "primary": IS_PRIMARY})
    while True:
        try:
            c, a = srv.accept()
            threading.Thread(target=handle, args=(c, a), daemon=True).start()
        except: break

if __name__ == "__main__":
    time.sleep(1.0); run()
