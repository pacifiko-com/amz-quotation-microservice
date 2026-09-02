#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Setup del rol OIDC de deploy para amz-quotation-microservice (SAM/Lambda).
# Uso:  aws sso login  (o exporta credenciales admin)  &&  ./.github/deploy-setup.sh
# Requiere: AWS CLI con permisos para crear IAM roles/policies.
# El OIDC provider de GitHub ya existe en la cuenta (no lo recrea este script).
# Al terminar imprime el ARN para el secret AWS_DEPLOY_ROLE_ARN en GitHub.
# ---------------------------------------------------------------------------
set -euo pipefail

SERVICE="amz-quotation-microservice"
REPO="pacifiko-com/amz-quotation-microservice"

# ---- normalmente fijas ----
ACCOUNT="796868798913"
REGION="us-east-2"
STACK_QA="$SERVICE-qa"                    # coincide con samconfig.toml
STACK_PROD="$SERVICE-prod"
ROLE="gha-$SERVICE-deploy"

echo ">> Servicio: $SERVICE  |  Repo: $REPO  |  Role: $ROLE"

# ---- 1. Trust policy (OIDC, limitada a este repo) ----
cat > trust.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::$ACCOUNT:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
      "StringLike": { "token.actions.githubusercontent.com:sub": "repo:$REPO:*" }
    }
  }]
}
EOF

# ---- 2. Policy least-privilege del deploy ----
cat > deploy-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "CloudFormationStacks", "Effect": "Allow",
      "Action": ["cloudformation:CreateStack","cloudformation:UpdateStack","cloudformation:DeleteStack",
        "cloudformation:CreateChangeSet","cloudformation:DeleteChangeSet","cloudformation:ExecuteChangeSet",
        "cloudformation:DescribeChangeSet","cloudformation:DescribeStacks","cloudformation:DescribeStackEvents",
        "cloudformation:DescribeStackResource","cloudformation:DescribeStackResources","cloudformation:ListStackResources",
        "cloudformation:GetTemplate","cloudformation:GetTemplateSummary"],
      "Resource": [
        "arn:aws:cloudformation:$REGION:$ACCOUNT:stack/$STACK_QA/*",
        "arn:aws:cloudformation:$REGION:$ACCOUNT:stack/$STACK_PROD/*",
        "arn:aws:cloudformation:$REGION:$ACCOUNT:stack/aws-sam-cli-managed-default/*"] },
    { "Sid": "CloudFormationValidate", "Effect": "Allow",
      "Action": ["cloudformation:ValidateTemplate","cloudformation:ListStacks"], "Resource": "*" },
    { "Sid": "SamTransform", "Effect": "Allow",
      "Action": "cloudformation:CreateChangeSet",
      "Resource": "arn:aws:cloudformation:$REGION:aws:transform/Serverless-2016-10-31" },
    { "Sid": "SamManagedBucket", "Effect": "Allow",
      "Action": ["s3:CreateBucket","s3:GetBucketLocation","s3:GetBucketPolicy","s3:PutBucketPolicy",
        "s3:PutBucketTagging","s3:PutBucketVersioning","s3:PutBucketPublicAccessBlock","s3:PutEncryptionConfiguration",
        "s3:ListBucket","s3:GetObject","s3:PutObject","s3:GetObjectTagging","s3:PutObjectTagging"],
      "Resource": ["arn:aws:s3:::aws-sam-cli-managed-default*","arn:aws:s3:::aws-sam-cli-managed-default*/*"] },
    { "Sid": "LambdaExecutionRole", "Effect": "Allow",
      "Action": ["iam:CreateRole","iam:DeleteRole","iam:GetRole","iam:PassRole","iam:PutRolePolicy",
        "iam:DeleteRolePolicy","iam:GetRolePolicy","iam:AttachRolePolicy","iam:DetachRolePolicy",
        "iam:ListRolePolicies","iam:ListAttachedRolePolicies","iam:TagRole","iam:UntagRole"],
      "Resource": "arn:aws:iam::$ACCOUNT:role/$SERVICE-*" },
    { "Sid": "LambdaFunctions", "Effect": "Allow",
      "Action": ["lambda:CreateFunction","lambda:DeleteFunction","lambda:GetFunction","lambda:GetFunctionConfiguration",
        "lambda:UpdateFunctionCode","lambda:UpdateFunctionConfiguration","lambda:AddPermission","lambda:RemovePermission",
        "lambda:GetPolicy","lambda:ListTags","lambda:TagResource","lambda:UntagResource","lambda:PublishVersion"],
      "Resource": "arn:aws:lambda:$REGION:$ACCOUNT:function:$SERVICE-*" },
    { "Sid": "ApiGateway", "Effect": "Allow",
      "Action": ["apigateway:GET","apigateway:POST","apigateway:PUT","apigateway:PATCH","apigateway:DELETE"],
      "Resource": "arn:aws:apigateway:$REGION::/*" },
    { "Sid": "Ec2DescribeForVpcLambda", "Effect": "Allow",
      "Action": ["ec2:DescribeSecurityGroups","ec2:DescribeSubnets","ec2:DescribeVpcs"], "Resource": "*" },
    { "Sid": "LambdaLogs", "Effect": "Allow",
      "Action": ["logs:CreateLogGroup","logs:DeleteLogGroup","logs:DescribeLogGroups","logs:PutRetentionPolicy","logs:TagResource"],
      "Resource": "arn:aws:logs:$REGION:$ACCOUNT:log-group:/aws/lambda/*$SERVICE*" }
  ]
}
EOF

# ---- 3. Crear role + policy y adjuntar ----
echo ">> Creando role $ROLE ..."
aws iam create-role --role-name "$ROLE" --assume-role-policy-document file://trust.json

echo ">> Creando y adjuntando policy $ROLE ..."
aws iam create-policy --policy-name "$ROLE" --policy-document file://deploy-policy.json
aws iam attach-role-policy --role-name "$ROLE" \
  --policy-arn "arn:aws:iam::$ACCOUNT:policy/$ROLE"

echo
echo "==================================================================="
echo " LISTO. Secret para GitHub (repo $REPO):"
echo "   AWS_DEPLOY_ROLE_ARN = arn:aws:iam::$ACCOUNT:role/$ROLE"
echo "==================================================================="
