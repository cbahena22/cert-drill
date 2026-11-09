# Protocols and ports (self-written study notes)

- DNS translates host names into IP addresses and listens on port 53.
- HTTPS runs over TCP port 443 and wraps HTTP in TLS.
- SSH provides encrypted remote login on TCP port 22.
- TCP sets up a connection with a three-way handshake: SYN, SYN-ACK, ACK.
- UDP is connectionless and does not retransmit lost packets.
- ARP maps an IPv4 address to a MAC address on the local network.
- ICMP carries error and diagnostic messages such as echo request and echo reply.
- A DHCP server listens on UDP port 67.
- SMTP moves mail between servers, usually on TCP port 25.
- NTP keeps clocks in step and uses UDP port 123.
- A switch forwards frames by MAC address, while a router forwards packets by IP address.
- Telnet sends everything, including passwords, in clear text on TCP port 23.
- The transport layer is layer 4 of the OSI model and holds TCP and UDP.
- HTTP status code 404 means the server could not find the requested resource.
