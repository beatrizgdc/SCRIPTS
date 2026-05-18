#!/bin/bash

ARQUIVO="auditoria_ebs.csv"

# cria cabeçalho
echo "VOLUME_ID,ENCRYPTED,EM_USO,INSTANCE_ID,STATE,TIPO,CRIADO_POR,TEM_SNAPSHOT,SNAPSHOT_QTD,DEVICE,KMS_KEY,ACAO" > $ARQUIVO

########################################
# pega todos os volumes
########################################

VOLUMES=$(aws ec2 describe-volumes \
  --query 'Volumes[].VolumeId' \
  --output text)

TOTAL=$(echo $VOLUMES | wc -w)
COUNT=0

echo "Total de volumes: $TOTAL"

for vol in $VOLUMES
do
  COUNT=$((COUNT+1))

  echo "=============================="
  echo "[$COUNT/$TOTAL] Auditando $vol"

  ########################################
  # coleta dados do volume
  ########################################

  DADOS=$(aws ec2 describe-volumes \
    --volume-ids $vol \
    --query 'Volumes[0]' \
    --output json)

  ########################################
  # encrypted
  ########################################

  ENCRYPTED=$(echo $DADOS | jq -r '.Encrypted')

  if [ "$ENCRYPTED" == "true" ]; then
    ENCRYPTED="SIM"
  else
    ENCRYPTED="NAO"
  fi

  ########################################
  # state
  ########################################

  STATE=$(echo $DADOS | jq -r '.State')

  ########################################
  # tipo
  ########################################

  TIPO=$(echo $DADOS | jq -r '.VolumeType')

  ########################################
  # attachment
  ########################################

  INSTANCE_ID=$(echo $DADOS | jq -r '.Attachments[0].InstanceId // "-"')

  DEVICE=$(echo $DADOS | jq -r '.Attachments[0].Device // "-"')

  EM_USO="NAO"

  if [ "$INSTANCE_ID" != "-" ] && \
     [ "$INSTANCE_ID" != "null" ]; then

    EM_USO="SIM"
  else
    INSTANCE_ID="-"
  fi

  ########################################
  # snapshot origem
  ########################################

  SNAPSHOT_ID=$(echo $DADOS | jq -r '.SnapshotId // "-"')

  CRIADO_POR="MANUAL"

  if [ "$SNAPSHOT_ID" != "-" ] && \
     [ "$SNAPSHOT_ID" != "null" ]; then

    SNAP_DESC=$(aws ec2 describe-snapshots \
      --snapshot-ids $SNAPSHOT_ID \
      --query 'Snapshots[0].Description' \
      --output text 2>/dev/null)

    if echo "$SNAP_DESC" | grep -iq "awsbackup"; then
      CRIADO_POR="AWS_BACKUP"
    else
      CRIADO_POR="SNAPSHOT"
    fi
  fi

  ########################################
  # quantidade snapshots
  ########################################

  SNAPSHOT_QTD=$(aws ec2 describe-snapshots \
    --owner-ids self \
    --filters "Name=volume-id,Values=$vol" \
    --query 'length(Snapshots)' \
    --output text)

  if [ "$SNAPSHOT_QTD" -gt 0 ]; then
    TEM_SNAPSHOT="SIM"
  else
    TEM_SNAPSHOT="NAO"
  fi

  ########################################
  # kms
  ########################################

  KMS_KEY=$(echo $DADOS | jq -r '.KmsKeyId // "-"')

  ########################################
  # decisão
  ########################################

  ACAO="REVISAR"

  if [ "$ENCRYPTED" == "NAO" ] && \
     [ "$EM_USO" == "SIM" ]; then

    ACAO="MIGRAR_CRIPTOGRAFADO"

  elif [ "$ENCRYPTED" == "NAO" ] && \
       [ "$EM_USO" == "NAO" ]; then

    ACAO="PODE_REMOVER"

  elif [ "$ENCRYPTED" == "SIM" ] && \
       [ "$EM_USO" == "NAO" ]; then

    ACAO="VALIDAR_EXCLUSAO"

  elif [ "$ENCRYPTED" == "SIM" ] && \
       [ "$EM_USO" == "SIM" ]; then

    ACAO="OK"

  fi

  ########################################
  # salva
  ########################################

  echo "$vol,$ENCRYPTED,$EM_USO,$INSTANCE_ID,$STATE,$TIPO,$CRIADO_POR,$TEM_SNAPSHOT,$SNAPSHOT_QTD,$DEVICE,$KMS_KEY,$ACAO" >> $ARQUIVO

done

echo "=============================="
echo "Auditoria finalizada!"
