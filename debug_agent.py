#!/usr/bin/env python3
"""
debug_agent.py — Pipeline de debug do agente Jabuti/BV

Fluxo: BVID -> customer_id -> conversation_id -> logs Loki -> análise Claude

MUDANÇAS NESTA VERSÃO:
  - APIs reais do BV preenchidas (host, endpoints, auth via X-Api-Key)
  - Suporte a UAT e PROD como ambientes separados
  - Autenticação dupla no Grafana: GRAFANA_TOKEN (permanente) ou GRAFANA_SESSION (cookie)
  - Retry automático para VPN instável (3 tentativas, 5s de espera)
  - Distinção de erros HTTP: 401 auth, 403 permissão, ConnectionError VPN

REQUISITOS:
    pip install requests anthropic

VARIÁVEIS DE AMBIENTE:
    ANTHROPIC_API_KEY       Chave da API do Claude
    GRAFANA_TOKEN           Service account token (permanente)  — OU —
    GRAFANA_SESSION         Cookie de sessão do Grafana (temporário)
    BV_API_KEY              API Key das APIs Jabuti/BV (opcional, sobrescreve default UAT)
    BV_WORKER_ID            Worker ID (opcional, sobrescreve default UAT)

USO:
    python debug_agent.py <BVID>
    python debug_agent.py <BVID> --env prod
    python debug_agent.py <BVID> --hours 48
    python debug_agent.py <BVID> --save
    python debug_agent.py <BVID> --no-vpn-check
"""

import os, sys, json, time, argparse, re
from datetime import datetime, timedelta, timezone
import requests
from anthropic import Anthropic

ENVS = {
    "uat": {
        "host":      "api.bancobv.jabuti.ai",
        "api_key":   os.environ.get("BV_API_KEY",   "DEFINA_VIA_ENV_BV_API_KEY"),
        "worker_id": os.environ.get("BV_WORKER_ID", "ca3afe48-39ec-4fb2-a365-29859aeab69f"),
    },
    "prod": {
        "host":      "PREENCHER",
        "api_key":   os.environ.get("BV_API_KEY_PROD",   "PREENCHER"),
        "worker_id": os.environ.get("BV_WORKER_ID_PROD", "PREENCHER"),
    },
}

GRAFANA_BASE        = "https://jabutiagi.grafana.net"
LOKI_DATASOURCE_UID = "grafanacloud-logs"
LOKI_APP_FILTER     = 'app_kubernetes_io_name="worker-chart"'
GRAFANA_TOKEN       = os.environ.get("GRAFANA_TOKEN")
GRAFANA_SESSION     = os.environ.get("GRAFANA_SESSION")
ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY")

DEFAULT_HOURS  = 24
DEFAULT_MODEL  = "claude-sonnet-4-6"
LOG_LIMIT      = 2000
VPN_TIMEOUT    = 8
API_RETRIES    = 3
API_RETRY_WAIT = 5

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)

SYSTEM_PROMPT = """Você é um especialista em debug de agentes de IA conversacionais.
Estruture sua resposta:
1. RESUMO | 2. FLUXO | 3. PONTO DE FALHA | 4. CAUSA PROVÁVEL | 5. EVIDÊNCIAS | 6. PRÓXIMOS PASSOS
Seja técnico e direto. Não invente nada fora dos logs."""

def _step(msg): print(f"  {msg}...".ljust(55), end="", flush=True)
def _ok(d=""):  print(f"  ✓  {d}" if d else "  ✓")
def _warn(msg): print(f"\n  ⚠  {msg}")
def _fail(msg):
    print("  ✗")
    print(f"\nERRO: {msg}", file=sys.stderr)
    sys.exit(1)

def grafana_headers():
    if GRAFANA_TOKEN:   return {"Authorization": f"Bearer {GRAFANA_TOKEN}", "x-grafana-org-id": "1"}
    if GRAFANA_SESSION: return {"Cookie": f"grafana_session={GRAFANA_SESSION}", "x-grafana-org-id": "1"}
    return {}

def auth_label():
    return "token permanente" if GRAFANA_TOKEN else "cookie de sessão (temporário)" if GRAFANA_SESSION else "nenhuma"

def _extract_uuid(text, data):
    if isinstance(data, str) and UUID_RE.fullmatch(data.strip()): return data.strip()
    if isinstance(data, dict):
        for f in ("id","customer_id","conversation_id","data","result"):
            v = data.get(f)
            if isinstance(v, str) and UUID_RE.fullmatch(v.strip()): return v.strip()
    m = UUID_RE.search(text)
    return m.group(0) if m else None

def _get(url, headers, params, label):
    for attempt in range(1, API_RETRIES + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
            if r.status_code == 401:
                _fail("[Grafana] Cookie expirado (401).\n  Renove: export GRAFANA_SESSION='novo_valor'" if "grafana" in url else f"[{label}] Auth recusada (401).")
            if r.status_code == 403:
                _fail(f"[{label}] Sem permissão (403).")
            r.raise_for_status()
            return r
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < API_RETRIES:
                print(f"\n  ↻  [{label}] tentativa {attempt}/{API_RETRIES}, aguardando {API_RETRY_WAIT}s...", end="", flush=True)
                time.sleep(API_RETRY_WAIT)
            else:
                _fail(f"[{label}] {API_RETRIES} tentativas falharam: {e}\n  Verifique a VPN.")

def resolve_customer_id(bvid, env):
    _step("Buscando customer_id (API 1)")
    r = _get(f"https://{env['host']}/b2bcustomers/phone_number/{bvid}", {"X-Api-Key": env["api_key"]}, {}, "API customer")
    try: data = r.json()
    except: data = None
    cid = _extract_uuid(r.text, data) or _fail(f"UUID não encontrado: {r.text[:300]}")
    _ok(cid); return cid

def resolve_conversation_id(customer_id, env):
    _step("Buscando conversation_id (API 2)")
    r = _get(f"https://{env['host']}/conversations/customer/{customer_id}", {"X-Api-Key": env["api_key"]}, {"worker_id": env["worker_id"]}, "API conversation")
    try: data = r.json()
    except: data = None
    cid = _extract_uuid(r.text, data) or _fail(f"UUID não encontrado: {r.text[:300]}")
    _ok(cid); return cid

def fetch_logs(conversation_id, hours):
    end, start = datetime.now(timezone.utc), datetime.now(timezone.utc) - timedelta(hours=hours)
    _step(f"Buscando logs no Grafana (últimas {hours}h)")
    r = _get(f"{GRAFANA_BASE}/api/datasources/proxy/uid/{LOKI_DATASOURCE_UID}/loki/api/v1/query_range",
             grafana_headers(),
             {"query": f'{{{LOKI_APP_FILTER}}} |= `{conversation_id}`',
              "start": str(int(start.timestamp()*1e9)), "end": str(int(end.timestamp()*1e9)),
              "limit": LOG_LIMIT, "direction": "backward"}, "Loki")
    lines = sorted([(int(ts), ln) for s in r.json().get("data",{}).get("result",[]) for ts,ln in s.get("values",[])], key=lambda x: x[0])
    fmt   = [f"[{datetime.fromtimestamp(ts/1e9,tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] {ln}" for ts,ln in lines]
    _ok(f"{len(lines)} linhas")
    return "\n".join(fmt), len(lines)

def analyze_logs(logs, bvid, conv_id, model):
    _step(f"Analisando com Claude ({model})")
    resp = Anthropic().messages.create(model=model, max_tokens=2000, system=SYSTEM_PROMPT,
        messages=[{"role":"user","content":f"BVID: {bvid}\nconv_id: {conv_id}\n\nLOGS:\n{logs}"}])
    _ok(); return resp.content[0].text

def main():
    p = argparse.ArgumentParser()
    p.add_argument("bvid")
    p.add_argument("--env",   choices=["uat","prod"], default="uat")
    p.add_argument("--hours", type=int, default=DEFAULT_HOURS)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--save",  action="store_true")
    p.add_argument("--no-vpn-check", action="store_true")
    args = p.parse_args()
    env  = ENVS[args.env]

    print(f"\n{\'═\'*60}\n  Debug Jabuti/BV — {args.env.upper()}  |  {args.bvid}\n  Auth: {auth_label()}\n{\'═\'*60}\n")

    if not GRAFANA_TOKEN and not GRAFANA_SESSION: _fail("Defina GRAFANA_TOKEN ou GRAFANA_SESSION.")
    if not ANTHROPIC_API_KEY: _fail("Defina ANTHROPIC_API_KEY.")
    if GRAFANA_SESSION and not GRAFANA_TOKEN: _warn("Cookie temporário — renove GRAFANA_SESSION quando expirar.")
    if not args.no_vpn_check:
        _step(f"Testando VPN ({env['host']})")
        try: requests.head(f"https://{env['host']}", timeout=VPN_TIMEOUT); _ok()
        except: _fail("Host inacessível — verifique a VPN.")

    cid  = resolve_customer_id(args.bvid, env)
    conv = resolve_conversation_id(cid, env)
    logs, n = fetch_logs(conv, args.hours)
    if n == 0: _fail(f"Sem logs para {conv}. Tente --hours {args.hours*3}.")

    diag = analyze_logs(logs, args.bvid, conv, args.model)
    print(f"\n{\'═\'*60}\n  DIAGNÓSTICO\n{\'═\'*60}\n{diag}")

    if args.save:
        with open(f"debug_{conv}.log","w") as f: f.write(logs)
        with open(f"debug_{conv}.md","w")  as f: f.write(f"# Debug {args.bvid}\n\n{diag}\n")

if __name__ == "__main__":
    main()
