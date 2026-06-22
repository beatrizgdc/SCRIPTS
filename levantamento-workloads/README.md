# Levantamento de Workloads — Scripts de Discovery AWS

Scripts Python para levantamento técnico de infraestrutura AWS, usados na documentação de Workloads no Confluence (MSP Darede).

Todos os scripts rodam no **AWS CloudShell** e geram saída em `./output/`.

---

## Fluxo de uso

```
1. workload_discovery.py      → levantamento geral via payer (multi-conta)
2. levantamento_single_account.py  → fallback para contas sem trust policy na payer
3. check_cloudfront_waf.py    → inventário de WAF nas distribuições CloudFront
4. levantamento_wordpress.py  → levantamento focado em stacks WordPress/sites
```

Com os outputs em mãos, preenche os diagramas `.drawio` e a página de Workloads no Confluence.

---

## Scripts

### `workload_discovery.py` — Principal, multi-conta via payer

Roda na **payer** e faz assume role em cada conta membro. Para cada conta, gera um arquivo `output/levantamento_{account_id}_{account_name}.txt` com o inventário completo de recursos.

**Serviços coletados:** VPC/subnets/security groups, EC2, Auto Scaling Groups, ECS (clusters/serviços/tasks), EKS, RDS/Aurora, Lambda, ALB/NLB (listeners e target groups), WAF (regional e CloudFront), EFS, AWS Backup, CloudWatch Alarms, CloudFront, S3, Route53.

**O que editar antes de rodar:**

```python
ACCOUNTS = [
    {"id": "111122223333", "name": "NomeAmigavel"},
    ...
]

TARGET_REGIONS = ["us-east-1", "sa-east-1"]  # regiões alvo

ROLE_CANDIDATES = [
    "AWSControlTowerExecution",
    "OrganizationAccountAccessRole",
    ...
]
```

**Permissões necessárias nas contas membro:** `ReadOnlyAccess` ou `ViewOnlyAccess` + `SecurityAudit`

**Uso:**
```bash
python3 workload_discovery.py 2>&1 | tee discovery_$(date +%Y%m%d_%H%M%S).log
```

---

### `levantamento_single_account.py` — Sem assume role, roda na própria conta

Mesma cobertura do `workload_discovery.py`, mas sem assume role. Usar quando a conta não tem trust policy configurada para a payer (ou quando o acesso via payer não está disponível). Roda direto no CloudShell da conta-alvo.

**O que editar antes de rodar:**

```python
ACCOUNT_NAME = "nome-da-conta"  # usado no nome do arquivo de saída

TARGET_REGIONS = ["us-east-1", "sa-east-1"]
```

**Uso (no CloudShell DA própria conta):**
```bash
python3 levantamento_single_account.py 2>&1 | tee levantamento_$(date +%Y%m%d_%H%M%S).log
```

---

### `check_cloudfront_waf.py` — Inventário CloudFront × WAF

Utilitário focado: verifica quais distribuições CloudFront têm WAF associado e gera `output/cloudfront_waf_report.txt` com o status de cada distribuição (com/sem WAF, ID da WebACL se houver). Útil para identificar distribuições sem proteção WAF antes de preencher os diagramas.

Suporta dois modos de execução, controlados pela variável `SINGLE_ACCOUNT_MODE` no topo do script:

**Modo multi-conta via payer** (`SINGLE_ACCOUNT_MODE = False` — padrão)

Roda no CloudShell da payer e faz assume role nas contas membro. Preencha `ACCOUNTS` e `ROLE_CANDIDATES`:

```python
SINGLE_ACCOUNT_MODE = False

ACCOUNTS = [
    {"id": "111122223333", "name": "NOME_CONTA_1"},
    ...
]

ROLE_CANDIDATES = [
    "AWSControlTowerExecution",
    "OrganizationAccountAccessRole",
    ...
]
```

**Modo conta única** (`SINGLE_ACCOUNT_MODE = True`)

Roda direto na conta, sem assume role. Use no CloudShell da própria conta quando não há acesso via payer. Preencha apenas `SINGLE_ACCOUNT_NAME`:

```python
SINGLE_ACCOUNT_MODE = True
SINGLE_ACCOUNT_NAME = "nome-da-conta"
```

O relatório de saída é idêntico nos dois modos.

**Uso:**
```bash
python3 check_cloudfront_waf.py 2>&1 | tee cf_waf_$(date +%Y%m%d_%H%M%S).log
```

---

### `levantamento_wordpress.py` — Foco em stacks WordPress/sites

Script mais específico, voltado para workloads de sites com padrão **CloudFront → ALB → EC2 → RDS**. Coleta EC2, ASG, ECS, RDS, ALB, CloudFront, WAF, EFS e gera um resumo de findings de segurança ao final.

Roda direto na conta (sem assume role). Ideal para workloads de sites/WordPress onde o `workload_discovery.py` traz mais informação do que o necessário.

**Uso (no CloudShell da conta):**
```bash
python3 levantamento_wordpress.py 2>&1 | tee wordpress_$(date +%Y%m%d_%H%M%S).log
```

---

## Permissões necessárias (mínimo)

| Cenário | Política |
|---|---|
| Payer assume role nas membros | `ReadOnlyAccess` + `SecurityAudit` nas contas membro |
| Roda direto na conta | `ReadOnlyAccess` ou `ViewOnlyAccess` + `SecurityAudit` |
| CloudFront WAF check | Mesma da payer + `cloudfront:ListDistributions` nas membros |

---

## Output

Todos os scripts criam a pasta `./output/` automaticamente. Os arquivos `.txt` gerados são a fonte de verdade para preencher:

- Diagramas de arquitetura (`.drawio`)
- Seção de Inventário de Workloads no Confluence
- RTO/RPO e dados de SLA por workload
