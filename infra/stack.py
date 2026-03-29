import json
import os

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
    aws_sqs as sqs,
    aws_sns as sns,
    aws_lambda as _lambda,
    aws_lambda_event_sources as lambda_events,
    aws_stepfunctions as sfn,
    aws_apigatewayv2 as apigwv2,
    aws_iam as iam,
)
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration
from constructs import Construct


import jsii

@jsii.implements(cdk.ILocalBundling)
class _LocalBundler:
    def __init__(self, source_path: str):
        self._source_path = source_path

    def try_bundle(self, output_dir: str, *, image=None, entrypoint=None,
                   command=None, volumes=None, environment=None,
                   working_directory=None, user=None, security_opt=None,
                   network=None, bundling_file_access=None, **kwargs) -> bool:
        import subprocess, shutil
        subprocess.check_call([
            "pip", "install", "-r",
            os.path.join(self._source_path, "requirements.txt"),
            "-t", output_dir, "-q",
            "--platform", "manylinux2014_x86_64",
            "--python-version", "3.12",
            "--only-binary=:all:",
        ])
        src_dir = os.path.join(self._source_path, "src")
        shutil.copytree(src_dir, os.path.join(output_dir, "src"), dirs_exist_ok=True)
        return True


class AutopilotStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ============================================================
        # DynamoDB Tables
        # ============================================================
        tasks_table = dynamodb.Table(
            self, "TasksTable",
            table_name="handson-tasks",
            partition_key=dynamodb.Attribute(name="task_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        tasks_table.add_global_secondary_index(
            index_name="state-index",
            partition_key=dynamodb.Attribute(name="state", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="updated_at", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )
        tasks_table.add_global_secondary_index(
            index_name="date-index",
            partition_key=dynamodb.Attribute(name="created_date", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="created_at", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        resources_table = dynamodb.Table(
            self, "ResourcesTable",
            table_name="handson-resources",
            partition_key=dynamodb.Attribute(name="task_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="resource_arn", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ============================================================
        # S3 Bucket
        # ============================================================
        workflow_bucket = s3.Bucket(
            self, "WorkflowBucket",
            bucket_name=f"handson-workflow-{cdk.Aws.ACCOUNT_ID}",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ============================================================
        # SQS Queues (each with DLQ)
        # ============================================================
        def make_queue(name: str) -> sqs.Queue:
            dlq = sqs.Queue(
                self, f"{name}DLQ",
                queue_name=f"handson-{name}-queue-dlq",
                retention_period=Duration.days(14),
            )
            return sqs.Queue(
                self, f"{name}Queue",
                queue_name=f"handson-{name}-queue",
                visibility_timeout=Duration.seconds(960),
                retention_period=Duration.days(1),
                dead_letter_queue=sqs.DeadLetterQueue(
                    max_receive_count=3,
                    queue=dlq,
                ),
            )

        research_queue = make_queue("research")
        execute_queue = make_queue("execute")
        publish_queue = make_queue("publish")

        # ============================================================
        # SNS Topics
        # ============================================================
        notifications_topic = sns.Topic(
            self, "NotificationsTopic",
            topic_name="handson-workflow-notifications",
        )
        alerts_topic = sns.Topic(
            self, "AlertsTopic",
            topic_name="handson-workflow-alerts",
        )

        # ============================================================
        # Lambda Functions
        # ============================================================
        code_path = os.path.join(os.path.dirname(__file__), "..")
        code_excludes = [
            "infra", ".aws-sam", "cdk.out", ".git", "__pycache__",
            "tests", "node_modules", ".pytest_cache",
        ]

        bundled_code = _lambda.Code.from_asset(
            code_path,
            exclude=code_excludes,
            asset_hash_type=cdk.AssetHashType.OUTPUT,
            bundling=cdk.BundlingOptions(
                image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                command=[
                    "bash", "-c",
                    "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output",
                ],
                local=_LocalBundler(code_path),
            ),
        )

        common_env = {
            "TASKS_TABLE": tasks_table.table_name,
            "RESOURCES_TABLE": resources_table.table_name,
            "S3_BUCKET": workflow_bucket.bucket_name,
        }

        api_handler = _lambda.Function(
            self, "ApiHandler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.api.handler.handler",
            code=bundled_code,
            timeout=Duration.seconds(900),
            memory_size=256,
            environment={**common_env},
        )

        sqs_handler = _lambda.Function(
            self, "SqsHandler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.orchestrator.sqs_handler.handler",
            code=bundled_code,
            timeout=Duration.seconds(900),
            memory_size=512,
            environment={
                **common_env,
                "GITHUB_SECRET_NAME": "aws-lab-autopilot/github",
            },
        )

        increment_rework = _lambda.Function(
            self, "IncrementRework",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.orchestrator.increment_rework.handler",
            code=bundled_code,
            timeout=Duration.seconds(30),
            memory_size=128,
            environment={
                "TASKS_TABLE": tasks_table.table_name,
            },
        )

        # ============================================================
        # IAM Permissions
        # ============================================================
        # ApiHandler
        tasks_table.grant_read_write_data(api_handler)
        resources_table.grant_read_write_data(api_handler)
        workflow_bucket.grant_read_write(api_handler)

        # SqsHandler
        tasks_table.grant_read_write_data(sqs_handler)
        resources_table.grant_read_write_data(sqs_handler)
        workflow_bucket.grant_read_write(sqs_handler)

        # SqsHandler needs Secrets Manager for GitHub config
        github_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "GithubSecret", "aws-lab-autopilot/github"
        )
        github_secret.grant_read(sqs_handler)

        # SqsHandler needs Bedrock permissions for agent model calls
        sqs_handler.add_to_role_policy(iam.PolicyStatement(
            actions=[
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
            ],
            resources=[
                f"arn:aws:bedrock:us-east-1:{cdk.Aws.ACCOUNT_ID}:inference-profile/us.anthropic.*",
                f"arn:aws:bedrock:*::foundation-model/anthropic.*",
            ],
        ))

        # SqsHandler needs IAM permissions for Execute Agent scoped roles
        sqs_handler.add_to_role_policy(iam.PolicyStatement(
            actions=[
                "iam:CreateRole", "iam:DeleteRole", "iam:GetRole",
                "iam:PutRolePolicy", "iam:DeleteRolePolicy",
                "iam:TagRole", "iam:ListRolePolicies",
            ],
            resources=[f"arn:aws:iam::{cdk.Aws.ACCOUNT_ID}:role/handson-lab-*"],
        ))

        # IncrementRework
        tasks_table.grant_read_write_data(increment_rework)

        # ============================================================
        # Step Functions State Machine
        # ============================================================
        sfn_def_path = os.path.join(os.path.dirname(__file__), "..", "src", "orchestrator", "sfn_definition.json")
        with open(sfn_def_path) as f:
            sfn_def_str = f.read()

        # Perform substitutions
        sfn_def_str = sfn_def_str.replace("${ResearchQueueUrl}", research_queue.queue_url)
        sfn_def_str = sfn_def_str.replace("${ExecuteQueueUrl}", execute_queue.queue_url)
        sfn_def_str = sfn_def_str.replace("${PublishQueueUrl}", publish_queue.queue_url)
        sfn_def_str = sfn_def_str.replace("${IncrementReworkFn}", increment_rework.function_arn)
        sfn_def_str = sfn_def_str.replace("${NotifyTopic}", notifications_topic.topic_arn)
        sfn_def_str = sfn_def_str.replace("${AlertTopic}", alerts_topic.topic_arn)

        state_machine = sfn.StateMachine(
            self, "WorkflowStateMachine",
            state_machine_name="handson-workflow",
            definition_body=sfn.DefinitionBody.from_string(sfn_def_str),
            timeout=Duration.hours(24),
        )

        # State machine permissions
        research_queue.grant_send_messages(state_machine)
        execute_queue.grant_send_messages(state_machine)
        publish_queue.grant_send_messages(state_machine)
        increment_rework.grant_invoke(state_machine)
        notifications_topic.grant_publish(state_machine)
        alerts_topic.grant_publish(state_machine)

        # ApiHandler needs to start executions
        api_handler.add_environment("STATE_MACHINE_ARN", state_machine.state_machine_arn)
        state_machine.grant_start_execution(api_handler)

        # SqsHandler needs SendTaskSuccess/SendTaskFailure
        sqs_handler.add_environment("STATE_MACHINE_ARN", state_machine.state_machine_arn)
        sqs_handler.add_to_role_policy(iam.PolicyStatement(
            actions=["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources=[state_machine.state_machine_arn],
        ))

        # ============================================================
        # SQS → Lambda Event Sources
        # ============================================================
        sqs_handler.add_event_source(lambda_events.SqsEventSource(research_queue, batch_size=1))
        sqs_handler.add_event_source(lambda_events.SqsEventSource(execute_queue, batch_size=1))
        sqs_handler.add_event_source(lambda_events.SqsEventSource(publish_queue, batch_size=1))

        # ============================================================
        # API Gateway HTTP API
        # ============================================================
        http_api = apigwv2.HttpApi(
            self, "HttpApi",
            api_name="autopilot-api",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=["*"],
                allow_methods=[apigwv2.CorsHttpMethod.GET, apigwv2.CorsHttpMethod.POST, apigwv2.CorsHttpMethod.DELETE],
                allow_headers=["Content-Type"],
            ),
        )

        integration = HttpLambdaIntegration("ApiIntegration", api_handler)

        http_api.add_routes(
            path="/{proxy+}",
            methods=[apigwv2.HttpMethod.ANY],
            integration=integration,
        )

        # ============================================================
        # Outputs
        # ============================================================
        cdk.CfnOutput(self, "ApiUrl", value=http_api.api_endpoint, description="HTTP API endpoint URL")
        cdk.CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)
        cdk.CfnOutput(self, "TasksTableName", value=tasks_table.table_name)
        cdk.CfnOutput(self, "WorkflowBucketName", value=workflow_bucket.bucket_name)
