"""Operator-only, read-only CloudShell deployment plan. Never creates AWS resources."""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

# Supports both the canonical checkout and a flat, reviewed CloudShell bundle.
ROOT = Path(__file__).resolve().parents[2]
if (ROOT / "rove_recovery_trust.py").is_file():
    sys.path.insert(0, str(ROOT))
import rove_recovery_trust as trust


def parameters_from(template, entries):
    trust.require(isinstance(entries, list), "parameters_invalid")
    parameters = {}
    for entry in entries:
        trust.fields(entry, {"ParameterKey", "ParameterValue"})
        key, value = entry["ParameterKey"], entry["ParameterValue"]
        trust.require(isinstance(key, str) and key not in parameters and isinstance(value, str), "parameters_invalid")
        parameters[key] = value
    trust.fields(parameters, set(template["Parameters"]))
    for key, value in parameters.items():
        definition = template["Parameters"][key]
        trust.require(len(value) <= definition.get("MaxLength", 4096), "parameter_length_invalid")
        if "AllowedPattern" in definition:
            trust.require(re.fullmatch(definition["AllowedPattern"], value), "parameter_pattern_invalid")
        if "AllowedValues" in definition:
            trust.require(value in definition["AllowedValues"], "parameter_value_invalid")
    trust.require(parameters["ReceiptBucketName"] != parameters["RecoveryBucketName"], "separate_receipt_bucket_required")
    trust.validate_policy(trust.strict_json(parameters["ApprovedPolicyJson"]), parameters["PolicyRevisionSha256"])
    return parameters


def digest(value):
    return trust.sha(trust.json_bytes(value))


def read_only_plan(template, entries, account, stack_name, clients):
    trust.require(re.fullmatch(r"[0-9]{12}", account) and re.fullmatch(r"[a-z][a-z0-9-]{2,42}", stack_name), "context_invalid")
    parameters = parameters_from(template, entries)
    identity = clients["sts"].get_caller_identity()
    trust.require(identity["Account"] == account, "wrong_aws_account")
    iam, s3, cfn = (clients[name] for name in ("iam", "s3", "cloudformation"))
    arn = identity["Arn"]
    if arn == f"arn:aws:iam::{account}:root":
        keys = iam.get_account_summary()["SummaryMap"].get("AccountAccessKeysPresent")
        trust.require(type(keys) is int and keys == 0, "permanent_root_keys_present_or_unknown")
        admin_model = "ROOT_TEMPORARY_SESSION_WITHOUT_ACCESS_KEYS"
    elif arn.startswith(f"arn:aws:iam::{account}:user/"):
        user = iam.get_user()["User"]
        trust.require(user["Arn"] == arn, "human_identity_mismatch")
        trust.require(not iam.list_access_keys(UserName=user["UserName"])["AccessKeyMetadata"], "permanent_admin_keys_present")
        admin_model = "IAM_USER_WITHOUT_ACCESS_KEYS"
    else:
        trust.require(arn.startswith(f"arn:aws:sts::{account}:assumed-role/"), "human_identity_not_supported")
        admin_model = "ROLE_SESSION"
    try:
        cfn.describe_stacks(StackName=stack_name)
    except Exception as exc:
        trust.require(trust.aws_code(exc) == "ValidationError" and
                      getattr(exc, "response", {}).get("Error", {}).get("Message") ==
                      f"Stack with id {stack_name} does not exist", "stack_absence_not_proven")
    else:
        raise trust.TrustError("existing_stack_update_forbidden")
    try:
        s3.head_bucket(Bucket=parameters["ReceiptBucketName"], ExpectedBucketOwner=account)
    except Exception as exc:
        trust.require(trust.aws_code(exc) in {"404", "NoSuchBucket"}, "receipt_bucket_absence_not_proven")
    else:
        raise trust.TrustError("receipt_bucket_already_exists")
    args = dict(Bucket=parameters["RecoveryBucketName"], ExpectedBucketOwner=account)
    trust.require(s3.get_bucket_location(**args)["LocationConstraint"] == "eu-central-1", "wrong_recovery_bucket_region")
    versioning = {key: value for key, value in s3.get_bucket_versioning(**args).items() if key != "ResponseMetadata"}
    lock = s3.get_object_lock_configuration(**args)["ObjectLockConfiguration"]
    policy = trust.strict_json(s3.get_bucket_policy(**args)["Policy"])
    trust.require(versioning.get("Status") == "Enabled" and lock.get("ObjectLockEnabled") == "Enabled"
                  and lock.get("Rule", {}).get("DefaultRetention") == {"Mode": "COMPLIANCE", "Days": 30}, "production_config_drift")
    resource = f"arn:aws:s3:::{parameters['RecoveryBucketName']}/recovery/*"
    trust.require(any(item.get("Effect") == "Deny" and item.get("Action") in ("s3:PutObject", ["s3:PutObject"])
                      and item.get("Resource") in (resource, [resource])
                      and item.get("Condition") == {"StringNotEquals": {"s3:if-none-match": "*"}}
                      for item in policy.get("Statement", [])), "production_conditional_deny_not_proven")
    uploader = parameters["UploaderUserName"]
    user = iam.get_user(UserName=uploader)["User"]
    trust.require(user["Arn"] == f"arn:aws:iam::{account}:user/{uploader}"
                  and "PermissionsBoundary" not in user, "uploader_identity_or_boundary_drift")
    managed = iam.list_attached_user_policies(UserName=uploader)
    groups = iam.list_groups_for_user(UserName=uploader)
    names = iam.list_user_policies(UserName=uploader)
    trust.require(not managed["AttachedPolicies"] and not groups["Groups"]
                  and not any(item.get("IsTruncated") for item in (managed, groups, names)), "uploader_influences_or_pagination")
    policies = {}
    allowed = 0
    for name in sorted(names["PolicyNames"]):
        document = iam.get_user_policy(UserName=uploader, PolicyName=name)["PolicyDocument"]
        trust.require(isinstance(document, dict), "uploader_policy_invalid")
        policies[name] = document
        for item in document["Statement"]:
            if item["Effect"] == "Allow":
                trust.require(item["Action"] in ("s3:PutObject", ["s3:PutObject"])
                              and item["Resource"] in (resource, [resource]), "unexpected_uploader_permissions")
                allowed += 1
    trust.require(allowed > 0, "uploader_put_not_proven")
    reader = iam.list_access_keys(UserName="rove-recovery-reader")
    trust.require(not reader.get("IsTruncated") and not any(item["Status"] == "Active"
                  for item in reader["AccessKeyMetadata"]), "active_reader_keys_present")
    raw_template = json.dumps(template, sort_keys=True, separators=(",", ":")) + "\n"
    trust.require(len(raw_template.encode()) <= 51200, "template_body_limit_exceeded")
    validation = cfn.validate_template(TemplateBody=raw_template)
    capabilities = validation.get("Capabilities", [])
    trust.require(set(capabilities) <= {"CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"}, "unexpected_capability")
    return dict(plan_version=1, status="PASS", stack_name=stack_name, account_id=account, region="eu-central-1",
        template_sha256=trust.sha(raw_template.encode()), parameters_sha256=digest(entries),
        release_sha256=template["Outputs"]["ReleaseSha256"]["Value"], capabilities=capabilities,
        policy_revision=parameters["PolicyRevisionSha256"], receipt_retention_days=365,
        production_hashes=dict(bucket_policy=digest(policy), versioning=digest(versioning),
                               object_lock=digest(lock), uploader_inline_policies=digest(policies)),
        uploader_policy_hashes={name: digest(document) for name, document in policies.items()},
        human_admin_model=admin_model, active_reader_keys=0, mutations="NONE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--template-sha256", required=True)
    parser.add_argument("--parameters-sha256", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--stack-name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stage = "LOCAL_INPUTS"
    try:
        for path in (args.template, args.parameters):
            trust.require(path.is_file() and not path.is_symlink(), "input_file_invalid")
        trust.require(not args.output.exists() and not args.output.is_symlink(), "output_already_exists")
        template_raw, parameters_raw = args.template.read_bytes(), args.parameters.read_bytes()
        trust.require(trust.sha(template_raw) == args.template_sha256
                      and trust.sha(parameters_raw) == args.parameters_sha256, "independent_artifact_hash_mismatch")
        template = json.loads(template_raw)
        entries = trust.strict_json(parameters_raw)
        parameters_from(template, entries)
        release = hashlib.sha256(Path(trust.__file__).read_bytes()).hexdigest()
        trust.require(template["Outputs"]["ReleaseSha256"]["Value"] == release, "release_source_mismatch")
        import boto3
        from botocore.config import Config
        config = Config(connect_timeout=3, read_timeout=10, retries={"total_max_attempts": 1},
                        ignore_configured_endpoint_urls=True)
        session = boto3.Session()
        credentials = session.get_credentials().get_frozen_credentials()
        trust.require(bool(credentials.token), "temporary_admin_session_required")
        # IAM endpoint/signing is SDK-resolved; never override its global endpoint.
        clients = {name: session.client(name, config=config, **({} if name == "iam" else {"region_name": "eu-central-1"}))
                   for name in ("sts", "iam", "s3", "cloudformation")}
        stage = "READ_ONLY_AWS_PLAN"
        result = read_only_plan(template, entries, args.account_id, args.stack_name, clients)
        stage = "LOCAL_PLAN_OUTPUT"
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(trust.json_bytes(result))
        print("AWS_TRUST_READ_ONLY_PLAN=PASS")
        print("PLAN_SHA256=" + digest(result))
        print("RECEIPT_COMPLIANCE_DAYS=365; ACTIVE_READER_KEYS=0")
        print("RESOURCES_CREATED=NONE; KEYS_CREATED=NONE; MUTATIONS=NONE")
    except Exception as exc:
        code = trust.aws_code(exc) or (str(exc) if isinstance(exc, trust.TrustError) else type(exc).__name__)
        print("AWS_TRUST_READ_ONLY_PLAN=STOP; STAGE=" + stage + "; ERROR_CODE=" + code)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
