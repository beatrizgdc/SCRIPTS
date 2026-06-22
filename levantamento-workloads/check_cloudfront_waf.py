#!/usr/bin/env python3
"""
check_cloudfront_waf.py — Verifica WAF associado em todas as distribuições CloudFront
Roda na payer e assume role nas contas membro.

Uso:
    python3 check_cloudfront_waf.py 2>&1 | tee cf_waf_$(date +%Y%m%d_%H%M%S).log

Saída: output/cloudfront_waf_report.txt + resumo no terminal
"""

import boto3
import os
from datetime import datetime, timezone

# ─── CONFIGURAÇÃO — PREENCHA ANTES DE RODAR ───────────────────

# Modo de execução:
#   False → multi-conta via payer com assume role (preencha ACCOUNTS abaixo)
#   True  → roda direto na conta atual, sem assume role
#            (use no CloudShell da própria conta quando não há acesso via payer)
SINGLE_ACCOUNT_MODE = False

# Nome amigável usado no relatório quando SINGLE_ACCOUNT_MODE = True.
# Ignorado no modo multi-conta.
SINGLE_ACCOUNT_NAME = "NOME_DA_CONTA"

# Lista de contas membro a verificar (usado apenas quando SINGLE_ACCOUNT_MODE = False).
ACCOUNTS = [
    {"id": "111111111111", "name": "NOME_CONTA_1"},
    {"id": "222222222222", "name": "NOME_CONTA_2"},
    # {"id": "333333333333", "name": "NOME_CONTA_3"},
]

# Roles que o script vai tentar assumir, em ordem de preferência.
# Ignorado quando SINGLE_ACCOUNT_MODE = True.
ROLE_CANDIDATES = [
    "AWSControlTowerExecution",
    "aws-controltower-AdministratorExecutionRole",
    "OrganizationAccountAccessRole",
    # "nome-da-role-customizada",
]
# ──────────────────────────────────────────────────────────────

OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"[{ts()}] {msg}")


def assume_role(account_id):
    sts = boto3.client("sts")
    for role_name in ROLE_CANDIDATES:
        try:
            resp = sts.assume_role(
                RoleArn=f"arn:aws:iam::{account_id}:role/{role_name}",
                RoleSessionName="cf-waf-check"
            )
            creds = resp["Credentials"]
            return boto3.Session(
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"]
            ), role_name
        except Exception:
            continue
    return None, None


def check_cloudfront_waf(session, account_id, account_name):
    results = []
    try:
        cf = session.client("cloudfront", region_name="us-east-1")
        dists = []
        for page in cf.get_paginator("list_distributions").paginate():
            items = page.get("DistributionList", {}).get("Items", [])
            dists.extend(items)

        if not dists:
            return results

        for d in dists:
            aliases = d.get("Aliases", {}).get("Items", [])
            domain = ", ".join(aliases) if aliases else d["DomainName"]
            waf_id = d.get("WebACLId", "")  # campo correto: WebACLId
            waf_name = waf_id.split("/")[-1] if waf_id else ""
            enabled = d.get("Enabled", False)

            results.append({
                "account_id": account_id,
                "account_name": account_name,
                "dist_id": d["Id"],
                "domain": domain,
                "enabled": enabled,
                "has_waf": bool(waf_id),
                "waf_name": waf_name,
            })

    except Exception as e:
        log(f"  ⚠️  Erro ao checar CloudFront em {account_name}: {e}")

    return results


def main():
    sts_local = boto3.client("sts")
    current_account = sts_local.get_caller_identity()["Account"]

    all_results = []
    failed = []

    if SINGLE_ACCOUNT_MODE:
        # ── Modo conta única (sem assume role) ─────────────────────────────
        if SINGLE_ACCOUNT_NAME == "NOME_DA_CONTA":
            print("ERRO: preencha SINGLE_ACCOUNT_NAME no início do script antes de rodar.")
            return

        log(f"Modo: SINGLE ACCOUNT — {SINGLE_ACCOUNT_NAME} ({current_account})")
        session = boto3.Session()
        results = check_cloudfront_waf(session, current_account, SINGLE_ACCOUNT_NAME)
        all_results.extend(results)
        log(f"  → {len(results)} distribuições encontradas")

    else:
        # ── Modo multi-conta via payer (assume role) ────────────────────────
        if not ACCOUNTS or ACCOUNTS[0]["id"] in ("111111111111", "222222222222"):
            print("ERRO: preencha a lista ACCOUNTS no início do script antes de rodar.")
            print("      Ou defina SINGLE_ACCOUNT_MODE = True para rodar na conta atual.")
            return

        log(f"Modo: MULTI-CONTA — payer: {current_account}")
        log(f"Verificando CloudFront WAF em {len(ACCOUNTS)} contas...\n")

        for acc in ACCOUNTS:
            account_id = acc["id"]
            account_name = acc["name"]
            log(f"Processando: {account_name} ({account_id})")

            if account_id == current_account:
                session = boto3.Session()
                log(f"  → Conta atual — usando sessão local")
            else:
                session, role = assume_role(account_id)
                if not session:
                    log(f"  ⚠️  Sem role acessível — pulando")
                    failed.append(f"{account_name} ({account_id})")
                    continue
                log(f"  → Role: {role}")

            results = check_cloudfront_waf(session, account_id, account_name)
            all_results.extend(results)
            log(f"  → {len(results)} distribuições encontradas")

    # ── Relatório ──────────────────────────────────────────────
    lines = []
    lines.append("=" * 70)
    lines.append(f"CLOUDFRONT WAF REPORT — Gerado em {ts()} UTC")
    lines.append("=" * 70)

    sem_waf = [r for r in all_results if not r["has_waf"]]
    com_waf = [r for r in all_results if r["has_waf"]]

    lines.append(f"\nTotal de distribuições: {len(all_results)}")
    lines.append(f"  ✅ Com WAF:  {len(com_waf)}")
    lines.append(f"  ⚠️  Sem WAF: {len(sem_waf)}")

    # Por conta
    by_account = {}
    for r in all_results:
        key = f"{r['account_name']} ({r['account_id']})"
        by_account.setdefault(key, []).append(r)

    for account_label, dists in sorted(by_account.items()):
        lines.append(f"\n{'─' * 70}")
        lines.append(f"  {account_label}")
        lines.append(f"{'─' * 70}")
        for d in dists:
            enabled_str = "Enabled" if d["enabled"] else "Disabled"
            if d["has_waf"]:
                lines.append(f"  ✅  [{enabled_str}] {d['domain']}")
                lines.append(f"       ID: {d['dist_id']}")
                lines.append(f"       WAF: {d['waf_name']}")
            else:
                lines.append(f"  ⚠️  [{enabled_str}] {d['domain']}")
                lines.append(f"       ID: {d['dist_id']}")
                lines.append(f"       WAF: SEM WAF")

    if sem_waf:
        lines.append(f"\n{'=' * 70}")
        lines.append("RESUMO — Distribuições SEM WAF:")
        lines.append(f"{'=' * 70}")
        for r in sem_waf:
            enabled_str = "Enabled" if r["enabled"] else "Disabled"
            lines.append(f"  ⚠️  {r['account_name']} | [{enabled_str}] {r['domain']} | {r['dist_id']}")

    if failed:
        lines.append(f"\n⚠️  Contas sem acesso: {', '.join(failed)}")

    report = "\n".join(lines)
    print("\n" + report)

    out_file = f"{OUTPUT_DIR}/cloudfront_waf_report.txt"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(report)

    log(f"\n✅ Relatório salvo em: {out_file}")


if __name__ == "__main__":
    main()
