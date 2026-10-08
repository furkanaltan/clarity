"""Read-only IaC least-privilege checks; no AWS calls."""

import importlib.util
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import rove_recovery_trust as trust
from test_recovery_trust import ClientError, FakeLambda, FakeS3, configuration

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("trust_stack", ROOT / "deploy/recovery/trust_stack.py")
stack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stack)
plan_spec = importlib.util.spec_from_file_location("trust_plan", ROOT / "deploy/recovery/trust_plan.py")
plan = importlib.util.module_from_spec(plan_spec)
plan_spec.loader.exec_module(plan)


class TrustInfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.template = stack.template()
        self.resources = self.template["Resources"]

    def statements(self, role):
        return self.resources[role]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]

    def test_private_versioned_compliance_bucket_with_no_lifecycle(self):
        bucket = self.resources["ReceiptBucket"]
        self.assertEqual(bucket["DeletionPolicy"], "Retain")
        self.assertEqual(bucket["UpdateReplacePolicy"], "Retain")
        properties = bucket["Properties"]
        self.assertTrue(properties["ObjectLockEnabled"])
        self.assertEqual(properties["VersioningConfiguration"], {"Status": "Enabled"})
        self.assertEqual(properties["ObjectLockConfiguration"]["Rule"]["DefaultRetention"], {"Mode": "COMPLIANCE", "Days": 365})
        self.assertTrue(all(properties["PublicAccessBlockConfiguration"].values()))
        self.assertNotIn("LifecycleConfiguration", properties)

    def test_verifier_can_only_read_versions_retention_and_its_logs(self):
        statements = self.statements("VerifierRole")
        s3 = statements[0]
        self.assertEqual(set(s3["Action"]), {"s3:GetObjectVersion", "s3:GetObjectRetention"})
        self.assertEqual(s3["Resource"], [stack.sub("arn:aws:s3:::${RecoveryBucketName}/recovery/recovery_set_*.tar.gz.gpg")])
        self.assertEqual(statements[1]["Action"], ["logs:CreateLogStream", "logs:PutLogEvents"])

    def test_authority_has_no_delete_iam_bucket_or_retention_write(self):
        statements = self.statements("ReceiptRole")
        self.assertEqual(set(statements[0]["Action"]), {"s3:PutObject", "s3:GetObject", "s3:GetObjectVersion", "s3:GetObjectRetention"})
        self.assertTrue(all("ReceiptBucketName" in json.dumps(item) for item in statements[0]["Resource"]))
        self.assertNotIn("ManagedPolicyArns", self.resources["ReceiptRole"]["Properties"])

    def test_uploader_gets_only_two_exact_published_invocations(self):
        policy = self.resources["UploaderInvokeOnly"]["Properties"]
        self.assertEqual(policy["Users"], [stack.ref("UploaderUserName")])
        self.assertEqual(policy["PolicyDocument"]["Statement"], [{"Effect": "Allow", "Action": "lambda:InvokeFunction",
                                                               "Resource": [stack.ref("ReceiptVersion"), stack.ref("VerifierVersion")]}])
        self.assertEqual(self.template["Parameters"]["UploaderUserName"]["AllowedValues"], ["rove-recovery-uploader"])

    def test_bucket_denies_foreign_put_overwrite_delete_and_retention_override(self):
        statements = self.resources["ReceiptBucketPolicy"]["Properties"]["PolicyDocument"]["Statement"]
        by_name = {item["Sid"]: item for item in statements}
        self.assertTrue(all(item["Effect"] == "Deny" for item in statements))
        self.assertEqual(by_name["ConditionalCreateOnly"]["Condition"], {"StringNotEquals": {"s3:if-none-match": "*"}})
        self.assertEqual(by_name["OnlyReceiptRoleWrites"]["Condition"], {"ArnNotEquals": {"aws:PrincipalArn": {"Fn::GetAtt": ["ReceiptRole", "Arn"]}}})
        self.assertEqual(set(by_name["NoDeletionOrRetentionOverride"]["Action"]), {
            "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutObjectRetention", "s3:PutObjectLegalHold"})

    def test_no_reader_user_keys_scheduler_public_urls_or_existing_bucket_resource(self):
        types = {item["Type"] for item in self.resources.values()}
        self.assertEqual(types, {"AWS::S3::Bucket", "AWS::S3::BucketPolicy", "AWS::IAM::Role", "AWS::IAM::Policy",
                                "AWS::Logs::LogGroup", "AWS::Lambda::Function", "AWS::Lambda::Version"})
        self.assertEqual(sum(item["Type"] == "AWS::S3::Bucket" for item in self.resources.values()), 1)
        self.assertEqual(sum(item["Type"] == "AWS::Lambda::Function" for item in self.resources.values()), 2)
        self.assertEqual(sum(item["Type"] == "AWS::IAM::Role" for item in self.resources.values()), 2)

    def test_reproducible_source_bound_versions_and_template_body_size(self):
        self.assertEqual(self.template, stack.template())
        raw = json.dumps(self.template, separators=(",", ":"), sort_keys=True).encode()
        self.assertLessEqual(len(raw), 51200)
        for name in ("Receipt", "Verifier"):
            properties = self.resources[name + "Function"]["Properties"]
            compile(properties["Code"]["ZipFile"], "index.py", "exec")
            self.assertNotIn("class RecoveryTrustClient:", properties["Code"]["ZipFile"])
            self.assertEqual(properties["Runtime"], "python3.12")
            self.assertEqual(properties["Timeout"], 20)
            self.assertEqual(self.resources[name + "Version"]["DeletionPolicy"], "Retain")
            description = self.resources[name + "Version"]["Properties"]["Description"]
            self.assertIn("${PolicyRevisionSha256}", description["Fn::Sub"])

    def rendered_handler(self, name, request, pins, policy, s3, **overrides):
        module = ModuleType("synthetic_lambda_package")
        code = self.resources[name + "Function"]["Properties"]["Code"]["ZipFile"]
        arn = pins.receipt_arn if name == "Receipt" else pins.verifier_arn
        context = SimpleNamespace(invoked_function_arn=arn, function_version=arn.rsplit(":", 1)[1])
        environment = dict(TRUST_ACCOUNT=pins.account_id, TRUST_ENVIRONMENT=pins.environment_id,
            TRUST_POLICY_REVISION=pins.policy_revision, TRUST_POLICY_JSON=trust.json_bytes(policy).decode(),
            TRUST_RECEIPT_BUCKET=pins.receipt_bucket, TRUST_RECOVERY_BUCKET=pins.recovery_bucket,
            TRUST_PUBLIC_FINGERPRINT=pins.public_key_fingerprint, TRUST_RELEASE_SHA256=pins.release_sha256,
            TRUST_KIND="receipt" if name == "Receipt" else "verifier",
            AWS_LAMBDA_FUNCTION_NAME=arn.split(":")[-2])
        environment.update(overrides)
        with patch.dict(sys.modules, {module.__name__: module}), patch.dict(os.environ, environment, clear=True):
            exec(compile(code, "index.py", "exec"), module.__dict__)
            with patch.object(module, "sdk_client", return_value=s3):
                handler = module.receipt_handler if name == "Receipt" else module.verifier_handler
                return handler(request, context)

    def test_actual_rendered_receipt_handler_has_bound_context_and_exact_duplicate(self):
        pins, policy = configuration()
        s3 = FakeS3(pins)
        client = trust.RecoveryTrustClient(pins, policy, client=FakeLambda(pins, policy, s3))
        generation = "recovery_set_" + (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ") + "_" + "2" * 32
        request = client.request(generation, "e" * 64, "manifest")
        first = self.rendered_handler("Receipt", request, pins, policy, s3)
        self.assertEqual(first["status"], "PASS")
        self.assertEqual(first, self.rendered_handler("Receipt", request, pins, policy, s3))
        for changes in ({"TRUST_ACCOUNT": "222222222222"}, {"TRUST_KIND": "verifier"},
                        {"AWS_LAMBDA_FUNCTION_NAME": "other"}, {"TRUST_POLICY_REVISION": "0" * 64},
                        {"TRUST_RECEIPT_BUCKET": "../invalid"}, {"TRUST_PUBLIC_FINGERPRINT": "bad"}):
            with self.subTest(changes=changes):
                result = self.rendered_handler("Receipt", request, pins, policy, s3, **changes)
                self.assertEqual(result["status"], "BLOCK")
        s3.denied = True
        result = self.rendered_handler("Receipt", request, pins, policy, s3)
        self.assertEqual(result, {"response_version": 1, "status": "BLOCK", "code": "authority_dependency_failed"})

    def test_actual_rendered_metadata_handler_never_gets_body_or_uses_latest(self):
        pins, policy = configuration()
        s3 = FakeS3(pins)
        modified = datetime.now(timezone.utc) - timedelta(hours=1)
        s3.head = dict(VersionId="exact-version", ContentLength=32, LastModified=modified,
                      ObjectLockMode="COMPLIANCE", ObjectLockRetainUntilDate=modified + timedelta(days=30))
        s3.retention = dict(Mode="COMPLIANCE", RetainUntilDate=modified + timedelta(days=30))
        generation = "recovery_set_" + modified.strftime("%Y%m%dT%H%M%SZ") + "_" + "2" * 32
        client = trust.RecoveryTrustClient(pins, policy, client=FakeLambda(pins, policy, s3))
        request = dict(client.request(generation, "e" * 64, "metadata"),
                       object_key=f"recovery/{generation}.tar.gz.gpg", version_id="exact-version", size=32, retention_days=30)
        result = self.rendered_handler("Verifier", request, pins, policy, s3)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual([call for call, _ in s3.calls], ["head", "retention"])
        self.assertTrue(all(args["VersionId"] == "exact-version" for _, args in s3.calls))
        result = self.rendered_handler("Verifier", dict(request, version_id="latest"), pins, policy, s3)
        self.assertEqual(result["status"], "BLOCK")


class TrustReadOnlyPlanTests(unittest.TestCase):
    def setUp(self):
        self.template = stack.template()
        self.pins, self.policy = configuration()
        self.parameters = dict(EnvironmentId=self.pins.environment_id, ReceiptBucketName=self.pins.receipt_bucket,
            RecoveryBucketName=self.pins.recovery_bucket, PolicyRevisionSha256=self.pins.policy_revision,
            ApprovedPolicyJson=trust.json_bytes(self.policy).decode(), PublicFingerprint=self.pins.public_key_fingerprint,
            UploaderUserName="rove-recovery-uploader")
        self.entries = [{"ParameterKey": key, "ParameterValue": value} for key, value in sorted(self.parameters.items())]
        self.calls = []
        self.admin = f"arn:aws:sts::{self.pins.account_id}:assumed-role/synthetic-admin/session"
        self.resource = f"arn:aws:s3:::{self.pins.recovery_bucket}/recovery/*"
        self.responses = {
            "get_caller_identity": {"Account": self.pins.account_id, "Arn": self.admin},
            "get_account_summary": {"SummaryMap": {"AccountAccessKeysPresent": 0}},
            "get_bucket_location": {"LocationConstraint": "eu-central-1"},
            "get_bucket_versioning": {"Status": "Enabled", "ResponseMetadata": {"RequestId": "random"}},
            "get_object_lock_configuration": {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled",
                "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 30}}}},
            "get_bucket_policy": {"Policy": json.dumps({"Statement": [{"Effect": "Deny", "Action": "s3:PutObject",
                "Resource": self.resource, "Condition": {"StringNotEquals": {"s3:if-none-match": "*"}}}]})},
            "get_user": {"User": {"UserName": "rove-recovery-uploader",
                "Arn": f"arn:aws:iam::{self.pins.account_id}:user/rove-recovery-uploader"}},
            "list_attached_user_policies": {"AttachedPolicies": [], "IsTruncated": False},
            "list_groups_for_user": {"Groups": [], "IsTruncated": False},
            "list_user_policies": {"PolicyNames": ["synthetic-put-only"], "IsTruncated": False},
            "get_user_policy": {"PolicyDocument": {"Statement": [{"Effect": "Allow", "Action": "s3:PutObject", "Resource": self.resource}]}},
            "list_access_keys": {"AccessKeyMetadata": [], "IsTruncated": False},
            "validate_template": {"Capabilities": ["CAPABILITY_IAM"]},
        }
        def call(name, **kwargs):
            self.calls.append((name, kwargs))
            if name == "describe_stacks":
                error = ClientError("ValidationError")
                error.response["Error"]["Message"] = "Stack with id synthetic-trust does not exist"
                raise error
            if name == "head_bucket":
                raise ClientError("404")
            return self.responses[name]
        methods = set(self.responses) | {"describe_stacks", "head_bucket"}
        self.client = SimpleNamespace(**{name: (lambda name=name, **kwargs: call(name, **kwargs)) for name in methods})
        self.clients = dict.fromkeys(("sts", "iam", "s3", "cloudformation"), self.client)

    def run_plan(self):
        return plan.read_only_plan(self.template, self.entries, self.pins.account_id, "synthetic-trust", self.clients)

    def test_read_only_plan_records_stable_hashes_without_creating_anything(self):
        first = self.run_plan()
        self.responses["get_bucket_versioning"]["ResponseMetadata"]["RequestId"] = "different"
        second = self.run_plan()
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "PASS")
        self.assertEqual(first["mutations"], "NONE")
        self.assertEqual(first["active_reader_keys"], 0)
        self.assertFalse(any(name.startswith(("create", "put", "delete", "update", "invoke")) for name, _ in self.calls))

    def test_unknown_parameter_duplicate_or_policy_revision_blocks_before_aws(self):
        original = list(self.entries)
        for entries in (original + [original[0]], original + [{"ParameterKey": "Secret", "ParameterValue": "synthetic"}],
                        [dict(item, ParameterValue="0" * 64) if item["ParameterKey"] == "PolicyRevisionSha256" else item for item in original]):
            self.entries = entries
            with self.subTest(entries=entries), self.assertRaises(trust.TrustError):
                self.run_plan()
        self.assertFalse(self.calls)

    def test_existing_stack_or_bucket_never_leads_to_update(self):
        for name in ("describe_stacks", "head_bucket"):
            with patch.object(self.client, name, return_value={}), self.assertRaises(trust.TrustError):
                self.run_plan()

    def test_wrong_account_region_lock_and_versioning_drift_block(self):
        for name, value in (("get_caller_identity", {"Account": "222222222222", "Arn": self.admin}),
                            ("get_bucket_location", {"LocationConstraint": "us-east-1"}),
                            ("get_bucket_versioning", {"Status": "Suspended"}),
                            ("get_object_lock_configuration", {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled",
                             "Rule": {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 30}}}})):
            with self.subTest(name=name), patch.dict(self.responses, {name: value}), self.assertRaises(trust.TrustError):
                self.run_plan()

    def test_uploader_boundary_managed_groups_pagination_or_read_rights_block(self):
        for name, value in (("get_user", {"User": dict(self.responses["get_user"]["User"], PermissionsBoundary={})}),
                            ("list_attached_user_policies", {"AttachedPolicies": [{}]}),
                            ("list_groups_for_user", {"Groups": [{}]}),
                            ("list_user_policies", {"PolicyNames": [], "IsTruncated": True}),
                            ("get_user_policy", {"PolicyDocument": {"Statement": [{"Effect": "Allow", "Action": "s3:GetObject", "Resource": self.resource}]}})):
            with self.subTest(name=name), patch.dict(self.responses, {name: value}), self.assertRaises(trust.TrustError):
                self.run_plan()

    def test_reader_active_key_prevents_release_plan(self):
        with patch.dict(self.responses, list_access_keys={"AccessKeyMetadata": [{"Status": "Active"}]}), self.assertRaises(trust.TrustError):
            self.run_plan()

    def test_existing_root_console_session_requires_proven_absence_of_permanent_keys(self):
        root = f"arn:aws:iam::{self.pins.account_id}:root"
        with patch.dict(self.responses, get_caller_identity={"Account": self.pins.account_id, "Arn": root}):
            result = self.run_plan()
            self.assertEqual(result["human_admin_model"], "ROOT_TEMPORARY_SESSION_WITHOUT_ACCESS_KEYS")
            for count in (1, None, False):
                with self.subTest(count=count), patch.dict(self.responses,
                        get_account_summary={"SummaryMap": {"AccountAccessKeysPresent": count}}), self.assertRaises(trust.TrustError):
                    self.run_plan()

    def test_existing_iam_admin_requires_no_access_keys(self):
        human = dict(UserName="synthetic-human", Arn=f"arn:aws:iam::{self.pins.account_id}:user/synthetic-human")
        original = self.client.get_user
        def resolve(**kwargs):
            return original(**kwargs) if "UserName" in kwargs else {"User": human}
        with patch.dict(self.responses, get_caller_identity={"Account": self.pins.account_id, "Arn": human["Arn"]}), \
             patch.object(self.client, "get_user", side_effect=resolve):
            self.assertEqual(self.run_plan()["human_admin_model"], "IAM_USER_WITHOUT_ACCESS_KEYS")
            with patch.dict(self.responses, list_access_keys={"AccessKeyMetadata": [{"Status": "Inactive"}]}), self.assertRaises(trust.TrustError):
                self.run_plan()

    def test_stack_and_bucket_access_denied_are_not_absence(self):
        for name in ("describe_stacks", "head_bucket"):
            with patch.object(self.client, name, side_effect=ClientError("AccessDenied")), self.assertRaises(trust.TrustError):
                self.run_plan()


if __name__ == "__main__":
    unittest.main()
