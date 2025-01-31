ir-eye
==============
# Introduction
This small python-script will enable you to receive power meter readings from an infrared eye in Home Assistant via MQTT.

I'm left with a power meter that only supports read-outs using infrared communication, and needed a _minimal_ setup to get those readings into my Home Assistant.

I searched and found [a blog post](https://www.dabbler.dk/index.php/2022/02/27/guest-writer-echelon-nes-smart-meters-nes-echelon-electrical-meter-connect-to-mqtt-home-assistant-via-ir/) that uses a slightly adapted version of [termineter](https://github.com/rsmusllp/termineter/wiki/GettingStarted) to fetch the values from the meter, write them to a file, and then use a node-RED flow to push the values to mqtt for Home Assistant to pick up. 
I tried it out and observed that if the readings stopped working, the node-RED flow would keep pushing the same values over and over, so heavily inspired, I ventured into making this python script and document my steps...

# Installation
The following step-by-step installation guide relies the following setup in place:
* Proxmox
* Mosquitto MQTT
* Home Assistant connected to MQTT
* A password that allows readouts received from the local electricity provider

Minimal editing is required to adapt the script to your environment...

Without further ado...

## Plug in the infrared eye
... in the USB port
## Create a basic debian container
I used [Debian LXC from Community-Scripts](https://community-scripts.github.io/ProxmoxVE/scripts?id=debian)

```bash
root@proxmox:~# bash -c "$(wget -qLO - https://github.com/community-scripts/ProxmoxVE/raw/main/ct/debian.sh)"
```

My selections were:
* unprivileged
* hostname=ir-eye
* 256MB memory
* disable ipv6


## Find the id of the infrared eye
```bash
root@proxmox:~# ls -l /dev/serial/by-id/`
```

For me that gave output along the lines of:
```adoc
    usb-FTDI_FT230X_Basic_UART_DQ00LLF3-if00-port0 -> ../../ttyUSB0
```

## Create device passthrough for the container
In the Proxmox web-interface, select the container (ir-eye)->`Resources`->`Add`->`Device Passthrough` and fill in `/dev/serial/by-id/usb-FTDI_FT230X_Basic_UART_DQ00LLF3-if00-port0`

Stop the ir-eye container

Start the ir-eye container

## Add dedicated user to mosquitto
Log in to the shell for your mosquitto/mqtt container or vm
```bash
root@mqtt:~# mosquitto_passwd /etc/mosquitto/passwd powermeterreader
root@mqtt:~# service mosquitto restart
```

## In the new container, create non-root user and fetch the python script
Log in to the shell for the newly created ir-eye proxmox-container
```bash
root@ir-eye:~# apt-get install python3-paho-mqtt python3-serial git
root@ir-eye:~# adduser meterreader
root@ir-eye:~# chown meterreader:root /dev/serial/by-id/usb-FTDI_FT230X_Basic_UART_DQ00LLF3-if00-port0
root@ir-eye:~# su - meterreader
meterreader@ir-eye:~$ git clone https://github.com/themeterreader/ir-eye.git
```
## Edit the parameters specific to your setup in the script
```bash
meterreader@ir-eye:~$ nano ir-eye/ir-meterreader.py
     # set parameters 
     #   meter-password from power company
     #   usb-device
     #   mosquitto credentials
     # Leave DEBUG=True for now
```
## Run the script manually to verify functionality
```bash
meterreader@ir-eye:~$ python3 ir-eye/ir-meterreader.py
```

<details>
    <summary>Output should look similar to this (click to expand)</summary>
<pre>
Sending message: ident
 --> sending 'EE0000000001201310'
 <-- received '06ee000000002500001000070f1131303233333032332020202020202020202020202020202020202020202058cb'
Sending message: logon
 --> sending 'EE002000000D500063666978656475736572208e11'
 <-- received '06ee0020000001008051'
Sending message: security
 --> sending 'EE00000000155137363734376234333762526562624162713432367013'
 <-- received '06ee0000000001001131'
Sending message: readtable23
 --> sending 'EE00200000083f0017000000000803f2'
 <-- received '06ee002000000c000008fb1304024d891a00fc92f2'
Sending message: readtable28
 --> sending 'EE00000000083f001C000000002894d5'
 <-- received '06ee000000002c00002800000000e7000000000000004700000056020000bc020000c1020000e0870300b18e0300d5840300f1950e'
Sending message: logoff
 --> sending 'EE0020000001521720'
 <-- received '06ee0020000001008051'
Sending message: terminate
 --> sending 'EE0000000001219a01'
 <-- received '06ee0000000001001131'
2025-01-01 12:00:00
{
current_L1 = 0.598
  current_L2 = 0.7000000000000001
  current_L3 = 0.705
  energy_fwd = 33821.691
  energy_rev = 1739.085
  power_fwd = 0
  power_rev = 231
  voltage_L1 = 231.392
  voltage_L2 = 233.137
  voltage_L3 = 230.613
}
</pre>
</details>
If you see any errors or traces, resolve those before continuing.

## Change DEBUG parameter s specific to your setup in the script
```bash
meterreader@ir-eye:~$ nano ir-eye/ir-meterreader.py     
     # DEBUG=False
```

## Add job to cron scheduler
This will run the job every minute
```bash
meterreader@ir-eye:~$ (crontab -l 2>/dev/null | grep -v ir-meterreader.py; echo "* * * * * /usr/bin/python3 /home/meterreader/ir-eye/ir-meterreader.py") | crontab -
```

If you want to get data only every 15 minutes, use this instead
```bash
meterreader@ir-eye:~$ (crontab -l 2>/dev/null | grep -v ir-meterreader.py; echo "*/15 * * * * /usr/bin/python3 /home/meterreader/ir-eye/ir-meterreader.py") | crontab -
```
