#!/usr/bin/env bash
# Per-client limits on the web ports (TCP 80 and 443) of the VPS, in the kernel firewall
# (nftables), before Caddy sees the traffic. Idempotent: running it again replaces the rules.
#
# Why: the application limits requests only on /api/v1, /mars-photos/api/v1 and /api/v2. The
# web interface, /assets, /docs, /health and the TLS handshakes themselves are free to a single
# client, and stock Caddy has no per-client limit. This table caps, per client:
#   - open connections (nft "ct count"), and
#   - the rate of NEW connections (a TLS handshake costs CPU).
# IPv4 is counted per address; IPv6 per /64 (one subscriber's LAN) and, with a larger budget,
# per /48 (one customer's whole allocation), the same split as the application's limiter.
# It does NOT stop a volume DDoS (many sources, or traffic that fills the link): that needs a
# CDN or scrubbing service in front of the server (deploy/README.md, "Edge limits").
#
# Own table `inet mars_limits`: the ufw, Docker and fail2ban tables are not touched, and the
# system `nftables.service` (which would flush the whole ruleset) is NOT used. SSH (22) is not
# matched. Loopback is exempt: host-check.sh, Docker health checks and the SSH tunnel use it.
#
# Usage (as root, on the server):
#   limits.sh show      print the rules that would be loaded
#   limits.sh check     render and validate with `nft -c`; changes nothing
#   limits.sh apply     write /etc/nftables.d/mars-limits.nft, validate, load, enable the
#                       boot unit mars-limits.service
#   limits.sh status    the loaded table with its counters, the unit state
#   limits.sh remove    delete the table, disable the unit, remove the file (the rollback)
# Numbers (environment, defaults in brackets; run `apply` again after changing them):
#   CONN_MAX_V4 (500)    open connections per IPv4 address
#   CONN_MAX_V6_64 (500) per IPv6 /64           CONN_MAX_V6_48 (1000) per IPv6 /48
#   CONN_MAX_ALL (1500)  open connections of ALL clients together on 80/443 (see below)
#   NEW_RATE_V4 (100)    new connections per second per IPv4 address, burst NEW_BURST_V4 (300)
#   NEW_RATE_V6_64 (100) burst NEW_BURST_V6_64 (300)
#   NEW_RATE_V6_48 (400) burst NEW_BURST_V6_48 (1000)
# CONN_MAX_ALL: about 0.44 MB of Caddy memory per held connection (measured, README "Edge limits"),
# so 1500 connections are about 660 MB of the 1 GB limit of the caddy container. Raise it only
# together with `mem_limit` in compose.yml (N x 0.44 MB must stay below about 70% of it).
# Sizing: the web server speaks HTTP/1.1 only (Caddyfile), so a browser keeps up to 6
# connections per host, 12 for solindex.net plus api.solindex.net; an ordinary visitor stays
# under 20. Memory: a client that never reads holds about 0.43 MB of Caddy memory per
# connection (stand measurement), so 500 connections of one address cost about 215 MB of the
# 1 GB Caddy limit; the number was kept at 500 because a lower one hurts visitors behind one NAT
# (20 browsers active together already use 240 connections). Carrier-grade NAT and universities put hundreds to a few
# thousand visitors behind one IPv4 address, so the numbers are loose on purpose: this is a
# brake on one source opening thousands of connections, not a per-person quota (the
# application limits requests per IP separately). A client over a limit has the first packet
# (SYN) of the extra connection dropped and retries; nothing is banned, and connections
# already open are never cut. The tool and directory are fixed (not taken from the
# environment): the script runs as root.
set -euo pipefail

NFT=nft
RULES_DIR=/etc/nftables.d
RULES_FILE="$RULES_DIR/mars-limits.nft"
UNIT=mars-limits.service
TABLE_NAME=mars_limits
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CONN_MAX_V4="${CONN_MAX_V4:-500}"
CONN_MAX_V6_64="${CONN_MAX_V6_64:-500}"
CONN_MAX_V6_48="${CONN_MAX_V6_48:-1000}"
CONN_MAX_ALL="${CONN_MAX_ALL:-1500}"
NEW_RATE_V4="${NEW_RATE_V4:-100}"
NEW_BURST_V4="${NEW_BURST_V4:-300}"
NEW_RATE_V6_64="${NEW_RATE_V6_64:-100}"
NEW_BURST_V6_64="${NEW_BURST_V6_64:-300}"
NEW_RATE_V6_48="${NEW_RATE_V6_48:-400}"
NEW_BURST_V6_48="${NEW_BURST_V6_48:-1000}"

for v in CONN_MAX_V4 CONN_MAX_V6_64 CONN_MAX_V6_48 CONN_MAX_ALL NEW_RATE_V4 NEW_BURST_V4 \
         NEW_RATE_V6_64 NEW_BURST_V6_64 NEW_RATE_V6_48 NEW_BURST_V6_48; do
  [[ "${!v}" =~ ^[1-9][0-9]{0,5}$ ]] || { echo "$v must be a positive integer, got '${!v}'" >&2; exit 2; }
done

die() { echo "$*" >&2; exit 1; }
need_root() { [[ "$(id -u)" -eq 0 ]] || die "run as root: sudo bash $0 $1"; }

render() {
  cat <<EOF
# Managed by deploy/vps/limits.sh: do not edit by hand, run the script again instead.
# The first two statements (create if missing, then delete) make loading idempotent inside ONE
# atomic transaction: the whole new table replaces the old one, or nothing changes.
table inet $TABLE_NAME
delete table inet $TABLE_NAME

table inet $TABLE_NAME {
	# Dynamic sets filled by the rules below, one entry per client key. "ct count" keeps a
	# counter per key that follows the number of tracked connections (the kernel refuses
	# "ct count" in a set with a timeout, so these have none and use \`add\`); the rate rules
	# keep a token bucket per key, and \`update\` refreshes its timeout on every hit so idle
	# keys are forgotten.
	set conn_v4    { type ipv4_addr; flags dynamic; size 262144; }
	set conn_v6_64 { type ipv6_addr; flags dynamic; size 262144; }
	set conn_v6_48 { type ipv6_addr; flags dynamic; size 262144; }
	# One entry only: the key is the protocol (always tcp here), so the count is global.
	set conn_all   { type inet_proto; flags dynamic; size 4; }
	set rate_v4    { type ipv4_addr; flags dynamic,timeout; timeout 2m; size 262144; }
	set rate_v6_64 { type ipv6_addr; flags dynamic,timeout; timeout 2m; size 262144; }
	set rate_v6_48 { type ipv6_addr; flags dynamic,timeout; timeout 2m; size 262144; }

	chain input {
		# After connection tracking (-200), before ufw's filter chains (0).
		type filter hook input priority -10; policy accept;

		iifname "lo" accept

		# Only the first packet of a NEW connection is judged; packets of established
		# connections are not matched, so a limit never cuts a transfer in progress.
		# Each rule matches only when its key is OVER the limit, then counts and drops.

		# 1. Rate of new connections per client. First, so that a SYN dropped here is not
		# counted as a connection below.
		tcp dport { 80, 443 } ct state new update @rate_v4 { ip saddr limit rate over $NEW_RATE_V4/second burst $NEW_BURST_V4 packets } counter drop comment "rate v4"
		tcp dport { 80, 443 } ct state new update @rate_v6_64 { ip6 saddr & ffff:ffff:ffff:ffff:: limit rate over $NEW_RATE_V6_64/second burst $NEW_BURST_V6_64 packets } counter drop comment "rate v6 /64"
		tcp dport { 80, 443 } ct state new update @rate_v6_48 { ip6 saddr & ffff:ffff:ffff:: limit rate over $NEW_RATE_V6_48/second burst $NEW_BURST_V6_48 packets } counter drop comment "rate v6 /48"

		# 2. Open connections per client.
		tcp dport { 80, 443 } ct state new add @conn_v4 { ip saddr ct count over $CONN_MAX_V4 } counter drop comment "conn v4"
		tcp dport { 80, 443 } ct state new add @conn_v6_64 { ip6 saddr & ffff:ffff:ffff:ffff:: ct count over $CONN_MAX_V6_64 } counter drop comment "conn v6 /64"
		tcp dport { 80, 443 } ct state new add @conn_v6_48 { ip6 saddr & ffff:ffff:ffff:: ct count over $CONN_MAX_V6_48 } counter drop comment "conn v6 /48"

		# 3. Open connections of ALL clients together. Below the per-client limits, above any
		# normal load: under a distributed overload new connections are refused (SYN dropped)
		# instead of Caddy being killed for memory (README, "Edge limits"). Connections already
		# open are not cut. Counted last, so connections refused above are not counted.
		tcp dport { 80, 443 } ct state new add @conn_all { meta l4proto ct count over $CONN_MAX_ALL } counter drop comment "conn all"
	}
}
EOF
}

# Renders to a temporary file next to the final one and validates it (`nft -c`: parse and
# type-check, nothing is loaded). Prints the path of the validated file.
render_checked() {
  local tmp
  tmp="$(mktemp "${RULES_FILE}.XXXXXX" 2>/dev/null || mktemp)"
  render >"$tmp"
  if ! "$NFT" -c -f "$tmp"; then
    echo "nft rejected the rules (file kept for inspection: $tmp); nothing was changed" >&2
    exit 1
  fi
  printf '%s\n' "$tmp"
}

cmd="${1:-}"
case "$cmd" in
  show)
    render
    ;;

  check)
    need_root check
    command -v "$NFT" >/dev/null || die "nft not found (apt-get install nftables)"
    tmp="$(render_checked)"
    rm -f "$tmp"
    echo "OK: the rules are valid (nothing loaded). Next: sudo bash $0 apply"
    ;;

  apply)
    need_root apply
    command -v "$NFT" >/dev/null || die "nft not found (apt-get install nftables)"
    install -d -m 755 "$RULES_DIR"
    tmp="$(render_checked)"
    install -m 644 "$tmp" "$RULES_FILE"
    rm -f "$tmp"
    "$NFT" -f "$RULES_FILE"
    echo "loaded: $RULES_FILE"
    unit_src="$SCRIPT_DIR/systemd/$UNIT"
    if [[ -f "$unit_src" ]]; then
      install -m 644 "$unit_src" "/etc/systemd/system/$UNIT"
      systemctl daemon-reload
      systemctl enable "$UNIT"
      # Already loaded above; starting the unit only records it as active (idempotent load).
      systemctl start "$UNIT"
      echo "boot unit $UNIT enabled"
    else
      echo "WARNING: $unit_src missing: the rules are active now but will NOT survive a reboot" >&2
    fi
    echo "Check: sudo bash $0 status"
    ;;

  status)
    need_root status
    if "$NFT" list table inet "$TABLE_NAME" 2>/dev/null; then
      :
    else
      echo "table inet $TABLE_NAME is NOT loaded"
    fi
    echo
    systemctl is-enabled "$UNIT" 2>/dev/null || true
    systemctl is-active "$UNIT" 2>/dev/null || true
    ;;

  remove)
    need_root remove
    "$NFT" delete table inet "$TABLE_NAME" 2>/dev/null || echo "table was not loaded"
    systemctl disable --now "$UNIT" 2>/dev/null || true
    rm -f "$RULES_FILE" "/etc/systemd/system/$UNIT"
    systemctl daemon-reload
    echo "removed: no per-client limits are active"
    ;;

  *)
    sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//' >&2
    exit 2
    ;;
esac
