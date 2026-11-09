# Addressing and subnets (self-written study notes)

- A /24 network has a 24-bit prefix and leaves 254 usable host addresses.
- A /26 network holds 64 addresses in total, which is 62 usable hosts.
- The block 192.0.2.0/24 is reserved for documentation and examples.
- RFC 1918 sets aside three private address ranges that are not routed on the public internet.
- The loopback address 127.0.0.1 always refers to the local host itself.
- The default gateway is the router a host uses to reach addresses outside its own subnet.
- A subnet mask separates the network portion of an address from the host portion.
- An IPv6 address is 128 bits long and is written as eight groups of hexadecimal digits.
- In the subnet 203.0.113.0/24 the broadcast address is 203.0.113.255.
- A host that shows a 169.254 address was self-assigned one because no DHCP server answered.
- DHCP gives a client an address, mask, gateway and DNS server for a limited lease time.
- NAT lets many private hosts share one public address by rewriting addresses and ports.
- A VLAN splits one physical switch into separate broadcast domains.
- Adding one bit to the prefix length halves the number of addresses in a subnet.
