#!/usr/bin/env python3
"""
Levantamento de infraestrutura WordPress - Darede MSP
Rodar no AWS CloudShell de cada conta
Permissões necessárias: ReadOnly (SecurityAudit ou ViewOnlyAccess)
"""

import boto3
import json
from datetime import datetime
from botocore.exceptions import ClientError

SEPARADOR = "=" * 65
SEP_SECAO = "-" * 65

def titulo(texto):
    print(f"\n{SEPARADOR}")
    print(f"  {texto}")
    print(SEPARADOR)

def secao(texto):
    print(f"\n{SEP_SECAO}")
    print(f"  {texto}")
    print(SEP_SECAO)

def campo(label, valor, aviso=False):
    prefixo = "  ⚠️ " if aviso else "  ✅ "
    print(f"{prefixo}{label}: {valor}")

def nao_encontrado(recurso):
    print(f"  ℹ️  Nenhum {recurso} encontrado nesta conta.")

# ─────────────────────────────────────────────
# INFO DA CONTA
# ─────────────────────────────────────────────
def get_account_info():
    sts = boto3.client('sts')
    identity = sts.get_caller_identity()
    session = boto3.session.Session()
    return {
        'account_id': identity['Account'],
        'region': session.region_name or 'us-east-1'
    }

# ─────────────────────────────────────────────
# EC2 - INSTÂNCIAS RODANDO
# ─────────────────────────────────────────────
def check_ec2():
    secao("EC2 - Instâncias em execução")
    ec2 = boto3.client('ec2')
    try:
        response = ec2.describe_instances(
            Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
        )
        instancias = []
        for r in response['Reservations']:
            for i in r['Instances']:
                instancias.append(i)

        if not instancias:
            nao_encontrado("instância EC2 running")
            return

        print(f"  Total de instâncias running: {len(instancias)}\n")
        for i in instancias:
            nome = next((t['Value'] for t in i.get('Tags', []) if t['Key'] == 'Name'), '(sem nome)')
            print(f"  → {nome}")
            campo("ID", i['InstanceId'])
            campo("Tipo", i['InstanceType'])
            campo("AZ", i['Placement']['AvailabilityZone'])
            campo("IP Privado", i.get('PrivateIpAddress', 'N/A'))
            campo("IP Público", i.get('PublicIpAddress', 'N/A'))

            # Verifica se tem volume EBS com delete on termination
            for bdm in i.get('BlockDeviceMappings', []):
                ebs = bdm.get('Ebs', {})
                if not ebs.get('DeleteOnTermination', True):
                    campo("EBS persistente", bdm['DeviceName'])
            print()

    except ClientError as e:
        print(f"  ❌ Erro ao buscar EC2: {e}")

# ─────────────────────────────────────────────
# AUTO SCALING GROUPS
# ─────────────────────────────────────────────
def check_asg():
    secao("Auto Scaling Groups (EC2)")
    asg_client = boto3.client('autoscaling')
    try:
        response = asg_client.describe_auto_scaling_groups()
        asgs = response['AutoScalingGroups']

        if not asgs:
            nao_encontrado("Auto Scaling Group")
            print("  ⚠️  ATENÇÃO: Sem ASG = sem escalonamento automático de EC2!")
            return

        for asg in asgs:
            print(f"  → {asg['AutoScalingGroupName']}")
            campo("Min / Desired / Max", f"{asg['MinSize']} / {asg['DesiredCapacity']} / {asg['MaxSize']}")
            campo("AZs", ", ".join(asg['AvailabilityZones']))

            # Políticas de scaling
            policies = asg_client.describe_policies(AutoScalingGroupName=asg['AutoScalingGroupName'])
            if policies['ScalingPolicies']:
                campo("Políticas de scaling", f"{len(policies['ScalingPolicies'])} encontrada(s)")
                for p in policies['ScalingPolicies']:
                    print(f"       - {p['PolicyName']} ({p['PolicyType']})")
            else:
                campo("Políticas de scaling", "NENHUMA - scaling manual apenas", aviso=True)
            print()

    except ClientError as e:
        print(f"  ❌ Erro ao buscar ASGs: {e}")

# ─────────────────────────────────────────────
# ECS - CLUSTERS E SERVIÇOS
# ─────────────────────────────────────────────
def check_ecs():
    secao("ECS - Clusters e Serviços")
    ecs = boto3.client('ecs')
    aas = boto3.client('application-autoscaling')

    try:
        clusters = ecs.list_clusters()['clusterArns']
        if not clusters:
            nao_encontrado("cluster ECS")
            return

        cluster_details = ecs.describe_clusters(clusters=clusters)['clusters']
        for cluster in cluster_details:
            nome_cluster = cluster['clusterName']
            print(f"  → Cluster: {nome_cluster}")
            campo("Status", cluster['status'])
            campo("Serviços ativos", cluster['activeServicesCount'])
            campo("Tasks rodando", cluster['runningTasksCount'])
            print()

            # Serviços do cluster
            service_arns = ecs.list_services(cluster=nome_cluster, maxResults=100)['serviceArns']
            if not service_arns:
                continue

            services = ecs.describe_services(cluster=nome_cluster, services=service_arns)['services']
            for svc in services:
                print(f"    Serviço: {svc['serviceName']}")
                campo("  Launch type", svc.get('launchType', 'FARGATE (capacityProviders)'), )
                campo("  Desired / Running / Pending",
                      f"{svc['desiredCount']} / {svc['runningCount']} / {svc['pendingCount']}")

                # Task definition
                td_arn = svc['taskDefinition']
                td = ecs.describe_task_definition(taskDefinition=td_arn)['taskDefinition']
                for container in td.get('containerDefinitions', []):
                    campo("  Container", container['name'])
                    campo("    CPU / Memória", f"{container.get('cpu','?')} / {container.get('memory','?')} MB")
                    # Verifica se tem EFS mount
                    for mount in container.get('mountPoints', []):
                        campo("    Mount", mount['containerPath'])
                for vol in td.get('volumes', []):
                    if 'efsVolumeConfiguration' in vol:
                        campo("  EFS Volume", vol['name'] + " → FS: " + vol['efsVolumeConfiguration']['fileSystemId'])

                # Auto Scaling do serviço
                resource_id = f"service/{nome_cluster}/{svc['serviceName']}"
                try:
                    scalable = aas.describe_scalable_targets(
                        ServiceNamespace='ecs',
                        ResourceIds=[resource_id]
                    )['ScalableTargets']
                    if scalable:
                        st = scalable[0]
                        campo("  Auto Scaling ECS",
                              f"Min {st['MinCapacity']} / Max {st['MaxCapacity']}")
                        policies = aas.describe_scaling_policies(
                            ServiceNamespace='ecs',
                            ResourceId=resource_id
                        )['ScalingPolicies']
                        for p in policies:
                            print(f"       Política: {p['PolicyName']} ({p['PolicyType']})")
                    else:
                        campo("  Auto Scaling ECS", "NÃO CONFIGURADO", aviso=True)
                except ClientError:
                    campo("  Auto Scaling ECS", "Não verificado", aviso=True)
                print()

    except ClientError as e:
        print(f"  ❌ Erro ao buscar ECS: {e}")

# ─────────────────────────────────────────────
# RDS - INSTÂNCIAS
# ─────────────────────────────────────────────
def check_rds():
    secao("RDS - Instâncias de Banco de Dados")
    rds = boto3.client('rds')
    try:
        instances = rds.describe_db_instances()['DBInstances']
        if not instances:
            nao_encontrado("instância RDS")
            return

        for db in instances:
            print(f"  → {db['DBInstanceIdentifier']}")
            campo("Engine", f"{db['Engine']} {db['EngineVersion']}")
            campo("Classe", db['DBInstanceClass'])
            campo("Status", db['DBInstanceStatus'])
            campo("Multi-AZ", "✓ Habilitado" if db['MultiAZ'] else "✗ NÃO habilitado (sem failover automático)", aviso=not db['MultiAZ'])
            campo("Backup retention", f"{db['BackupRetentionPeriod']} dias", aviso=db['BackupRetentionPeriod'] == 0)
            campo("Janela de backup", db.get('PreferredBackupWindow', 'N/A'))
            campo("Storage", f"{db['AllocatedStorage']} GB ({db['StorageType']})")
            campo("Endpoint", db.get('Endpoint', {}).get('Address', 'N/A'))

            # Read Replicas
            replicas = db.get('ReadReplicaDBInstanceIdentifiers', [])
            if replicas:
                campo("Read Replicas", ", ".join(replicas))
            else:
                campo("Read Replicas", "Nenhuma", aviso=False)
            print()

    except ClientError as e:
        print(f"  ❌ Erro ao buscar RDS: {e}")

# ─────────────────────────────────────────────
# AWS BACKUP
# ─────────────────────────────────────────────
def check_backup():
    secao("AWS Backup - Planos e Cofres")
    backup = boto3.client('backup')
    try:
        plans = backup.list_backup_plans()['BackupPlansList']
        if not plans:
            nao_encontrado("plano de backup")
            print("  ⚠️  AWS Backup sem planos configurados!")
            return

        for plan in plans:
            print(f"  → Plano: {plan['BackupPlanName']}")
            campo("ID", plan['BackupPlanId'])
            campo("Criado em", str(plan.get('CreationDate', 'N/A'))[:10])

            # Regras do plano
            try:
                detail = backup.get_backup_plan(BackupPlanId=plan['BackupPlanId'])
                for rule in detail['BackupPlan'].get('Rules', []):
                    print(f"    Regra: {rule['RuleName']}")
                    campo("    Cofre", rule['TargetBackupVaultName'])
                    campo("    Schedule", rule.get('ScheduleExpression', 'N/A'))
                    lifecycle = rule.get('Lifecycle', {})
                    campo("    Retenção (dias)", lifecycle.get('DeleteAfterDays', 'N/A'))
                    campo("    Cold storage após", lifecycle.get('MoveToColdStorageAfterDays', 'N/A'))
            except ClientError:
                pass

            # Seleções (o que está coberto)
            selections = backup.list_backup_selections(BackupPlanId=plan['BackupPlanId'])['BackupSelectionsList']
            if selections:
                print(f"    Recursos cobertos:")
                for sel in selections:
                    try:
                        sel_detail = backup.get_backup_selection(
                            BackupPlanId=plan['BackupPlanId'],
                            SelectionId=sel['SelectionId']
                        )['BackupSelection']
                        resources = sel_detail.get('Resources', [])
                        conditions = sel_detail.get('ListOfTags', [])
                        if resources:
                            for r in resources:
                                print(f"      - {r}")
                        if conditions:
                            for c in conditions:
                                print(f"      - Tag: {c['ConditionKey']}={c['ConditionValue']}")
                    except ClientError:
                        print(f"      - {sel['SelectionName']}")
            else:
                campo("  Recursos cobertos", "Nenhuma seleção encontrada!", aviso=True)
            print()

    except ClientError as e:
        print(f"  ❌ Erro ao buscar AWS Backup: {e}")

# ─────────────────────────────────────────────
# ALB - LOAD BALANCERS
# ─────────────────────────────────────────────
def check_alb():
    secao("ALB - Application Load Balancers")
    elb = boto3.client('elbv2')
    try:
        lbs = elb.describe_load_balancers()['LoadBalancers']
        albs = [lb for lb in lbs if lb['Type'] == 'application']

        if not albs:
            nao_encontrado("ALB")
            return

        for alb in albs:
            print(f"  → {alb['LoadBalancerName']}")
            campo("DNS", alb['DNSName'])
            campo("Scheme", alb['Scheme'])
            campo("AZs", ", ".join([az['ZoneName'] for az in alb['AvailabilityZones']]))

            # Listeners
            listeners = elb.describe_listeners(LoadBalancerArn=alb['LoadBalancerArn'])['Listeners']
            campo("Listeners", f"{len(listeners)} encontrado(s)")
            for l in listeners:
                print(f"    Listener: {l['Protocol']}:{l['Port']}")
                if l['Protocol'] == 'HTTPS':
                    certs = l.get('Certificates', [])
                    if certs:
                        campo("    Certificado ARN", certs[0]['CertificateArn'][-40:] + "...")

            # Target Groups
            tgs = elb.describe_target_groups(LoadBalancerArn=alb['LoadBalancerArn'])['TargetGroups']
            campo("Target Groups", len(tgs))
            for tg in tgs:
                health = elb.describe_target_health(TargetGroupArn=tg['TargetGroupArn'])
                saudaveis = sum(1 for t in health['TargetHealthDescriptions']
                               if t['TargetHealth']['State'] == 'healthy')
                total = len(health['TargetHealthDescriptions'])
                status = "⚠️" if saudaveis < total else "✅"
                print(f"    TG: {tg['TargetGroupName']} → {status} {saudaveis}/{total} targets healthy")
                campo("    Protocolo/Porta", f"{tg['Protocol']}:{tg['Port']}")
                campo("    Target type", tg['TargetType'])
            print()

    except ClientError as e:
        print(f"  ❌ Erro ao buscar ALBs: {e}")

# ─────────────────────────────────────────────
# CLOUDFRONT
# ─────────────────────────────────────────────
def check_cloudfront():
    secao("CloudFront - Distribuições")
    cf = boto3.client('cloudfront', region_name='us-east-1')
    try:
        response = cf.list_distributions()
        dist_list = response['DistributionList']
        items = dist_list.get('Items', [])

        if not items:
            nao_encontrado("distribuição CloudFront")
            return

        campo("Total de distribuições", len(items))
        print()
        for d in items:
            aliases = d.get('Aliases', {}).get('Items', ['(sem alias)'])
            print(f"  → {', '.join(aliases)}")
            campo("ID", d['Id'])
            campo("Domain", d['DomainName'])
            campo("Status", d['Status'])
            campo("Enabled", d['Enabled'])
            campo("WAF WebACL", d.get('WebACLId', 'NÃO ASSOCIADO') or 'NÃO ASSOCIADO',
                  aviso=not d.get('WebACLId'))

            # Origins
            origins = d.get('Origins', {}).get('Items', [])
            for origin in origins:
                campo("Origin", f"{origin['Id']} → {origin['DomainName']}")
            print()

    except ClientError as e:
        print(f"  ❌ Erro ao buscar CloudFront: {e}")

# ─────────────────────────────────────────────
# WAF
# ─────────────────────────────────────────────
def check_waf():
    secao("WAF v2 - WebACLs")
    waf = boto3.client('wafv2', region_name='us-east-1')
    try:
        # CloudFront (global)
        cf_acls = waf.list_web_acls(Scope='CLOUDFRONT')['WebACLs']
        print(f"  Scope CLOUDFRONT (global): {len(cf_acls)} WebACL(s)")
        for acl in cf_acls:
            print(f"    → {acl['Name']}")
            campo("  ARN", acl['ARN'][-60:] + "...")

        # Regional
        regional_acls = waf.list_web_acls(Scope='REGIONAL')['WebACLs']
        print(f"\n  Scope REGIONAL: {len(regional_acls)} WebACL(s)")
        for acl in regional_acls:
            print(f"    → {acl['Name']}")

            # Recursos associados
            try:
                resources = waf.list_resources_for_web_acl(WebACLArn=acl['ARN'])
                for r in resources.get('ResourceArns', []):
                    print(f"      Associado a: ...{r[-50:]}")
            except ClientError:
                pass

    except ClientError as e:
        print(f"  ❌ Erro ao buscar WAF: {e}")

# ─────────────────────────────────────────────
# EFS
# ─────────────────────────────────────────────
def check_efs():
    secao("EFS - Sistemas de Arquivos")
    efs = boto3.client('efs')
    try:
        filesystems = efs.describe_file_systems()['FileSystems']
        if not filesystems:
            nao_encontrado("sistema de arquivos EFS")
            return

        for fs in filesystems:
            nome = fs.get('Name', '(sem nome)')
            print(f"  → {nome}")
            campo("ID", fs['FileSystemId'])
            campo("Estado", fs['LifeCycleState'])
            campo("Tamanho", f"{round(fs['SizeInBytes']['Value'] / (1024**3), 2)} GB")
            campo("Performance", fs['PerformanceMode'])
            campo("Throughput mode", fs['ThroughputMode'])

            # Mount targets
            mts = efs.describe_mount_targets(FileSystemId=fs['FileSystemId'])['MountTargets']
            campo("Mount targets", f"{len(mts)} AZ(s)")
            for mt in mts:
                print(f"    AZ: {mt['AvailabilityZoneName']} | IP: {mt['IpAddress']} | Estado: {mt['LifeCycleState']}")

            # Backup policy
            try:
                bp = efs.describe_backup_policy(FileSystemId=fs['FileSystemId'])
                status_bp = bp['BackupPolicy']['Status']
                campo("Backup automático EFS", status_bp, aviso=(status_bp != 'ENABLED'))
            except ClientError:
                campo("Backup automático EFS", "Não verificado", aviso=True)
            print()

    except ClientError as e:
        print(f"  ❌ Erro ao buscar EFS: {e}")

# ─────────────────────────────────────────────
# RESUMO FINAL
# ─────────────────────────────────────────────
def resumo(info):
    titulo(f"FIM DO LEVANTAMENTO | Conta: {info['account_id']} | Região: {info['region']}")
    print(f"  Executado em: {datetime.now().strftime('%d/%m/%Y às %H:%M')}")
    print(f"\n  Copie a saída acima e repasse para preenchimento da documentação.")
    print(f"{SEPARADOR}\n")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    info = get_account_info()
    titulo(f"LEVANTAMENTO DE INFRAESTRUTURA WORDPRESS - DAREDE MSP")
    campo("Conta AWS", info['account_id'])
    campo("Região", info['region'])
    campo("Data/Hora", datetime.now().strftime('%d/%m/%Y %H:%M'))

    check_ec2()
    check_asg()
    check_ecs()
    check_rds()
    check_backup()
    check_alb()
    check_cloudfront()
    check_waf()
    check_efs()
    resumo(info)

if __name__ == '__main__':
    main()
