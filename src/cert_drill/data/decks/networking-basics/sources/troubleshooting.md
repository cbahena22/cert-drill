# Troubleshooting (self-written study notes)

- The ping command sends ICMP echo requests to test whether a host is reachable.
- The traceroute command lists each router hop on the path to a destination.
- The nslookup command queries DNS to show which address a name resolves to.
- The ipconfig /all command on Windows shows the address, mask, gateway and DNS server of each adapter.
- A successful ping to 127.0.0.1 shows that the local TCP/IP stack works.
- If a host can ping an address but not a name, the usual cause is a DNS problem.
- A duplicate IP address on a network causes intermittent connectivity for both hosts.
- A wrong subnet mask can leave a host unable to reach machines on its own network.
- Start troubleshooting at the physical layer by checking cables and link lights.
- A packet capture shows the actual packets on the wire and helps confirm what really happened.
- The netstat command lists open connections and listening ports on a host.
- A request that times out suggests a dropped packet, while a refused connection means a host answered and rejected it.
- Change one thing at a time and record it so that a fix can be undone.
- The ARP cache holds recent IP to MAC mappings and can be listed with arp -a.
