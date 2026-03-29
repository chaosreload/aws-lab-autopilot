#!/usr/bin/env python3
import aws_cdk as cdk
from stack import AutopilotStack

app = cdk.App()

article_repo = app.node.try_get_context("article_repo") or "chaosreload/aws-hands-on-lab"

AutopilotStack(app, "aws-lab-autopilot-dev", env=cdk.Environment(
    region="us-east-1",
), article_repo=article_repo)

app.synth()
