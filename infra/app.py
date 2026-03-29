#!/usr/bin/env python3
import aws_cdk as cdk
from stack import AutopilotStack

app = cdk.App()

article_repo = app.node.try_get_context("article_repo") or "chaosreload/aws-hands-on-lab"

AutopilotStack(app, "aws-lab-autopilot", env=cdk.Environment(
    region="us-west-2",
), article_repo=article_repo)

app.synth()
