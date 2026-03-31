#!/usr/bin/env python3
import os, re, csv, time, math, argparse, datetime
from mininet.log import setLogLevel, info, warn
from mininet.node import Controller
from mn_wifi.net import Mininet_wifi
from mn_wifi.node import OVSKernelAP

def ensure_dir(p): os.makedirs(p, exist_ok=True)
def now_tag(): return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def percentile(vals, p):
    if not vals: return None
    vals = sorted(vals)
    k = (len(vals)-1) * (p/100.0)
    f = math.floor(k); c = math.ceil(k)
    if f == c: return vals[int(k)]
    return vals[f]*(c-k) + vals[c]*(k-f)

def parse_ping_ms(txt):
    return [float(m.group(1)) for m in re.finditer(r"time=([\d.]+)\s*ms", txt)]

def parse_iperf_mbps(txt):
    m = re.findall(r"([\d.]+)\s*(Gbits|Mbits)/sec", txt)
    if not m: return None
    val, unit = m[-1]
    val = float(val)
    return val*1000.0 if unit=="Gbits" else val

def cmd_exists(node, cmd):
    return node.cmd(f"sh -lc 'command -v {cmd} >/dev/null 2>&1; echo $?'").strip().endswith("0")

def get_wlan(node):
    for i in node.intfList():
        s = str(i)
        if "wlan" in s:
            return s
    return ""

def tc_clear(node):
    intf = get_wlan(node)                                                                                                                                                                    
    if intf:                                                                                                                                                                                 
        node.cmd(f"sh -lc 'tc qdisc del dev {intf} root >/dev/null 2>&1 || true'")                                                                                                           
                                                                                                                                                                                             
def tc_tbf(node, rate_kbit, burst_kbit=32, latency_ms=50):
    intf = get_wlan(node)
    if not intf: return
    tc_clear(node)
    node.cmd(f"sh -lc \"tc qdisc add dev {intf} root tbf rate {rate_kbit}kbit burst {burst_kbit}kbit latency {latency_ms}ms\"")

def wait_assoc(sta, timeout_s=25):
    if not cmd_exists(sta, "iw"):
        return False
    ifname = get_wlan(sta) or f"{sta.name}-wlan0"
    t0 = time.time()
    while time.time()-t0 < timeout_s:
        out = sta.cmd(f"sh -lc 'iw dev {ifname} link 2>/dev/null'")
        if "Connected to" in out:
            return True
        time.sleep(0.5)
    return False

def start_bulb_discovery(bulb, duration_s, discovery_pps, burst_every_s, burst_pps, burst_dur_s):
    payload = (
        "M-SEARCH * HTTP/1.1\\r\\n"
        "HOST:239.255.255.250:1900\\r\\n"
        "MAN:\\\"ssdp:discover\\\"\\r\\n"
        "MX:1\\r\\n"
        "ST:ssdp:all\\r\\n\\r\\n"
    )
    py = (
        "import socket,time;"
        "m=('239.255.255.250',1900);"
        "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP);"
        "s.setsockopt(socket.IPPROTO_IP,socket.IP_MULTICAST_TTL,1);"
        f"payload=b'{payload}';"
        f"dur={float(duration_s)}; rate={float(discovery_pps)};"
        f"burst_every={float(burst_every_s)}; burst_pps={float(burst_pps)}; burst_dur={float(burst_dur_s)};"
        "t0=time.time(); next_burst=t0+burst_every;"
        "while time.time()-t0 < dur:"
        "  now=time.time();"
        "  if now >= next_burst:"
        "    end=now+burst_dur; gap=1.0/max(burst_pps,1e-6);"
        "    while time.time()<end and time.time()-t0<dur:"
        "      s.sendto(payload,m); time.sleep(gap);"
        "    next_burst += burst_every;"
        "  else:"
        "    s.sendto(payload,m); time.sleep(1.0/max(rate,1e-6));"
    )
    pid = bulb.cmd(f"sh -lc 'python3 -c \"{py}\" >/dev/null 2>&1 & echo $!'").strip()
    return int(pid) if pid.isdigit() else None

def kill_pid(node, pid):
    if pid:
        node.cmd(f"sh -lc 'kill {pid} >/dev/null 2>&1 || true'")

def build_net(bulbs):
    net = Mininet_wifi(controller=Controller, accessPoint=OVSKernelAP)

    sta1 = net.addStation('sta1', ip='10.0.0.1/24', position='10,30,0', wlans=1, range=120)
    sta2 = net.addStation('sta2', ip='10.0.0.2/24', position='20,30,0', wlans=1, range=120)
    ap1  = net.addAccessPoint('ap1', ssid='home-wifi', mode='g', channel='1',
                              position='15,20,0', wlans=1, range=150)
    c1   = net.addController('c1')

    bulb_nodes = []
    for k in range(1, bulbs+1):
        bulb_nodes.append(net.addStation(f'bulb{k}', ip=f'10.0.0.{20+k}/24',
                                         position=f'{5*k},15,0', wlans=1, range=120))

    net.configureWifiNodes()
    net.addLink(sta1, ap1)
    net.addLink(sta2, ap1)
    for b in bulb_nodes:
        net.addLink(b, ap1)

    net.build()
    c1.start()
    ap1.start([c1])

    time.sleep(5)
    a1 = wait_assoc(sta1)
    a2 = wait_assoc(sta2)
    if not (a1 and a2):
        warn(f"Association not ready: sta1={a1}, sta2={a2}\n")

    return net, sta1, sta2, bulb_nodes

def run_condition(sta1, sta2, bulbs, out_dir, condition,
                  tcp_s, udp_s, udp_mbps, warmup_s,
                  discovery_pps, burst_every_s, burst_pps, burst_dur_s,
                  bulb_rate_kbit):

    ensure_dir(out_dir)

    if condition == "mitigated":
        for b in bulbs:
            tc_tbf(b, bulb_rate_kbit)
    else:
        for b in bulbs:
            tc_clear(b)

    bulb_pids = []
    if condition in ("active", "mitigated"):
        dur = tcp_s + udp_s + int(warmup_s) + 5
        for b in bulbs:
            bulb_pids.append(start_bulb_discovery(b, dur, discovery_pps, burst_every_s, burst_pps, burst_dur_s))

    time.sleep(warmup_s)

    dst = sta2.IP()
    ping_path = os.path.join(out_dir, "ping.txt")
    ping_count = int((tcp_s + udp_s) / 0.2)
    ping_pid = sta1.cmd(f"sh -lc 'ping -D -i 0.2 -c {ping_count} {dst} > {ping_path} 2>&1 & echo $!'").strip()
    ping_pid = int(ping_pid) if ping_pid.isdigit() else None

    if not (cmd_exists(sta1, "iperf3") and cmd_exists(sta2, "iperf3")):
        raise RuntimeError("iperf3 not found. Install: sudo apt-get install iperf3")

    tcp_cli = os.path.join(out_dir, "iperf_tcp_client.txt")
    sta2.cmd(f"sh -lc 'iperf3 -s -1 > /dev/null 2>&1 &'")
    time.sleep(1)
    sta1.cmd(f"sh -lc 'iperf3 -c {dst} -t {tcp_s} > {tcp_cli} 2>&1'")

    udp_cli = os.path.join(out_dir, "iperf_udp_client.txt")
    sta2.cmd(f"sh -lc 'iperf3 -s -1 > /dev/null 2>&1 &'")
    time.sleep(1)
    sta1.cmd(f"sh -lc 'iperf3 -c {dst} -u -b {udp_mbps}M -t {udp_s} > {udp_cli} 2>&1'")

    time.sleep(1)
    if ping_pid: kill_pid(sta1, ping_pid)
    for pid in bulb_pids: kill_pid(sta1, pid)
    for b in bulbs: tc_clear(b)

    ping_txt = open(ping_path, "r", errors="ignore").read()
    rtts = parse_ping_ms(ping_txt)
    p50 = percentile(rtts, 50)
    p95 = percentile(rtts, 95)
    p99 = percentile(rtts, 99)

    tcp_txt = open(tcp_cli, "r", errors="ignore").read()
    udp_txt = open(udp_cli, "r", errors="ignore").read()

    return {
        "condition": condition,
        "bulb_count": len(bulbs),
        "ping_p50_ms": p50, "ping_p95_ms": p95, "ping_p99_ms": p99,
        "tcp_mbps": parse_iperf_mbps(tcp_txt),
        "udp_mbps": parse_iperf_mbps(udp_txt),
        "out_dir": out_dir
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bulbs", type=int, required=True, help="Single bulb count per run (0,5,10).")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--tcp_s", type=int, default=10)
    ap.add_argument("--udp_s", type=int, default=10)
    ap.add_argument("--udp_mbps", type=int, default=10)
    ap.add_argument("--warmup_s", type=float, default=3.0)
    ap.add_argument("--discovery_pps", type=float, default=1.0)
    ap.add_argument("--burst_every_s", type=float, default=10.0)
    ap.add_argument("--burst_pps", type=float, default=20.0)
    ap.add_argument("--burst_dur_s", type=float, default=1.0)
    ap.add_argument("--bulb_rate_kbit", type=int, default=128)
    args = ap.parse_args()

    setLogLevel('info')
    base = os.path.join("results", now_tag() + f"_bulbs{args.bulbs}")
    ensure_dir(base)

    net, sta1, sta2, bulb_nodes = build_net(args.bulbs)
    rows = []
    try:
        for t in range(1, args.trials+1):
            for cond in (["idle"] if args.bulbs == 0 else ["idle","active","mitigated"]):
                out_dir = os.path.join(base, f"trial{t:02d}", cond)
                rows.append(run_condition(
                    sta1, sta2, bulb_nodes, out_dir, cond,
                    args.tcp_s, args.udp_s, args.udp_mbps, args.warmup_s,
                    args.discovery_pps, args.burst_every_s, args.burst_pps, args.burst_dur_s,
                    args.bulb_rate_kbit
                ))
    finally:
        net.stop()

    with open(os.path.join(base, "summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sorted(rows[0].keys()))
        w.writeheader()
        for r in rows: w.writerow(r)

    info(f"\nDONE. Results folder: {base}\n")
    info(f"Summary CSV: {os.path.join(base,'summary.csv')}\n")

if __name__ == "__main__":
    main()