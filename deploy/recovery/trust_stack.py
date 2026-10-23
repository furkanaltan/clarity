"""Render the two-authority CREATE-only CloudFormation template, without AWS calls."""

import argparse
import hashlib
import json
from pathlib import Path


def ref(name):
    return {"Ref": name}


def sub(value):
    return {"Fn::Sub": value}


def template():
    source = (Path(__file__).resolve().parents[2] / "rove_recovery_trust.py").read_text()
    release = hashlib.sha256(source.encode()).hexdigest()
    # The Lambda package contains shared schemas and handlers, not the local adapters.
    marker = "\nclass RecoveryTrustClient:\n"
    if source.count(marker) != 1:
        raise RuntimeError("handler_boundary_invalid")
    code = source.split(marker, 1)[0]
    compile(code, "index.py", "exec")
    parameters = {
        "EnvironmentId": {"Type": "String", "AllowedPattern": "[a-z][a-z0-9-]{0,47}"},
        "ReceiptBucketName": {"Type": "String", "AllowedPattern": "[a-z0-9][a-z0-9-]{1,61}[a-z0-9]"},
        "RecoveryBucketName": {"Type": "String", "AllowedPattern": "[a-z0-9][a-z0-9-]{1,61}[a-z0-9]"},
        "PolicyRevisionSha256": {"Type": "String", "AllowedPattern": "[0-9a-f]{64}"},
        "ApprovedPolicyJson": {"Type": "String", "MaxLength": 2400},
        "PublicFingerprint": {"Type": "String", "AllowedPattern": "[0-9A-F]{40}"},
        "UploaderUserName": {"Type": "String", "AllowedValues": ["rove-recovery-uploader"]},
    }
    bucket = {"Type": "AWS::S3::Bucket", "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain",
              "Properties": {
                  "BucketName": ref("ReceiptBucketName"), "ObjectLockEnabled": True,
                  "VersioningConfiguration": {"Status": "Enabled"},
                  "ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled", "Rule": {
                      "DefaultRetention": {"Mode": "COMPLIANCE", "Days": 365}}},
                  "PublicAccessBlockConfiguration": {key: True for key in (
                      "BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")},
                  "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
                  "BucketEncryption": {"ServerSideEncryptionConfiguration": [{
                      "ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}}}
    receipt_objects = [sub("arn:aws:s3:::${ReceiptBucketName}/receipts/${EnvironmentId}/*"),
                       sub("arn:aws:s3:::${ReceiptBucketName}/policies/*")]
    resources = {"ReceiptBucket": bucket}
    for name, kind, actions, objects in (
        ("Receipt", "receipt", ["s3:PutObject", "s3:GetObject", "s3:GetObjectVersion", "s3:GetObjectRetention"], receipt_objects),
        ("Verifier", "verifier", ["s3:GetObjectVersion", "s3:GetObjectRetention"],
         [sub("arn:aws:s3:::${RecoveryBucketName}/recovery/recovery_set_*.tar.gz.gpg")]),
    ):
        log_name = sub("/aws/lambda/${AWS::StackName}-" + kind)
        resources[name + "Log"] = {"Type": "AWS::Logs::LogGroup", "Properties": {
            "LogGroupName": log_name, "RetentionInDays": 14}}
        resources[name + "Role"] = {"Type": "AWS::IAM::Role", "Properties": {
            "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]},
            "Policies": [{"PolicyName": "bounded-runtime", "PolicyDocument": {
                "Version": "2012-10-17", "Statement": [
                    {"Effect": "Allow", "Action": actions, "Resource": objects},
                    {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                     "Resource": sub("arn:aws:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/lambda/${AWS::StackName}-" + kind + ":*")}]
            }}]}}
        variables = {"TRUST_ACCOUNT": ref("AWS::AccountId"), "TRUST_ENVIRONMENT": ref("EnvironmentId"),
                     "TRUST_POLICY_REVISION": ref("PolicyRevisionSha256"), "TRUST_POLICY_JSON": ref("ApprovedPolicyJson"),
                     "TRUST_RECEIPT_BUCKET": ref("ReceiptBucketName"), "TRUST_RECOVERY_BUCKET": ref("RecoveryBucketName"),
                     "TRUST_PUBLIC_FINGERPRINT": ref("PublicFingerprint"), "TRUST_RELEASE_SHA256": release,
                     "TRUST_KIND": kind}
        resources[name + "Function"] = {"Type": "AWS::Lambda::Function",
            "DependsOn": [name + "Log", "ReceiptBucketPolicy"], "Properties": {
                "FunctionName": sub("${AWS::StackName}-" + kind), "Runtime": "python3.12",
                "Handler": "index." + ("receipt_handler" if name == "Receipt" else "verifier_handler"),
                "Role": {"Fn::GetAtt": [name + "Role", "Arn"]}, "Timeout": 20,
                "MemorySize": 128, "ReservedConcurrentExecutions": 2,
                "Environment": {"Variables": variables}, "Code": {"ZipFile": code}}}
        resources[name + "Version"] = {"Type": "AWS::Lambda::Version", "DeletionPolicy": "Retain",
            "UpdateReplacePolicy": "Retain", "Properties": {"FunctionName": ref(name + "Function"),
            "Description": sub("policy-${PolicyRevisionSha256}-release-" + release)}}
    resources["ReceiptBucketPolicy"] = {"Type": "AWS::S3::BucketPolicy", "Properties": {
        "Bucket": ref("ReceiptBucket"), "PolicyDocument": {"Version": "2012-10-17", "Statement": [
            {"Sid": "TlsOnly", "Effect": "Deny", "Principal": "*", "Action": "s3:*",
             "Resource": [sub("arn:aws:s3:::${ReceiptBucketName}"), sub("arn:aws:s3:::${ReceiptBucketName}/*")],
             "Condition": {"Bool": {"aws:SecureTransport": "false"}}},
            {"Sid": "OnlyReceiptRoleWrites", "Effect": "Deny", "Principal": "*", "Action": "s3:PutObject",
             "Resource": sub("arn:aws:s3:::${ReceiptBucketName}/*"),
             "Condition": {"ArnNotEquals": {"aws:PrincipalArn": {"Fn::GetAtt": ["ReceiptRole", "Arn"]}}}},
            {"Sid": "ConditionalCreateOnly", "Effect": "Deny", "Principal": "*", "Action": "s3:PutObject",
             "Resource": sub("arn:aws:s3:::${ReceiptBucketName}/*"),
             "Condition": {"StringNotEquals": {"s3:if-none-match": "*"}}},
            {"Sid": "NoDeletionOrRetentionOverride", "Effect": "Deny", "Principal": "*",
             "Action": ["s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutObjectRetention", "s3:PutObjectLegalHold"],
             "Resource": sub("arn:aws:s3:::${ReceiptBucketName}/*")}
        ]}}}
    resources["UploaderInvokeOnly"] = {"Type": "AWS::IAM::Policy", "Properties": {
        "PolicyName": sub("${AWS::StackName}-published-invoke-only"), "Users": [ref("UploaderUserName")],
        "PolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
            "Action": "lambda:InvokeFunction", "Resource": [ref("ReceiptVersion"), ref("VerifierVersion")]}]}}}
    result = {"AWSTemplateFormatVersion": "2010-09-09",
              "Description": "Reviewed recovery trust authorities. CREATE only; no Production deployment or timers.",
              "Parameters": parameters, "Resources": resources, "Outputs": {
                  "ReceiptVersionArn": {"Value": ref("ReceiptVersion")},
                  "VerifierVersionArn": {"Value": ref("VerifierVersion")},
                  "ReceiptRoleArn": {"Value": {"Fn::GetAtt": ["ReceiptRole", "Arn"]}},
                  "VerifierRoleArn": {"Value": {"Fn::GetAtt": ["VerifierRole", "Arn"]}},
                  "ReceiptBucketName": {"Value": ref("ReceiptBucket")},
                  "ReleaseSha256": {"Value": release}}}
    return result


def main():
    parser = argparse.ArgumentParser(description="Render only; never deploys or contacts AWS")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raw = (json.dumps(template(), separators=(",", ":"), sort_keys=True) + "\n").encode()
    if len(raw) > 51200:
        raise SystemExit("STOP=template_body_limit_exceeded")
    with args.output.open("xb") as handle:
        handle.write(raw)
    print("TEMPLATE_SHA256=" + hashlib.sha256(raw).hexdigest())
    print("TEMPLATE_BYTES=" + str(len(raw)) + "; AWS_CALLS=NONE")


if __name__ == "__main__":
    main()
