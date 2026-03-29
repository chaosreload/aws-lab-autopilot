#!/usr/bin/env python3
import aws_cdk as cdk
from stack import AutopilotStack

app = cdk.App()
AutopilotStack(app, "aws-lab-autopilot-dev", env=cdk.Environment(
    region="us-east-1",
))
app.synth()
