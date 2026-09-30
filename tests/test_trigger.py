#!/usr/bin/env python3

import json
from pathlib import Path
from unittest.mock import patch

import boto3
from moto import mock_aws
from moto.core import DEFAULT_ACCOUNT_ID

from src.handle_born_digital_trigger import (get_config,
                                             get_volume_configurations,
                                             lambda_handler)

TEST_CLUSTER_NAME = "default"
CONFIGS = {
    "AWS_REGION": "us-east-1",
    "ECS_CLUSTER": TEST_CLUSTER_NAME,
    "ECS_SUBNET": "subnet",
    "ECS_SECURITY_GROUP": "sg-123456789",
    "EXPANSION_RATIO": 4.0,
    "EBS_VOLUME_ROLE": "ebs-volume-role"
}


@mock_aws
@patch('src.handle_born_digital_trigger.get_config')
def test_guardduty_args(mock_config):
    mock_config.return_value = CONFIGS
    ecs_client = boto3.client("ecs", region_name="us-east-1")
    ecs_client.create_cluster(clusterName=TEST_CLUSTER_NAME)
    ecs_client.register_task_definition(
        family="born_digital_validation",
        containerDefinitions=[
            {
                "name": "born_digital_validation",
                "image": "docker/hello-world:latest",
                "cpu": 1024,
                "memory": 400,
            }
        ],
    )
    s3_client = boto3.client("s3", region_name="us-east-1")
    s3_client.create_bucket(Bucket="amzn-s3-demo-bucket")
    s3_client.put_object(Bucket="amzn-s3-demo-bucket", Key="APKAEIBAERJR2EXAMPLE", Body="content")

    with open(Path('fixtures', 'guardduty_msg.json'), 'r') as df:
        message = json.load(df)
        lambda_handler(message, None)

        tasks = ecs_client.list_tasks(cluster=TEST_CLUSTER_NAME)
        assert len(tasks['taskArns']) == 1

        task_response = ecs_client.describe_tasks(
            cluster=TEST_CLUSTER_NAME,
            tasks=[tasks['taskArns'][0]])

        assert task_response['tasks'][0]['startedBy'] == 'lambda/born_digital_trigger'
        assert task_response['tasks'][0][
            'taskDefinitionArn'] == f"arn:aws:ecs:us-east-1:{DEFAULT_ACCOUNT_ID}:task-definition/born_digital_validation:1"
        with open(Path('fixtures', 'guardduty_args.json'), 'r') as af:
            args = json.load(af)
            assert task_response['tasks'][0]['overrides'] == args


@mock_aws
@patch('src.handle_born_digital_trigger.get_config')
def test_aurora_args(mock_config):
    mock_config.return_value = CONFIGS
    client = boto3.client("ecs", region_name="us-east-1")
    client.create_cluster(clusterName=TEST_CLUSTER_NAME)
    client.register_task_definition(
        family="born_digital_packaging",
        containerDefinitions=[
            {
                "name": "born_digital_packaging",
                "image": "docker/hello-world:latest",
                "cpu": 1024,
                "memory": 400,
            }
        ],
    )

    with open(Path('fixtures', 'aurora_msg.json'), 'r') as df:
        message = json.load(df)
        lambda_handler(message, None)

        tasks = client.list_tasks(cluster=TEST_CLUSTER_NAME)
        assert len(tasks['taskArns']) == 1

        task_response = client.describe_tasks(
            cluster=TEST_CLUSTER_NAME,
            tasks=[tasks['taskArns'][0]])

        assert task_response['tasks'][0]['startedBy'] == 'lambda/born_digital_trigger'
        assert task_response['tasks'][0][
            'taskDefinitionArn'] == f"arn:aws:ecs:us-east-1:{DEFAULT_ACCOUNT_ID}:task-definition/born_digital_packaging:1"
        with open(Path('fixtures', 'aurora_args.json'), 'r') as af:
            args = json.load(af)
            assert task_response['tasks'][0]['overrides'] == args


@mock_aws
def test_config():
    ssm = boto3.client('ssm', region_name='us-east-1')
    path = "/dev/born_digital_pipeline"
    for name, value in [("foo", "bar"), ("baz", "buzz")]:
        ssm.put_parameter(
            Name=f"{path}/{name}",
            Value=value,
            Type="SecureString",
        )
    config = get_config(path)
    assert config == {'foo': 'bar', 'baz': 'buzz'}


def test_get_volume_configurations():
    output = get_volume_configurations(0, "bar")
    assert output == []

    output = get_volume_configurations(1, "foo")
    assert output == [
        {
            'name': 'ebs',
            'managedEBSVolume': {
                'volumeType': 'gp3',
                'sizeInGiB': 1,
                'throughput': 125,
                'encrypted': True,
                'roleArn': 'foo',
                'tagSpecifications': [
                    {
                        'resourceType': 'volume',
                        'propagateTags': 'TASK_DEFINITION'
                    }
                ]
            }
        }
    ]
