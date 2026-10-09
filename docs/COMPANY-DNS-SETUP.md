# Company-Wide DNS Setup — make `r/` work for everyone

Goal: everyone in the company types `http://r/hr-policy` (or
`http://r.company.com/hr-policy`) and lands on the Redirector, with no
per-laptop hosts-file edits.

There are two layers to this, and most companies need **both**:

| Layer | What it does | Where you configure it |
|---|---|---|
| **Public Cloud DNS** | `r.company.com` resolves on the internet / over VPN to your server | Cloudflare, Route 53, Azure DNS, Google Cloud DNS, GoDaddy, Namecheap… |
| **Router / Office DNS** | Bare `r` (single-label name) resolves inside the office LAN | Your office router, firewall, Pi-hole / AdGuard, or Windows AD DNS |

> **Rule of thumb:** use `r.company.com` as the canonical, always-works URL.
> Add bare `r` as a convenience shortcut on the office network. Bare single-label
> names never work reliably over public DNS alone — browsers and public
> resolvers treat them as search queries — so the router/internal-DNS step is
> what makes `http://r/` actually work company-wide.

Assumed example values (replace with yours):

- Server LAN IP: `192.168.1.10`
- Server public IP: `203.0.113.10`
- Domain: `company.com`
- Short names: `r` → `192.168.1.10` (LAN), `r.company.com` → `203.0.113.10` (public)

---

## 1. Deploy the server first

1. Deploy Redirector on a central machine/VM with a **static IP** (LAN static
   lease + static public IP / Elastic IP).
2. Run it behind a reverse proxy if you want clean port-80/443 URLs (see
   `README.md` → *Reverse Proxy Example (Nginx)*).
3. Confirm it answers on the IP before touching DNS:

```sh
curl -sI http://192.168.1.10/health | head -1   # expect: HTTP/1.1 200
curl http://192.168.1.10/api/r-status           # hostname diagnostics
```

---

## 2. Public Cloud DNS — `r.company.com` for everyone, everywhere

Create one **A record** in whatever holds your public domain. Pick the tab
that matches your provider; the record is the same everywhere:

- **Name/Host:** `r`
- **Type:** `A`
- **Value/Points to:** `203.0.113.10` (your server's public IP)
- **TTL:** `300` (5 min while testing, raise to `3600` once stable)

### 2a. Cloudflare

1. Domains → `company.com` → **DNS** → **Add record**.
2. Type `A`, Name `r`, IPv4 address `203.0.113.10`, TTL Auto, Proxy **OFF
   (DNS only)** for the first test — orange-cloud proxying hides the origin
   and can break plain-HTTP `r/` setups. Turn proxying back on only after
   `http://r.company.com/` works with proxy off.
3. Verify: `nslookup r.company.com` should return `203.0.113.10`.

### 2b. AWS Route 53

1. Route 53 → Hosted zones → `company.com` → **Create record**.
2. Record name `r`, record type `A`, value `203.0.113.10`, TTL `300`.
3. If the server is behind an ELB/ALB, use an **Alias A record** to the load
   balancer instead of a plain IP.
4. Verify: `dig r.company.com +short` → `203.0.113.10`.

### 2c. Azure DNS

1. DNS zones → `company.com` → **+ Record set**.
2. Name `r`, type `A`, IP `203.0.113.10`, TTL `300`.
3. Verify: `nslookup r.company.com`.

### 2d. Google Cloud DNS

1. Cloud DNS → zone for `company.com` → **Add record**.
2. DNS name `r.company.com.`, type `A`, IPv4 `203.0.113.10`, TTL `300`.
3. Verify: `dig r.company.com +short`.

### 2e. GoDaddy / Namecheap / other registrars

1. DNS Management → add record: Host `r`, Type `A`, Points to
   `203.0.113.10`, TTL `1/2 hour` (lowest available while testing).
2. Wait for propagation (up to a few hours on some registrars), then verify
   with `nslookup r.company.com 8.8.8.8`.

### HTTPS note — `https://r/` vs `https://r.company.com/`

`http://r.company.com/` works immediately. For `https://`, terminate TLS at
your reverse proxy (Nginx + certbot / Caddy) with a cert for `r.company.com`.

Bare `https://r/` with a browser-trusted certificate is **not possible via any
public CA** — no public CA (Let's Encrypt included) issues certs for
single-label names like `r`. This is a CA/Browser Forum rule, not a Redirector
limitation. Your options:

1. **Recommended: both.** `http://r/*` on the LAN (plain HTTP via the router
   override in §3) + `https://r.company.com/*` as the real encrypted URL.
2. **Type `r`, land on HTTPS.** Redirect bare-`r` HTTP to the FQDN at Nginx —
   no cert hacks, one extra hop:
   ```nginx
   server {
       listen 80;
       server_name r;
       return 301 https://r.company.com$request_uri;
   }
   ```
3. **Native `https://r/` — LAN-only, needs a private CA.** Run `mkcert`
   (small team) or AD Certificate Services / Smallstep (larger org), issue a
   cert with SAN `r`, terminate it at Nginx, and push the root cert to every
   company device via GPO/MDM. Any device missing the root gets a cert
   warning. Only worth it if IT already runs an internal CA.

---

## 3. Router / Office DNS — bare `r` for the LAN

This is the step that replaces editing every laptop's hosts file. You add **one**
DNS override on the box every office device already asks for DNS, then all of
them resolve `r` to the server.

### 3a. What to configure (any router)

1. Give the Redirector server a **static DHCP lease** (e.g. always
   `192.168.1.10`) so the override never goes stale.
2. In the router admin → DNS / DHCP settings, add a **local DNS record /
   host override**:

   ```text
   r              →  192.168.1.10
   r.company.com  →  192.168.1.10   (split-horizon: LAN clients get the LAN IP)
   ```

   The second line is important: without it, office laptops resolve
   `r.company.com` to the *public* IP and hairpin out through the WAN (slow,
   and broken on routers without NAT loopback).
3. Make sure DHCP advertises the **router itself** (or your Pi-hole / AD DNS)
   as the clients' DNS server — not `8.8.8.8` directly. Clients that use
   external DNS bypass your override entirely.
4. On each client, flush DNS once (`ipconfig /flushdns`, `sudo dscacheutil
   -flushcache`, `sudo systemd-resolve --flush-caches`) and test:

```sh
nslookup r            # should return 192.168.1.10
ping -c1 r
curl -sI http://r/health | head -1
```

### 3b. Recipes per platform

**OpenWrt (dnsmasq) — most common office-router firmware:**

```sh
# Network → DHCP and DNS → Static Leases: pin server MAC to 192.168.1.10
# Network → DHCP and DNS → Hostnames: Add `r` → 192.168.1.10
# or via /etc/dnsmasq.conf:
address=/r/192.168.1.10
address=/r.company.com/192.168.1.10
/etc/init.d/dnsmasq restart
```

**pfSense / OPNsense:**
Services → DNS Resolver → **Host Overrides** → Add: Host `r`, Domain
`company.com` (and a second entry Host `r` with empty domain for bare `r`),
IP `192.168.1.10`. Apply, then Services → DHCP Server → set DNS to the
firewall IP.

**UniFi (UDM / USG):**
Settings → Networks → DHCP → set DNS to your internal resolver, then
Settings → Routing → **DNS → Static DNS Entries**: `r` → `192.168.1.10`,
`r.company.com` → `192.168.1.10`. (On older UniFi OS this lives under
Services → DNS → DNSmasq options with `address=/r/192.168.1.10`.)

**MikroTik (RouterOS):**

```routeros
/ip dns static add name=r address=192.168.1.10
/ip dns static add name=r.company.com address=192.168.1.10
/ip dhcp-server network set 0 dns-server=192.168.1.1
```

**Stock TP-Link / ASUS / Netgear:**
Advanced → DHCP → set Primary DNS to the router IP; LAN → **DNS / Host
mapping** (sometimes called "Manual DNS" or "Address Reservation + DNS"):
add `r` → `192.168.1.10`. If your stock firmware has no local-DNS feature,
put a $35 Pi-hole / AdGuard box in front of it (next section) — that is the
standard workaround.

**Pi-hole / AdGuard Home (recommended when the router can't do it):**
Pi-hole → Local DNS → DNS Records → `r` → `192.168.1.10`,
`r.company.com` → `192.168.1.10`. AdGuard → Filters → DNS rewrites → same
two entries. Then point the router's DHCP DNS at the Pi-hole IP.

**Windows Server AD DNS:**
DNS Manager → Forward Lookup Zone `company.com` → New Host (A): `r` →
`192.168.1.10`. For bare `r`, add a forward zone named `r` with an empty-name
A record → `192.168.1.10`, or push `company.com` as the DNS suffix search
list via Group Policy so typing `r` auto-tries `r.company.com`.

### 3c. The browser-search trap (read this before filing a bug)

Single-letter `r` looks like a search query to Chrome/Edge. Two fixes:

1. Users must type the full `http://r/shortcut` (with `http://` and trailing
   path) **once** — afterwards the browser learns `r` is a host.
2. Push `company.com` as a **DNS search domain** via DHCP/GPO (`r` then
   resolves as `r.company.com` even for stub resolvers that won't resolve
   single-label names). On Windows this is DHCP option 015 / GPO
   *DNS Suffix Search List*; on UniFi/OpenWrt it is the DHCP "Domain name".

The app's `GET /api/r-status` endpoint reports whether the requesting client
resolved `r` correctly — link it in your rollout mail so users can self-check.

---

## 4. Recommended end state

```text
Laptop in office ──DNS──▶ router / Pi-hole / AD DNS
   r               ──▶  192.168.1.10  (override)
   r.company.com   ──▶  192.168.1.10  (split-horizon override)

Laptop at home / on VPN ──DNS──▶ public DNS
   r.company.com   ──▶  203.0.113.10  (Cloudflare / Route53 / Azure / GCP)
   (bare r only works on VPN if the VPN pushes the office DNS)
```

Rollout checklist:

- [ ] Server has static LAN + public IP, answers on the IP.
- [ ] Public A record `r.company.com` → public IP, verified with
  `nslookup r.company.com 8.8.8.8`.
- [ ] Router/internal override `r` + `r.company.com` → LAN IP, verified with
  `nslookup r` on a DHCP client.
- [ ] DHCP advertises the internal resolver; search domain `company.com`
  pushed.
- [ ] Reverse proxy + TLS for `https://r.company.com/` (optional but
  recommended for remote/VPN users).
- [ ] Admin password rotated from the generated default; `/admin` restricted
  by VPN/IP allowlist if the instance is internet-facing.
- [ ] Rollout mail tells the team the canonical URL (`r.company.com`) plus
  the `http://r/` shortcut and the `http://r/` first-visit note.

---

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `r.company.com` works on mobile data, not in office | No split-horizon override; router lacks NAT loopback | Add LAN override `r.company.com` → `192.168.1.10` (§3a) |
| Bare `r` opens a Google search | Browser search hijack / resolver won't do single-label | Type `http://r/` once; push `company.com` search domain (§3c) |
| `nslookup r` returns `NXDOMAIN` but `r.company.com` works | Client uses `8.8.8.8`, bypassing router DNS | Set DHCP DNS to router/Pi-hole IP; flush client cache |
| Record works for you, not for a colleague | Stale DNS cache / old DHCP lease | `ipconfig /flushdns`, renew lease, check they use office DNS |
| `r` worked, then broke after a reboot | Server DHCP lease changed | Pin a static lease for the server (§3a step 1) |
| Cloudflare shows the wrong IP | Orange-cloud proxy cached / wrong record | Set record to DNS-only while testing; check Audit Log |
| HTTPS cert error on bare `r` | No public CA issues single-label certs | Use `http://r/` on LAN, `https://r.company.com/` remotely — or deploy an internal CA |

*Related: `README.md` → Company-Wide Installation & Team Usage (overview),
`README.md` → Hostname Setup for r/ Shortcuts (single-machine hosts-file
method), `docs/DATA-PERSISTENCE.md` (what to back up before exposing a
server).*
