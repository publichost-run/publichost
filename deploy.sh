#!/usr/bin/env bash
# PublicHost — deploy your own SSH tunnel service with one command.
#
#   ./deploy.sh                  interactive install / reconfigure / upgrade
#   ./deploy.sh --help           all commands and options
#
# Requirements: a Linux server with a public IPv4 address, ports 80/443 and
# the SSH port free, and a domain whose DNS is managed by Cloudflare.
# Docker is installed automatically (with confirmation) if missing.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT/.env.docker"
ENV_TEMPLATE="$ROOT/.env.docker.example"
SECRET_DIR="$ROOT/secrets"
CF_TOKEN_FILE="$SECRET_DIR/cloudflare_dns_api_token"
TRAEFIK_TEMPLATE="$ROOT/deploy/traefik/dynamic.yml.tmpl"
TRAEFIK_CONFIG="$ROOT/deploy/traefik/dynamic.yml"
CF_HELPER="$ROOT/deploy/cloudflare_dns.py"
BACKEND_IMAGE="publichost-backend:latest"
PROD_CA="https://acme-v02.api.letsencrypt.org/directory"
STAGING_CA="https://acme-staging-v02.api.letsencrypt.org/directory"

# ----------------------------------------------------------------- output ---
if [[ -t 1 ]]; then
  C_RESET=$'\033[0m' C_BOLD=$'\033[1m' C_DIM=$'\033[2m'
  C_RED=$'\033[31m' C_GREEN=$'\033[32m' C_YELLOW=$'\033[33m' C_BLUE=$'\033[34m'
else
  C_RESET="" C_BOLD="" C_DIM="" C_RED="" C_GREEN="" C_YELLOW="" C_BLUE=""
fi
step() { printf '\n%s==>%s %s%s%s\n' "$C_BLUE" "$C_RESET" "$C_BOLD" "$*" "$C_RESET"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '    %s✔%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '    %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
die()  { printf '\n%sError:%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }
trap 'die "command failed (line $LINENO): $BASH_COMMAND"' ERR

usage() {
  cat <<EOF
PublicHost deploy script

Usage: ./deploy.sh [command] [options]

Commands:
  install      Configure (first run), build and start the stack (default)
  update       git pull, rebuild and restart with the existing configuration
  status       Show container status
  logs [svc]   Follow logs (all services, or one: publichost, api, traefik, ...)
  restart      Restart all services
  down         Stop the stack (data is kept)
  uninstall    Stop the stack and DELETE all data (database, host key, certs)

Options (install):
  --domain DOMAIN      Base domain, e.g. tunnel.example.com
  --email EMAIL        Email for Let's Encrypt notices
  --cf-token TOKEN     Cloudflare API token (or env CF_API_TOKEN; prompted if absent)
  --ssh-port PORT      Public SSH port for clients (default 2222)
  --ip IPV4            Server public IPv4 (auto-detected by default)
  --staging            Use the Let's Encrypt staging CA (testing, untrusted certs)
  --skip-dns           Do not create/update DNS records in Cloudflare
  --reconfigure        Ask all questions again even if .env.docker exists
  -y, --yes            Non-interactive: never prompt, accept defaults
  -h, --help           Show this help
EOF
}

# ------------------------------------------------------------------- args ---
COMMAND="install"
OPT_DOMAIN="" OPT_EMAIL="" OPT_SSH_PORT="" OPT_IP=""
OPT_CF_TOKEN="${CF_API_TOKEN:-}"
OPT_STAGING=0 OPT_SKIP_DNS=0 OPT_RECONFIGURE=0 ASSUME_YES=0
EXTRA_ARGS=()

if [[ $# -gt 0 && $1 != -* ]]; then
  COMMAND="$1"
  shift
fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) OPT_DOMAIN="${2:?--domain needs a value}"; shift 2 ;;
    --email) OPT_EMAIL="${2:?--email needs a value}"; shift 2 ;;
    --cf-token) OPT_CF_TOKEN="${2:?--cf-token needs a value}"; shift 2 ;;
    --ssh-port) OPT_SSH_PORT="${2:?--ssh-port needs a value}"; shift 2 ;;
    --ip) OPT_IP="${2:?--ip needs a value}"; shift 2 ;;
    --staging) OPT_STAGING=1; shift ;;
    --skip-dns) OPT_SKIP_DNS=1; shift ;;
    --reconfigure) OPT_RECONFIGURE=1; shift ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) EXTRA_ARGS+=("$1"); shift ;;
  esac
done

# ---------------------------------------------------------------- prompts ---
interactive() { [[ $ASSUME_YES -eq 0 && -t 0 ]]; }

# ask VAR "Question" "default"
ask() {
  local __var=$1 question=$2 default=${3:-} answer
  if ! interactive; then
    [[ -n $default ]] || die "$question: no value given (pass it as an option)"
    printf -v "$__var" '%s' "$default"
    return
  fi
  if [[ -n $default ]]; then
    read -rp "    $question [$default]: " answer
  else
    read -rp "    $question: " answer
  fi
  printf -v "$__var" '%s' "${answer:-$default}"
}

ask_secret() {
  local __var=$1 question=$2 answer
  interactive || die "$question: no value given (pass it as an option)"
  read -rsp "    $question: " answer
  echo
  printf -v "$__var" '%s' "$answer"
}

confirm() {
  local question=$1 default=${2:-y} answer
  if ! interactive; then
    [[ $default == y ]]
    return
  fi
  local hint="[Y/n]"
  [[ $default == y ]] || hint="[y/N]"
  read -rp "    $question $hint " answer
  answer=${answer:-$default}
  [[ $answer =~ ^[Yy] ]]
}

# ------------------------------------------------------------- env file ----
env_get() {
  [[ -f $ENV_FILE ]] || return 0
  { grep -E "^$1=" "$ENV_FILE" || true; } | tail -n 1 | cut -d= -f2-
}

env_set() {
  local tmp
  tmp="$(mktemp "$ENV_FILE.XXXXXX")"
  K="$1" V="$2" awk '
    BEGIN { k = ENVIRON["K"]; v = ENVIRON["V"]; done = 0 }
    index($0, k "=") == 1 { if (!done) { print k "=" v; done = 1 }; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$ENV_FILE" >"$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$ENV_FILE"
}

# --------------------------------------------------------------- docker ----
SUDO=()
DOCKER=(docker)

need_sudo() {
  if [[ $EUID -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || die "root privileges required (run as root or install sudo)"
    SUDO=(sudo)
  fi
}

compose() {
  "${DOCKER[@]}" compose --project-directory "$ROOT" -f "$ROOT/docker-compose.yml" \
    --env-file "$ENV_FILE" "$@"
}

project_name() {
  # Mirrors compose's default project name: the directory name, normalized.
  basename "$ROOT" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-'
}

ensure_docker() {
  step "Checking Docker"
  if ! command -v docker >/dev/null 2>&1; then
    warn "Docker is not installed."
    confirm "Install Docker now using the official script (get.docker.com)?" y \
      || die "Docker is required: https://docs.docker.com/engine/install/"
    need_sudo
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    "${SUDO[@]}" sh /tmp/get-docker.sh
    rm -f /tmp/get-docker.sh
    "${SUDO[@]}" systemctl enable --now docker >/dev/null 2>&1 || true
  fi
  if ! docker info >/dev/null 2>&1; then
    if [[ $EUID -ne 0 ]] && command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
      DOCKER=(sudo docker)
    else
      die "cannot talk to the Docker daemon (is it running? are you in the docker group?)"
    fi
  fi
  "${DOCKER[@]}" compose version >/dev/null 2>&1 \
    || die "Docker Compose v2 is required (the 'docker compose' plugin)"
  ok "$("${DOCKER[@]}" --version)"
}

# ------------------------------------------------------------ preflight ----
preflight() {
  step "Checking the system"
  [[ $(uname -s) == Linux ]] || die "deploy.sh supports Linux servers only"
  for cmd in curl awk grep; do
    command -v "$cmd" >/dev/null 2>&1 || die "'$cmd' is required"
  done
  [[ -f $ENV_TEMPLATE && -f $TRAEFIK_TEMPLATE ]] \
    || die "run deploy.sh from a full checkout of the repository"
  ok "$(uname -sr)"
}

port_in_use() {
  local port=$1
  if command -v ss >/dev/null 2>&1; then
    [[ -n $(ss -Hltn "sport = :$port" 2>/dev/null) ]]
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null
  fi
}

container_running() {
  [[ $("${DOCKER[@]}" inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true) == true ]]
}

check_ports() {
  local ssh_port=$1
  step "Checking ports"
  if ! container_running publichost-traefik; then
    for port in 80 443; do
      if port_in_use "$port"; then
        die "port $port is already in use (nginx/apache/caddy?). PublicHost's Traefik
       needs ports 80 and 443: stop that service, then re-run ./deploy.sh"
      fi
    done
  fi
  if ! container_running publichost && port_in_use "$ssh_port"; then
    if [[ $ssh_port == 22 ]]; then
      die "port 22 is used by the server's own sshd. Either pick another port
       (--ssh-port 2222) or move sshd to another port first (and make sure you
       can still log in!), then re-run ./deploy.sh"
    fi
    die "port $ssh_port is already in use; pick another one with --ssh-port"
  fi
  ok "80, 443 and $ssh_port are available"
}

open_firewall() {
  local ssh_port=$1
  if [[ $EUID -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || return 0
    SUDO=(sudo)
  fi
  if command -v ufw >/dev/null 2>&1 && "${SUDO[@]}" ufw status 2>/dev/null | grep -q "Status: active"; then
    step "Opening firewall ports (ufw)"
    for port in 80 443 "$ssh_port"; do
      "${SUDO[@]}" ufw allow "$port/tcp" >/dev/null
    done
    ok "ufw allows 80, 443, $ssh_port"
  elif command -v firewall-cmd >/dev/null 2>&1 && "${SUDO[@]}" firewall-cmd --state >/dev/null 2>&1; then
    step "Opening firewall ports (firewalld)"
    for port in 80 443 "$ssh_port"; do
      "${SUDO[@]}" firewall-cmd --permanent --add-port="$port/tcp" >/dev/null
    done
    "${SUDO[@]}" firewall-cmd --reload >/dev/null
    ok "firewalld allows 80, 443, $ssh_port"
  fi
  info "${C_DIM}Cloud firewall (AWS security group, Hetzner firewall, ...) must also allow 80, 443, $ssh_port.${C_RESET}"
}

# ------------------------------------------------------------- validate ----
valid_domain() {
  [[ $1 =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]
}
valid_email() { [[ $1 =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]]; }
valid_port() { [[ $1 =~ ^[0-9]+$ ]] && ((10#$1 >= 1 && 10#$1 <= 65535)); }
valid_ipv4() {
  [[ $1 =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  local octet
  for octet in "${BASH_REMATCH[@]:1}"; do ((10#$octet <= 255)) || return 1; done
}

random_secret() { head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

detect_ip() {
  local url ip
  for url in https://api.ipify.org https://ipv4.icanhazip.com https://ifconfig.me/ip; do
    ip="$(curl -4fsS --max-time 5 "$url" 2>/dev/null | tr -d '[:space:]' || true)"
    if valid_ipv4 "$ip"; then
      printf '%s' "$ip"
      return
    fi
  done
}

# ------------------------------------------------------------ configure ----
DOMAIN="" EMAIL="" SSH_PORT="" CF_TOKEN="" PUBLIC_IP=""

configure() {
  step "Configuration"
  local fresh=0
  if [[ ! -f $ENV_FILE ]]; then
    fresh=1
    cp "$ENV_TEMPLATE" "$ENV_FILE"
  fi
  chmod 600 "$ENV_FILE"

  local cur_domain cur_email cur_port
  cur_domain="$(env_get PUBLICHOST_BASE_DOMAIN)"
  cur_email="$(env_get PUBLICHOST_ACME_EMAIL)"
  cur_port="$(env_get PUBLICHOST_EXTERNAL_SSH_PORT)"
  [[ $cur_domain == example.com ]] && cur_domain=""
  [[ $cur_email == admin@example.com ]] && cur_email=""
  local reask=$((fresh || OPT_RECONFIGURE))

  # Domain
  DOMAIN="${OPT_DOMAIN:-}"
  if [[ -z $DOMAIN ]]; then
    if [[ -n $cur_domain && $reask -eq 0 ]]; then
      DOMAIN="$cur_domain"
    else
      info "Tunnels will be https://<random-name>.<domain>. Use a domain (or a"
      info "subdomain such as tunnel.example.com) whose DNS is on Cloudflare."
      ask DOMAIN "Domain" "$cur_domain"
    fi
  fi
  DOMAIN="$(printf '%s' "$DOMAIN" | tr '[:upper:]' '[:lower:]')"
  DOMAIN="${DOMAIN%.}"
  valid_domain "$DOMAIN" || die "invalid domain: '$DOMAIN'"

  # Email
  EMAIL="${OPT_EMAIL:-}"
  if [[ -z $EMAIL ]]; then
    if [[ -n $cur_email && $reask -eq 0 ]]; then
      EMAIL="$cur_email"
    else
      ask EMAIL "Email for Let's Encrypt" "$cur_email"
    fi
  fi
  valid_email "$EMAIL" || die "invalid email: '$EMAIL'"

  # SSH port
  SSH_PORT="${OPT_SSH_PORT:-}"
  if [[ -z $SSH_PORT ]]; then
    if [[ -n $cur_port && $reask -eq 0 ]]; then
      SSH_PORT="$cur_port"
    else
      info "Clients connect with 'ssh -p <port> ...'. Port 22 only works if the"
      info "server's own sshd has been moved to another port."
      ask SSH_PORT "Public SSH port for clients" "${cur_port:-2222}"
    fi
  fi
  valid_port "$SSH_PORT" || die "invalid SSH port: '$SSH_PORT'"

  # Cloudflare token (stored as a compose secret, not in .env.docker)
  CF_TOKEN="${OPT_CF_TOKEN:-}"
  if [[ -z $CF_TOKEN && -s $CF_TOKEN_FILE && $OPT_RECONFIGURE -eq 0 ]]; then
    CF_TOKEN="$(<"$CF_TOKEN_FILE")"
  fi
  if [[ -z $CF_TOKEN ]]; then
    info "Create a Cloudflare API token: dash.cloudflare.com → My Profile →"
    info "API Tokens → Create Token → template \"Edit zone DNS\" → Zone"
    info "Resources: Include / Specific zone / your domain."
    ask_secret CF_TOKEN "Cloudflare API token"
  fi
  CF_TOKEN="$(printf '%s' "$CF_TOKEN" | tr -d '[:space:]')"
  [[ -n $CF_TOKEN ]] || die "a Cloudflare API token is required"

  # Postgres password: keep the existing one (it is baked into the volume).
  local pg_password
  pg_password="$(env_get PUBLICHOST_POSTGRES_PASSWORD)"
  if [[ -z $pg_password ]]; then
    if "${DOCKER[@]}" volume inspect "$(project_name)_pgdata" >/dev/null 2>&1; then
      warn "existing database volume found but no password in .env.docker;"
      warn "assuming the old default 'publichost' — change it later if needed"
      pg_password="publichost"
    else
      pg_password="$(random_secret)"
    fi
  fi

  local ca="$PROD_CA"
  [[ $OPT_STAGING -eq 1 ]] && ca="$STAGING_CA"
  if [[ $OPT_STAGING -eq 0 && $(env_get PUBLICHOST_ACME_CA_SERVER) == "$STAGING_CA" && $reask -eq 0 ]]; then
    ca="$STAGING_CA"
  fi

  env_set PUBLICHOST_BASE_DOMAIN "$DOMAIN"
  env_set PUBLICHOST_SSH_DOMAIN "$DOMAIN"
  env_set PUBLICHOST_EXTERNAL_SSH_PORT "$SSH_PORT"
  env_set PUBLICHOST_ACME_EMAIL "$EMAIL"
  env_set PUBLICHOST_ACME_CA_SERVER "$ca"
  env_set PUBLICHOST_POSTGRES_PASSWORD "$pg_password"
  [[ -n $(env_get PUBLICHOST_POSTGRES_USER) ]] || env_set PUBLICHOST_POSTGRES_USER publichost
  [[ -n $(env_get PUBLICHOST_POSTGRES_DB) ]] || env_set PUBLICHOST_POSTGRES_DB publichost

  (umask 077 && mkdir -p "$SECRET_DIR" && printf '%s' "$CF_TOKEN" >"$CF_TOKEN_FILE")
  chmod 600 "$CF_TOKEN_FILE"

  ok "domain        $DOMAIN"
  ok "ssh port      $SSH_PORT"
  ok "acme email    $EMAIL$([[ $ca == "$STAGING_CA" ]] && echo " (STAGING CA)")"
  ok "saved to      .env.docker, secrets/cloudflare_dns_api_token"
}

load_config() {
  [[ -f $ENV_FILE ]] || die ".env.docker not found — run ./deploy.sh first"
  DOMAIN="$(env_get PUBLICHOST_BASE_DOMAIN)"
  SSH_PORT="$(env_get PUBLICHOST_EXTERNAL_SSH_PORT)"
  SSH_PORT="${SSH_PORT:-2222}"
}

render_traefik() {
  local domain_re="${DOMAIN//./\\.}"
  D="$DOMAIN" RE="$domain_re" awk '
    function repl(s, from, to,    out, i) {
      out = ""
      while ((i = index(s, from)) > 0) {
        out = out substr(s, 1, i - 1) to
        s = substr(s, i + length(from))
      }
      return out s
    }
    { line = repl($0, "__DOMAIN_RE__", ENVIRON["RE"]); print repl(line, "__DOMAIN__", ENVIRON["D"]) }
  ' "$TRAEFIK_TEMPLATE" >"$TRAEFIK_CONFIG"
}

# ------------------------------------------------------------------ DNS ----
run_cf_helper() {
  if command -v python3 >/dev/null 2>&1; then
    CF_API_TOKEN="$CF_TOKEN" python3 -I "$CF_HELPER" "$@"
  else
    "${DOCKER[@]}" image inspect "$BACKEND_IMAGE" >/dev/null 2>&1 || compose build publichost
    CF_API_TOKEN="$CF_TOKEN" "${DOCKER[@]}" run --rm -e CF_API_TOKEN \
      -v "$CF_HELPER:/opt/cloudflare_dns.py:ro" --entrypoint python \
      "$BACKEND_IMAGE" -I /opt/cloudflare_dns.py "$@"
  fi
}

configure_dns() {
  step "DNS records (Cloudflare)"
  PUBLIC_IP="${OPT_IP:-}"
  if [[ -z $PUBLIC_IP ]]; then
    PUBLIC_IP="$(detect_ip)"
    [[ -n $PUBLIC_IP ]] || die "could not detect the public IPv4 address; pass --ip"
  fi
  valid_ipv4 "$PUBLIC_IP" || die "invalid IPv4 address: '$PUBLIC_IP'"
  info "server public IP: $PUBLIC_IP"

  if [[ $OPT_SKIP_DNS -eq 1 ]]; then
    warn "--skip-dns: create these DNS-only (grey cloud) records yourself:"
    info "  A  $DOMAIN    -> $PUBLIC_IP"
    info "  A  *.$DOMAIN  -> $PUBLIC_IP"
    return
  fi
  run_cf_helper --domain "$DOMAIN" --ip "$PUBLIC_IP" \
    || die "Cloudflare DNS update failed (check the token permissions: Zone → DNS → Edit)"
  ok "DNS records point at $PUBLIC_IP"
}

check_dns_resolution() {
  [[ -n $PUBLIC_IP ]] || return 0
  command -v getent >/dev/null 2>&1 || return 0
  local name resolved probe="dns-check-$RANDOM.$DOMAIN"
  for name in "$DOMAIN" "$probe"; do
    resolved="$(getent ahostsv4 "$name" 2>/dev/null | awk 'NR == 1 { print $1 }' || true)"
    if [[ $resolved == "$PUBLIC_IP" ]]; then
      ok "$name resolves to $PUBLIC_IP"
    else
      warn "$name resolves to '${resolved:-nothing}', expected $PUBLIC_IP (DNS may still be propagating)"
    fi
  done
}

# ---------------------------------------------------------------- start ----
start_stack() {
  step "Building and starting containers (first build takes a few minutes)"
  compose build --pull
  compose up -d --remove-orphans --wait --wait-timeout 300 \
    || die "some containers did not become healthy; see: ./deploy.sh logs"
  ok "all containers are up"
}

wait_for_https() {
  step "Waiting for the TLS certificate (DNS-01 challenge, usually 1-3 minutes)"
  local insecure=()
  [[ $(env_get PUBLICHOST_ACME_CA_SERVER) == "$STAGING_CA" ]] && insecure=(--insecure)
  local deadline=$((SECONDS + 420)) code
  until curl -fsS "${insecure[@]}" --max-time 10 -o /dev/null \
    --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/" 2>/dev/null; do
    if ((SECONDS > deadline)); then
      warn "no valid certificate yet. Check: ./deploy.sh logs traefik"
      return 1
    fi
    sleep 5
  done
  ok "https://$DOMAIN serves a valid certificate"
  code="$(curl -sS "${insecure[@]}" --max-time 10 -o /dev/null -w '%{http_code}' \
    --resolve "deploy-check.$DOMAIN:443:127.0.0.1" "https://deploy-check.$DOMAIN/" || true)"
  if [[ $code == 502 ]]; then
    ok "tunnel subdomains are routed to the tunnel proxy"
  else
    warn "unexpected response for https://deploy-check.$DOMAIN/: HTTP $code"
  fi
}

host_key_fingerprint() {
  compose exec -T publichost python -c \
    "import asyncssh; print(asyncssh.read_private_key('/data/host_key').get_fingerprint())" \
    2>/dev/null || true
}

summary() {
  local port_flag="" fingerprint
  [[ $SSH_PORT == 22 ]] || port_flag="-p $SSH_PORT "
  fingerprint="$(host_key_fingerprint)"
  cat <<EOF

${C_GREEN}${C_BOLD}PublicHost is running.${C_RESET}

  Open a tunnel to your local port 3000:
    ${C_BOLD}ssh ${port_flag}-R 0:localhost:3000 $DOMAIN${C_RESET}

  Landing page:      https://$DOMAIN
  SSH host key:      ${fingerprint:-unknown}

  Manage:            ./deploy.sh status | logs | update | restart | down
EOF
}

# ------------------------------------------------------------- commands ----
cmd_install() {
  preflight
  ensure_docker
  configure
  check_ports "$SSH_PORT"
  open_firewall "$SSH_PORT"
  render_traefik
  configure_dns
  start_stack
  check_dns_resolution
  wait_for_https || true
  summary
}

cmd_update() {
  preflight
  ensure_docker
  load_config
  if [[ -d $ROOT/.git ]] && command -v git >/dev/null 2>&1; then
    step "Pulling the latest code"
    git -C "$ROOT" pull --ff-only || die "git pull failed; resolve it manually, then re-run"
  fi
  ASSUME_YES=1
  configure
  render_traefik
  start_stack
  wait_for_https || true
  summary
}

cmd_uninstall() {
  ensure_docker
  [[ -f $ENV_FILE ]] || die ".env.docker not found — nothing to uninstall"
  warn "This stops PublicHost and DELETES its data: database, WebLogs, the SSH host"
  warn "key and TLS certificates. .env.docker and secrets/ are kept."
  if interactive; then
    local answer
    read -rp "    Type 'delete' to continue: " answer
    [[ $answer == delete ]] || die "aborted"
  elif [[ $ASSUME_YES -ne 1 ]]; then
    die "refusing to delete data non-interactively without --yes"
  fi
  compose down -v --remove-orphans
  rm -f "$TRAEFIK_CONFIG"
  ok "PublicHost removed"
}

if [[ ${#EXTRA_ARGS[@]} -gt 0 && $COMMAND != logs ]]; then
  usage
  die "unknown option: ${EXTRA_ARGS[0]}"
fi

case "$COMMAND" in
  install) cmd_install ;;
  update) cmd_update ;;
  status) ensure_docker >/dev/null; load_config; compose ps ;;
  logs) ensure_docker >/dev/null; load_config; compose logs -f --tail=200 "${EXTRA_ARGS[@]}" || true ;;
  restart) ensure_docker >/dev/null; load_config; compose restart ;;
  down) ensure_docker >/dev/null; load_config; compose down ;;
  uninstall) cmd_uninstall ;;
  help) usage ;;
  *) usage; die "unknown command: $COMMAND" ;;
esac
