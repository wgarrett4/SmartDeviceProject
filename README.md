The experiment can be reproduced using an Ubuntu VirtualBox VM with Mininet-WiFi, mac80211_hwsim, Python 3, iperf3, and Linux tc. Each run produced an output folder containing raw ping logs, TCP iperf3 logs, UDP iperf3 logs, trial-level summary.csv, aggregate_summary.csv, and delta_table.csv. The main results in this paper were taken from the aggregate summaries and percent-change tables. 

```
sudo mn -c
sudo modprobe -r mac80211_hwsim
sudo modprobe mac80211_hwsim radios=80
sudo python3 m2_final_single_ap_smartbulb.py --experiment_id final_single_ap --experiment scaling --bulbs 5 --trials 10
sudo python3 m2_final_single_ap_smartbulb.py --experiment_id final_single_ap --experiment scaling --bulbs 10 --trials 10
sudo python3 m2_final_single_ap_smartbulb.py --experiment_id final_single_ap --experiment intensity --bulbs 10 --trials 10
```
