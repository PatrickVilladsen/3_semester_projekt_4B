#Dette er opsætningen til at gøre vores Raspberry Pi 5 til et Wifi access point.
#Dette gør vi for at kunne benytte den som vores mqtt broker.
#Dette er et script som ville køre alle kommandoerne som jeg kørte for at sætte det op.

#Først sørger vi for at systemet er opdateret

sudo apt update
sudo apt upgrade -y

#så installerer vi mosquitto, som fungerer som vores mqtt broker.
sudo apt install mosquitto mosquitto-clients
sudo systemctl enable mosquitto
sudo systemctl start mosquitto

#Så installerer vi et python mqtt bibliotek
pip3 install paho-mqtt

#Vi sørger først fo at der ikke i forvejen er et hotspot oprettet
sudo nmcli con delete hotspot

#nu gør vi så vores Raspberry pi 5 til et wifi hotspot, så vores ESP32'er kan forbinde til den

sudo nmcli con add type wifi ifname wlan0 con-name hotspot autoconnect yes ssid RaspberryPi_AP

#SSID kan ændres og burde faktisk være auto-genereret til hvis man havde flere
#Raspberry Pi's tæt på hinanden, så ville man undgå konflikt mellem dem
#Det ville dog kræve at vi fik gjort at værdierne i koden blev tilpasset med de autogenereret SSID'er og passwords.

#Nå men videre.

sudo nmcli connection modify hotspot 802-11-wireless.mode ap

sudo nmcli connection modify hotspot 802-11-wireless.band bg

sudo nmcli connection modify hotspot 802-11-wireless.channel 6

sudo nmcli connection modify hotspot ipv4.method shared

sudo nmcli connection modify hotspot ipv4.addresses 192.168.4.1/24

sudo nmcli connection modify hotspot wifi-sec.proto rsn

sudo nmcli connection modify hotspot wifi-sec.pairwise ccmp

sudo nmcli connection modify hotspot wifi-sec.group ccmp

sudo nmcli connection modify hotspot wifi-sec.key-mgmt wpa-psk

sudo nmcli connection modify hotspot wifi-sec.psk "RPI12345"


sudo nmcli con down hotspot

sudo nmcli con up hotspot

#Nu er Raspberry Pi'en klar til at køre koden, samt så kan ESP32'erne nu tilgå den med MQTT