#!/usr/bin/env python3
"""
workload_discovery.py — Levantamento técnico multi-conta para documentação de Workloads
Roda na payer (CloudShell) e assume role nas contas membro via lista manual.

Uso:
    python3 workload_discovery.py 2>&1 | tee discovery_$(date +%Y%m%d_%H%M%S).log

Saída: ./output/levantamento_{account_id}_{account_name}.txt por conta

Permissões necessárias nas contas membro:
    ReadOnlyAccess ou ViewOnlyAccess + SecurityAudit
"""

import boto3
import botocore
import os
from datetime import datetime, timezone

# ─── CONFIGURAÇÃO — PREENCHA ANTES DE RODAR ──────────────────────────────────

ACCOUNTS = [
    {"id": "058264241323", "name": "Conta_JBS_Sites"},
    {"id": "490004615756", "name": "JBS_Terminais"},
    {"id": "339712769475", "name": "JBS-Audit"},
    {"id": "339713154499", "name": "jbs-data-collection"},
    {"id": "891377378862", "name": "JBS-LogArchive"},
    {"id": "025066252202", "name": "JBS-Network"},
    {"id": "668119484199", "name": "jbs-prd"},
    {"id": "025066252299", "name": "JBS-Shared"},
    {"id": "714327808469", "name": "novos-negocios"},
    {"id": "740211355999", "name": "pa_jbs_spp"},
]

TARGET_REGIONS = [
    "us-east-1",
    "sa-east-1",
]

ROLE_CANDIDATES = [
    "AWSControlTowerExecution",
    "aws-controltower-AdministratorExecutionRole",
    "OrganizationAccountAccessRole",
    "darede-switch-role",
    "darede-full",
]

# ─────────────────────────────────────────────────────────────────────────────

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
                RoleSessionName="workload-discovery"
            )
            creds = resp["Credentials"]
            session = boto3.Session(
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"]
            )
            return session, role_name
        except Exception:
            continue
    return None, None


def tag_name(tags, default="(sem nome)"):
    if not tags:
        return default
    for t in tags:
        if t.get("Key") == "Name":
            return t.get("Value") or default
    return default


# ─── COLETA POR SERVIÇO ───────────────────────────────────────────────────────

def collect_account_info(session):
    lines = ["", "=" * 65, "ACCOUNT INFO", "=" * 65]
    try:
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        lines.append(f"  Account ID : {identity['Account']}")
        lines.append(f"  ARN        : {identity['Arn']}")
    except Exception as e:
        lines.append(f"  ⚠️  Erro ao obter identity: {e}")
    try:
        iam = session.client("iam")
        aliases = iam.list_account_aliases().get("AccountAliases", [])
        lines.append(f"  Alias      : {aliases[0] if aliases else '(sem alias)'}")
    except Exception:
        lines.append("  Alias      : (sem permissão)")
    return lines


def collect_vpc(session, region):
    lines = [f"\n{'─' * 65}", f"VPC — {region}", "─" * 65]
    try:
        ec2 = session.client("ec2", region_name=region)
        vpcs = ec2.describe_vpcs()["Vpcs"]

        if not vpcs:
            lines.append("  ℹ️  Nenhuma VPC encontrada")
            return lines

        for vpc in vpcs:
            name = tag_name(vpc.get("Tags"))
            default_flag = " (default)" if vpc.get("IsDefault") else ""
            lines.append(f"\n  VPC: {vpc['VpcId']} | {name}{default_flag}")
            lines.append(f"       CIDR: {vpc['CidrBlock']} | State: {vpc['State']}")

            subnets = ec2.describe_subnets(
                Filters=[{"Name": "vpc-id", "Values": [vpc["VpcId"]]}]
            )["Subnets"]
            pub = [s for s in subnets if s.get("MapPublicIpOnLaunch")]
            priv = [s for s in subnets if not s.get("MapPublicIpOnLaunch")]
            lines.append(f"       Subnets: {len(subnets)} total ({len(pub)} públicas, {len(priv)} privadas)")

        # Peering
        peerings = ec2.describe_vpc_peering_connections(
            Filters=[{"Name": "status-code", "Values": ["active"]}]
        )["VpcPeeringConnections"]
        if peerings:
            lines.append(f"\n  VPC Peering ativo ({len(peerings)}):")
            for p in peerings:
                req = p["RequesterVpcInfo"]
                acc = p["AccepterVpcInfo"]
                lines.append(f"    ✅ {p['VpcPeeringConnectionId']} | {req['VpcId']} ({req.get('OwnerId','?')}) ↔ {acc['VpcId']} ({acc.get('OwnerId','?')})")
        else:
            lines.append("\n  ℹ️  Sem VPC Peering ativo")

        # Transit Gateway
        try:
            tgw = ec2.describe_transit_gateway_attachments(
                Filters=[{"Name": "state", "Values": ["available"]}]
            )["TransitGatewayAttachments"]
            if tgw:
                lines.append(f"\n  Transit Gateway ({len(tgw)} attachments):")
                for t in tgw:
                    lines.append(f"    ✅ {t['TransitGatewayId']} | {t['ResourceType']} | {t['ResourceId']}")
            else:
                lines.append("  ℹ️  Sem Transit Gateway attachment")
        except Exception:
            pass

        # VPN
        vpn = ec2.describe_vpn_connections(
            Filters=[{"Name": "state", "Values": ["available"]}]
        )["VpnConnections"]
        if vpn:
            lines.append(f"\n  VPN Connections ({len(vpn)}):")
            for v in vpn:
                name = tag_name(v.get("Tags"))
                lines.append(f"    ✅ {v['VpnConnectionId']} | {name} | {v.get('Type','?')} | CGW: {v.get('CustomerGatewayId','?')}")
        else:
            lines.append("  ℹ️  Sem VPN Connection")

    except Exception as e:
        lines.append(f"  ⚠️  Erro: {e}")
    return lines


def collect_ec2(session, region):
    lines = [f"\n{'─' * 65}", f"EC2 — {region}", "─" * 65]
    try:
        ec2 = session.client("ec2", region_name=region)
        paginator = ec2.get_paginator("describe_instances")
        instances = []
        for page in paginator.paginate(
            Filters=[{"Name": "instance-state-name", "Values": ["running", "stopped", "stopping"]}]
        ):
            for r in page["Reservations"]:
                instances.extend(r["Instances"])

        if not instances:
            lines.append("  ℹ️  Nenhuma instância EC2 encontrada")
            return lines

        by_state = {}
        for i in instances:
            st = i["State"]["Name"]
            by_state[st] = by_state.get(st, 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in by_state.items())
        lines.append(f"  Total: {len(instances)} instâncias — {summary}")

        for i in sorted(instances, key=lambda x: tag_name(x.get("Tags", []))):
            name = tag_name(i.get("Tags", []))
            state = i["State"]["Name"]
            icon = "✅" if state == "running" else "⚠️"
            az = i.get("Placement", {}).get("AvailabilityZone", "?")
            private_ip = i.get("PrivateIpAddress", "—")
            public_ip = i.get("PublicIpAddress", "—")
            pub_flag = "  ⚠️ IP PÚBLICO EXPOSTO" if public_ip != "—" else ""
            platform = i.get("Platform", "linux")
            vols = len(i.get("BlockDeviceMappings", []))
            lines.append(f"\n  {icon} {name}")
            lines.append(f"     ID: {i['InstanceId']} | Tipo: {i['InstanceType']} | Estado: {state} | SO: {platform}")
            lines.append(f"     AZ: {az} | IP Privado: {private_ip} | IP Público: {public_ip}{pub_flag}")
            lines.append(f"     Volumes EBS: {vols}")

    except Exception as e:
        lines.append(f"  ⚠️  Erro: {e}")
    return lines


def collect_asg(session, region):
    lines = [f"\n{'─' * 65}", f"AUTO SCALING GROUPS — {region}", "─" * 65]
    try:
        asg_client = session.client("autoscaling", region_name=region)
        paginator = asg_client.get_paginator("describe_auto_scaling_groups")
        groups = []
        for page in paginator.paginate():
            groups.extend(page["AutoScalingGroups"])

        if not groups:
            lines.append("  ℹ️  Nenhum ASG encontrado")
            return lines

        lines.append(f"  Total: {len(groups)} Auto Scaling Groups")
        for g in groups:
            lines.append(f"\n  ✅ {g['AutoScalingGroupName']}")
            lines.append(f"     Min/Desired/Max: {g['MinSize']}/{g['DesiredCapacity']}/{g['MaxSize']}")
            lines.append(f"     Instâncias: {len(g['Instances'])} | AZs: {', '.join(g['AvailabilityZones'])}")
            lt = g.get("LaunchTemplate", {})
            lc = g.get("LaunchConfigurationName", "")
            if lt:
                lines.append(f"     Launch Template: {lt.get('LaunchTemplateName', '?')} v{lt.get('Version', '?')}")
            elif lc:
                lines.append(f"     Launch Config (legado): {lc}")

    except Exception as e:
        lines.append(f"  ⚠️  Erro: {e}")
    return lines


def collect_ecs(session, region):
    lines = [f"\n{'─' * 65}", f"ECS — {region}", "─" * 65]
    try:
        ecs = session.client("ecs", region_name=region)
        appas = session.client("application-autoscaling", region_name=region)

        cluster_arns = []
        for page in ecs.get_paginator("list_clusters").paginate():
            cluster_arns.extend(page["clusterArns"])

        if not cluster_arns:
            lines.append("  ℹ️  Nenhum cluster ECS encontrado")
            return lines

        clusters = ecs.describe_clusters(
            clusters=cluster_arns, include=["STATISTICS"]
        )["clusters"]
        lines.append(f"  Total: {len(clusters)} clusters")

        for c in clusters:
            lines.append(f"\n  ✅ Cluster: {c['clusterName']} | Status: {c['status']}")
            lines.append(f"     Running tasks: {c.get('runningTasksCount', 0)} | "
                         f"Pending: {c.get('pendingTasksCount', 0)} | "
                         f"Serviços: {c.get('activeServicesCount', 0)}")

            svc_arns = []
            for page in ecs.get_paginator("list_services").paginate(cluster=c["clusterArn"]):
                svc_arns.extend(page["serviceArns"])

            for i in range(0, len(svc_arns), 10):
                batch = svc_arns[i:i + 10]
                svcs = ecs.describe_services(cluster=c["clusterArn"], services=batch)["services"]
                for s in svcs:
                    cap = s.get("capacityProviderStrategy", [{}])
                    launch = s.get("launchType") or (cap[0].get("capacityProvider", "?") if cap else "?")
                    icon = "✅" if s["runningCount"] >= s["desiredCount"] > 0 else "⚠️"
                    lines.append(f"\n     {icon} Serviço: {s['serviceName']}")
                    lines.append(f"        Launch: {launch} | Desired/Running/Pending: "
                                 f"{s['desiredCount']}/{s['runningCount']}/{s['pendingCount']}")
                    lines.append(f"        Task Def: {s['taskDefinition'].split('/')[-1]}")

                    try:
                        targets = appas.describe_scalable_targets(
                            ServiceNamespace="ecs",
                            ResourceIds=[f"service/{c['clusterName']}/{s['serviceName']}"]
                        )["ScalableTargets"]
                        if targets:
                            t = targets[0]
                            lines.append(f"        Auto Scaling: ✅ Min {t['MinCapacity']} / Max {t['MaxCapacity']}")
                        else:
                            lines.append(f"        Auto Scaling: ⚠️ não configurado")
                    except Exception:
                        pass

    except Exception as e:
        lines.append(f"  ⚠️  Erro: {e}")
    return lines


def collect_eks(session, region):
    lines = [f"\n{'─' * 65}", f"EKS — {region}", "─" * 65]
    try:
        eks = session.client("eks", region_name=region)
        cluster_names = eks.list_clusters().get("clusters", [])

        if not cluster_names:
            lines.append("  ℹ️  Nenhum cluster EKS encontrado")
            return lines

        lines.append(f"  Total: {len(cluster_names)} clusters")
        for name in cluster_names:
            c = eks.describe_cluster(name=name)["cluster"]
            lines.append(f"\n  ✅ Cluster: {c['name']}")
            lines.append(f"     Versão K8s: {c['version']} | Status: {c['status']}")
            lines.append(f"     Endpoint: {c.get('endpoint', '?')}")
            lines.append(f"     Endpoint público: {'⚠️ SIM' if c.get('resourcesVpcConfig', {}).get('endpointPublicAccess') else '✅ não'}")
            lines.append(f"     Role ARN: {c.get('roleArn', '?')}")

            ngs = eks.list_nodegroups(clusterName=name).get("nodegroups", [])
            if ngs:
                lines.append(f"     Node Groups ({len(ngs)}):")
                for ng_name in ngs:
                    ng = eks.describe_nodegroup(clusterName=name, nodegroupName=ng_name)["nodegroup"]
                    sc = ng.get("scalingConfig", {})
                    types = ", ".join(ng.get("instanceTypes", ["?"]))
                    lines.append(f"       ✅ {ng_name}")
                    lines.append(f"          Tipo: {types} | AMI: {ng.get('amiType', '?')} | Status: {ng['status']}")
                    lines.append(f"          Scaling Min/Desired/Max: {sc.get('minSize','?')}/{sc.get('desiredSize','?')}/{sc.get('maxSize','?')}")
            else:
                lines.append("     ℹ️  Sem Node Groups (provável uso de Fargate profile)")

    except Exception as e:
        lines.append(f"  ⚠️  Erro: {e}")
    return lines


def collect_rds(session, region):
    lines = [f"\n{'─' * 65}", f"RDS / AURORA — {region}", "─" * 65]
    try:
        rds = session.client("rds", region_name=region)
        found = False

        # Aurora clusters
        clusters = rds.describe_db_clusters()["DBClusters"]
        if clusters:
            found = True
            lines.append(f"  Clusters Aurora ({len(clusters)}):")
            for c in clusters:
                multi_az = "✅ Multi-AZ" if c.get("MultiAZ") else "⚠️ Single-AZ (sem réplica reader)"
                members = ", ".join(m["DBInstanceIdentifier"] for m in c.get("DBClusterMembers", []))
                lines.append(f"\n  ✅ {c['DBClusterIdentifier']}")
                lines.append(f"     Engine: {c['Engine']} {c['EngineVersion']} | Status: {c['Status']}")
                lines.append(f"     {multi_az}")
                lines.append(f"     Writer endpoint: {c.get('Endpoint', '?')}")
                lines.append(f"     Reader endpoint: {c.get('ReaderEndpoint', '?')}")
                lines.append(f"     Members: {members or '(nenhum)'}")
                lines.append(f"     Backup: {c.get('BackupRetentionPeriod', 0)} dias | Janela: {c.get('PreferredBackupWindow', '?')}")

        # RDS standalone (não pertence a cluster Aurora)
        instances = rds.describe_db_instances()["DBInstances"]
        standalone = [i for i in instances if not i.get("DBClusterIdentifier")]
        if standalone:
            found = True
            lines.append(f"\n  Instâncias RDS standalone ({len(standalone)}):")
            for i in standalone:
                multi_az = "✅ Multi-AZ" if i.get("MultiAZ") else "⚠️ Single-AZ"
                icon = "✅" if i["DBInstanceStatus"] == "available" else "⚠️"
                stopped = " ⚠️ STOPPED" if i["DBInstanceStatus"] == "stopped" else ""
                lines.append(f"\n  {icon} {i['DBInstanceIdentifier']}{stopped}")
                lines.append(f"     Engine: {i['Engine']} {i['EngineVersion']} | Classe: {i['DBInstanceClass']}")
                lines.append(f"     {multi_az} | Status: {i['DBInstanceStatus']}")
                lines.append(f"     Storage: {i.get('AllocatedStorage', 0)} GB {i.get('StorageType', '')} | "
                             f"Backup: {i.get('BackupRetentionPeriod', 0)} dias")
                endpoint = i.get("Endpoint", {})
                lines.append(f"     Endpoint: {endpoint.get('Address', '?')}:{endpoint.get('Port', '?')}")

        if not found:
            lines.append("  ⚠️  Nenhum RDS ou Aurora encontrado — verificar se banco roda localmente na EC2")

    except Exception as e:
        lines.append(f"  ⚠️  Erro: {e}")
    return lines


def collect_lambda(session, region):
    lines = [f"\n{'─' * 65}", f"LAMBDA — {region}", "─" * 65]
    try:
        lmb = session.client("lambda", region_name=region)
        functions = []
        for page in lmb.get_paginator("list_functions").paginate():
            functions.extend(page["Functions"])

        if not functions:
            lines.append("  ℹ️  Nenhuma função Lambda encontrada")
            return lines

        runtimes = {}
        for f in functions:
            r = f.get("Runtime", "container/image")
            runtimes[r] = runtimes.get(r, 0) + 1

        lines.append(f"  Total: {len(functions)} funções")
        lines.append(f"  Runtimes: {', '.join(f'{v}x {k}' for k, v in sorted(runtimes.items()))}")

        for f in sorted(functions, key=lambda x: x["FunctionName"]):
            lines.append(f"\n  ✅ {f['FunctionName']}")
            lines.append(f"     Runtime: {f.get('Runtime', 'container')} | "
                         f"Memória: {f['MemorySize']} MB | Timeout: {f['Timeout']}s")
            lines.append(f"     Última modificação: {f.get('LastModified', '?')[:10]}")

    except Exception as e:
        lines.append(f"  ⚠️  Erro: {e}")
    return lines


def collect_alb(session, region):
    lines = [f"\n{'─' * 65}", f"LOAD BALANCERS (ALB/NLB) — {region}", "─" * 65]
    try:
        elbv2 = session.client("elbv2", region_name=region)
        lbs = []
        for page in elbv2.get_paginator("describe_load_balancers").paginate():
            lbs.extend(page["LoadBalancers"])

        if not lbs:
            lines.append("  ℹ️  Nenhum Load Balancer encontrado")
            return lines

        lines.append(f"  Total: {len(lbs)} Load Balancers")
        for lb in lbs:
            state = lb["State"]["Code"]
            icon = "✅" if state == "active" else "⚠️"
            azs = ", ".join(az["ZoneName"] for az in lb.get("AvailabilityZones", []))
            lines.append(f"\n  {icon} {lb['LoadBalancerName']}")
            lines.append(f"     Tipo: {lb['Type']} | Scheme: {lb['Scheme']} | Estado: {state}")
            lines.append(f"     DNS: {lb['DNSName']}")
            lines.append(f"     AZs: {azs}")

            try:
                tgs = elbv2.describe_target_groups(
                    LoadBalancerArn=lb["LoadBalancerArn"]
                )["TargetGroups"]
                if tgs:
                    lines.append(f"     Target Groups ({len(tgs)}):")
                    for tg in tgs:
                        try:
                            health = elbv2.describe_target_health(
                                TargetGroupArn=tg["TargetGroupArn"]
                            )["TargetHealthDescriptions"]
                            healthy = sum(1 for t in health if t["TargetHealth"]["State"] == "healthy")
                            total = len(health)
                            tg_icon = "✅" if healthy == total and total > 0 else "⚠️"
                            lines.append(f"       {tg_icon} {tg['TargetGroupName']} — {healthy}/{total} healthy "
                                         f"| Proto: {tg['Protocol']} Port: {tg['Port']}")
                        except Exception:
                            lines.append(f"       ℹ️  {tg['TargetGroupName']}")
            except Exception:
                pass

    except Exception as e:
        lines.append(f"  ⚠️  Erro: {e}")
    return lines


def collect_waf(session, region):
    lines = [f"\n{'─' * 65}", f"WAF v2 — {region}", "─" * 65]
    try:
        waf = session.client("wafv2", region_name=region)
        found = False

        regional = waf.list_web_acls(Scope="REGIONAL").get("WebACLs", [])
        if regional:
            found = True
            lines.append(f"  WAFs REGIONAL ({len(regional)}):")
            for w in regional:
                lines.append(f"    ✅ {w['Name']}")
                lines.append(f"       ARN: {w['ARN']}")

        if region == "us-east-1":
            cf_wafs = waf.list_web_acls(Scope="CLOUDFRONT").get("WebACLs", [])
            if cf_wafs:
                found = True
                lines.append(f"\n  WAFs CLOUDFRONT ({len(cf_wafs)}):")
                for w in cf_wafs:
                    lines.append(f"    ✅ {w['Name']}")
                    lines.append(f"       ARN: {w['ARN']}")

        if not found:
            lines.append("  ℹ️  Nenhum WAF encontrado")

    except Exception as e:
        lines.append(f"  ⚠️  Erro: {e}")
    return lines


def collect_efs(session, region):
    lines = [f"\n{'─' * 65}", f"EFS — {region}", "─" * 65]
    try:
        efs = session.client("efs", region_name=region)
        fss = []
        for page in efs.get_paginator("describe_file_systems").paginate():
            fss.extend(page["FileSystems"])

        if not fss:
            lines.append("  ℹ️  Nenhum EFS encontrado")
            return lines

        lines.append(f"  Total: {len(fss)} file systems")
        for f in fss:
            name = tag_name(f.get("Tags", []))
            size_gb = f.get("SizeInBytes", {}).get("Value", 0) / (1024 ** 3)

            try:
                bp = efs.describe_backup_policy(FileSystemId=f["FileSystemId"])
                backup_status = bp["BackupPolicy"]["Status"]
                backup = "✅" if backup_status == "ENABLED" else f"⚠️ {backup_status}"
            except Exception:
                backup = "⚠️ sem backup automático"

            # Mount targets
            mts = efs.describe_mount_targets(FileSystemId=f["FileSystemId"])["MountTargets"]
            mt_azs = ", ".join(m.get("AvailabilityZoneName", "?") for m in mts)

            lines.append(f"\n  ✅ {name} ({f['FileSystemId']})")
            lines.append(f"     Tamanho: {size_gb:.2f} GB | Throughput: {f.get('ThroughputMode', '?')} | State: {f['LifeCycleState']}")
            lines.append(f"     Mount targets: {len(mts)} — {mt_azs}")
            lines.append(f"     Backup automático: {backup}")

    except Exception as e:
        lines.append(f"  ⚠️  Erro: {e}")
    return lines


def collect_backup(session, region):
    lines = [f"\n{'─' * 65}", f"AWS BACKUP — {region}", "─" * 65]
    try:
        backup = session.client("backup", region_name=region)

        plans = backup.list_backup_plans().get("BackupPlansList", [])
        if plans:
            lines.append(f"  Planos de backup ({len(plans)}):")
            for p in plans:
                lines.append(f"    ✅ {p['BackupPlanName']} | ID: {p['BackupPlanId']}")
        else:
            lines.append("  ⚠️  Nenhum plano de backup configurado")

        try:
            resources = backup.list_protected_resources().get("Results", [])
            if resources:
                by_type = {}
                for r in resources:
                    t = r.get("ResourceType", "?")
                    by_type[t] = by_type.get(t, 0) + 1
                lines.append(f"  Recursos protegidos: {', '.join(f'{v}x {k}' for k, v in sorted(by_type.items()))}")
            else:
                lines.append("  ⚠️  Nenhum recurso protegido pelo AWS Backup")
        except Exception:
            pass

    except Exception as e:
        lines.append(f"  ⚠️  Erro: {e}")
    return lines


def collect_cloudwatch(session, region):
    lines = [f"\n{'─' * 65}", f"CLOUDWATCH ALARMS — {region}", "─" * 65]
    try:
        cw = session.client("cloudwatch", region_name=region)
        all_alarms = []
        for page in cw.get_paginator("describe_alarms").paginate():
            all_alarms.extend(page["MetricAlarms"])

        in_alarm = [a for a in all_alarms if a["StateValue"] == "ALARM"]
        insuff = [a for a in all_alarms if a["StateValue"] == "INSUFFICIENT_DATA"]

        lines.append(f"  Total de alarmes: {len(all_alarms)} | OK: {len(all_alarms) - len(in_alarm) - len(insuff)} | "
                     f"ALARM: {len(in_alarm)} | INSUFFICIENT_DATA: {len(insuff)}")

        if in_alarm:
            lines.append(f"\n  ⚠️  Alarmes em ALARM ({len(in_alarm)}):")
            for a in in_alarm:
                lines.append(f"     ⚠️ {a['AlarmName']}")
                lines.append(f"        Métrica: {a['Namespace']}/{a['MetricName']}")
        else:
            lines.append("  ✅ Nenhum alarme em estado ALARM")

    except Exception as e:
        lines.append(f"  ⚠️  Erro: {e}")
    return lines


def collect_cloudfront(session):
    lines = [f"\n{'─' * 65}", "CLOUDFRONT (global)", "─" * 65]
    try:
        cf = session.client("cloudfront", region_name="us-east-1")
        dists = []
        for page in cf.get_paginator("list_distributions").paginate():
            items = page.get("DistributionList", {}).get("Items", [])
            dists.extend(items)

        if not dists:
            lines.append("  ℹ️  Nenhuma distribuição CloudFront encontrada")
            return lines

        lines.append(f"  Total: {len(dists)} distribuições")
        for d in dists:
            enabled = "✅ Enabled" if d.get("Enabled") else "⚠️ Disabled"
            aliases = d.get("Aliases", {}).get("Items", [])
            domains = ", ".join(aliases) if aliases else d["DomainName"]
            origins = ", ".join(o["DomainName"] for o in d.get("Origins", {}).get("Items", []))
            waf_id = d.get("WebACLId", "")
            waf_info = f"✅ {waf_id.split('/')[-1]}" if waf_id else "⚠️ SEM WAF"
            lines.append(f"\n  {enabled} | ID: {d['Id']} | Status: {d['Status']}")
            lines.append(f"     Domínios: {domains}")
            lines.append(f"     Origins: {origins}")
            lines.append(f"     WAF: {waf_info}")
            lines.append(f"     PriceClass: {d.get('PriceClass', '?')}")

    except Exception as e:
        lines.append(f"  ⚠️  Erro: {e}")
    return lines


def collect_s3(session):
    lines = [f"\n{'─' * 65}", "S3 (global)", "─" * 65]
    try:
        s3 = session.client("s3", region_name="us-east-1")
        buckets = s3.list_buckets().get("Buckets", [])

        if not buckets:
            lines.append("  ℹ️  Nenhum bucket S3 encontrado")
            return lines

        lines.append(f"  Total: {len(buckets)} buckets")
        for b in buckets:
            name = b["Name"]
            created = b.get("CreationDate", "?")
            if hasattr(created, "strftime"):
                created = created.strftime("%Y-%m-%d")

            try:
                loc = s3.get_bucket_location(Bucket=name)
                bucket_region = loc["LocationConstraint"] or "us-east-1"
            except Exception:
                bucket_region = "?"

            try:
                pab = s3.get_public_access_block(Bucket=name)["PublicAccessBlockConfiguration"]
                pub_blocked = all(pab.values())
                pub_icon = "✅ bloqueado" if pub_blocked else "⚠️ ACESSO PÚBLICO"
            except Exception:
                pub_icon = "⚠️ sem configuração de block"

            try:
                ver = s3.get_bucket_versioning(Bucket=name)
                versioning = ver.get("Status", "Disabled")
            except Exception:
                versioning = "?"

            icon = "✅" if "bloqueado" in pub_icon else "⚠️"
            lines.append(f"  {icon} {name}")
            lines.append(f"     Região: {bucket_region} | Versioning: {versioning} | "
                         f"Public Access: {pub_icon} | Criado: {created}")

    except Exception as e:
        lines.append(f"  ⚠️  Erro: {e}")
    return lines


def collect_route53(session):
    lines = [f"\n{'─' * 65}", "ROUTE53 (global)", "─" * 65]
    try:
        r53 = session.client("route53", region_name="us-east-1")
        zones = []
        for page in r53.get_paginator("list_hosted_zones").paginate():
            zones.extend(page["HostedZones"])

        if not zones:
            lines.append("  ℹ️  Nenhuma hosted zone encontrada")
            return lines

        lines.append(f"  Total: {len(zones)} hosted zones")
        for z in zones:
            tipo = "privada" if z["Config"].get("PrivateZone") else "pública"
            records = z.get("ResourceRecordSetCount", "?")
            lines.append(f"  ✅ {z['Name'].rstrip('.')} ({tipo}) | {records} registros")

    except Exception as e:
        lines.append(f"  ⚠️  Erro: {e}")
    return lines


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def process_account(account_id, account_name, session):
    output = []
    output.append("=" * 65)
    output.append(f"WORKLOAD DISCOVERY — {account_name} ({account_id})")
    output.append(f"Gerado em: {ts()} UTC")
    output.append("Regiões: " + ", ".join(TARGET_REGIONS))
    output.append("=" * 65)

    output.extend(collect_account_info(session))

    for region in TARGET_REGIONS:
        log(f"    [{account_name}] Região {region}...")
        output.extend(collect_vpc(session, region))
        output.extend(collect_ec2(session, region))
        output.extend(collect_asg(session, region))
        output.extend(collect_ecs(session, region))
        output.extend(collect_eks(session, region))
        output.extend(collect_rds(session, region))
        output.extend(collect_lambda(session, region))
        output.extend(collect_alb(session, region))
        output.extend(collect_waf(session, region))
        output.extend(collect_efs(session, region))
        output.extend(collect_backup(session, region))
        output.extend(collect_cloudwatch(session, region))

    log(f"    [{account_name}] Serviços globais...")
    output.extend(collect_cloudfront(session))
    output.extend(collect_s3(session))
    output.extend(collect_route53(session))

    output.append("\n" + "=" * 65)
    output.append("FIM DO LEVANTAMENTO")
    output.append("=" * 65)

    safe_name = account_name.replace(" ", "_").replace("/", "-").replace("\\", "-")
    filename = f"{OUTPUT_DIR}/levantamento_{account_id}_{safe_name}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(output))

    return filename


def main():
    # Validação básica
    if not ACCOUNTS or ACCOUNTS[0]["id"] == "111111111111":
        print("ERRO: preencha a lista ACCOUNTS no início do script antes de rodar.")
        print("Exemplo:")
        print('  ACCOUNTS = [')
        print('      {"id": "123456789012", "name": "minha-conta"},')
        print('  ]')
        return

    log(f"Iniciando discovery de {len(ACCOUNTS)} conta(s)...")
    log(f"Regiões: {', '.join(TARGET_REGIONS)}")

    sts_local = boto3.client("sts")
    current_account = sts_local.get_caller_identity()["Account"]
    log(f"Conta de execução (payer): {current_account}")

    results = []
    failed = []

    for acc in ACCOUNTS:
        account_id = acc["id"]
        account_name = acc["name"]
        log(f"\nProcessando: {account_name} ({account_id})")

        if account_id == current_account:
            log("  → Conta atual — usando sessão local")
            session = boto3.Session()
            role_used = "LOCAL_SESSION"
        else:
            session, role_used = assume_role(account_id)
            if not session:
                log(f"  ⚠️  Nenhuma role funcionou em {account_name} ({account_id})")
                log(f"       Roles tentadas: {', '.join(ROLE_CANDIDATES)}")
                failed.append(f"{account_name} ({account_id}) — sem role acessível")
                continue
            log(f"  → Role assumida: {role_used}")

        try:
            filename = process_account(account_id, account_name, session)
            log(f"  ✅ Salvo: {filename}")
            results.append(filename)
        except Exception as e:
            log(f"  ⚠️  Erro ao processar {account_name}: {e}")
            failed.append(f"{account_name} ({account_id}) — {e}")

    # Resumo final
    print("\n" + "=" * 65)
    print("DISCOVERY CONCLUÍDO")
    print("=" * 65)
    print(f"✅ Contas processadas com sucesso: {len(results)}/{len(ACCOUNTS)}")
    for f in results:
        print(f"   {f}")
    if failed:
        print(f"\n⚠️  Falhas ({len(failed)}):")
        for f in failed:
            print(f"   {f}")
    print("\nComo usar a saída:")
    print("  cat output/levantamento_<account_id>_<name>.txt")
    print("  ls output/")


if __name__ == "__main__":
    main()
