#!/usr/bin/env python3

import logging
import traceback
from math import ceil
from os import environ

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

START_STATUS = 'START'

full_config_path = f"/{environ.get('ENV')}/{environ.get('APP_CONFIG_PATH')}"


def get_config(ssm_parameter_path):
    """Fetch config values from Parameter Store.

    Args:
        ssm_parameter_path (str): Path to parameters

    Returns:
        configuration (dict): all parameters found at the supplied path.
    """
    configuration = {}
    try:
        ssm_client = boto3.client(
            'ssm',
            region_name=environ.get('AWS_REGION'))

        param_details = ssm_client.get_parameters_by_path(
            Path=ssm_parameter_path,
            Recursive=False,
            WithDecryption=True)

        for param in param_details.get('Parameters', []):
            param_path_array = param.get('Name').split("/")
            section_position = len(param_path_array) - 1
            section_name = param_path_array[section_position]
            configuration[section_name] = param.get('Value')

    except BaseException:
        print("Encountered an error loading config from SSM.")
        traceback.print_exc()
    finally:
        return configuration


def calculate_gb_needed(object_bytes, expansion_ratio):
    """Calculates size needed to process an object, rounded up to the nearest integer.

    Args:
        object_bytes (str): Size of the object in bytes.
        expansion_ratio (str): Rate at which compressed files expand.

    Returns:
        gb_needed: GB needed to process the object.
    """
    needed_bytes = int(object_bytes) + \
        (int(object_bytes) * float(expansion_ratio))
    return ceil(needed_bytes / (1024 ** 3))


def get_volume_configurations(gb_needed, volume_role):
    volume_configurations = []
    if gb_needed:
        volume_configurations.append(
            {
                "name": "ebs",
                "managedEBSVolume": {
                    "volumeType": "gp3",
                    "sizeInGiB": gb_needed,
                    "throughput": 125,
                    "encrypted": True,
                    "roleArn": volume_role,
                    "tagSpecifications": [
                        {
                            "resourceType": "volume",
                            "propagateTags": "TASK_DEFINITION"
                        }
                    ]
                }
            }
        )
    return volume_configurations


def run_task(ecs_client, config, task_definition, environment, object_bytes=0):
    gb_needed = calculate_gb_needed(object_bytes, config['EXPANSION_RATIO'])
    volume_configurations = get_volume_configurations(
        gb_needed, config['EBS_VOLUME_ROLE'])

    service_response = ecs_client.run_task(
        cluster=config.get('ECS_CLUSTER'),
        launchType='FARGATE',
        networkConfiguration={
            'awsvpcConfiguration': {
                'subnets': [config.get('ECS_SUBNET')],
                'securityGroups': [config.get('ECS_SECURITY_GROUP')],
                'assignPublicIp': 'DISABLED'
            }
        },
        propagateTags='TASK_DEFINITION',
        taskDefinition=task_definition,
        count=1,
        startedBy='lambda/born_digital_trigger',
        overrides={
            'containerOverrides': [
                {
                    "name": task_definition,
                    "environment": environment
                }
            ]
        },
        volumeConfigurations=volume_configurations
    )
    return ", ".join([t['taskArn'] for t in service_response['tasks']])


def lambda_handler(event, context):
    """Triggers ECS task."""

    logging.debug(event)

    config = get_config(full_config_path)
    ecs_client = boto3.client('ecs', region_name=environ.get('AWS_REGION'))
    s3_client = boto3.client('s3', region_name=environ.get('AWS_REGION'))

    for record in event['Records']:

        if record.get('source') == 'aws.guardduty':
            """Handles events from GuardDuty."""

            logger.info("Received GuardDuty event")

            bucket_name = record['detail']['s3ObjectDetails']['bucketName']
            package_id = record['detail']['s3ObjectDetails']['objectKey']
            scan_result = record['detail']['scanResultDetails']['scanResultStatus']
            object_bytes = s3_client.head_object(Bucket=bucket_name, Key=package_id)['ContentLength']
            environment = [
                {
                    "name": "PACKAGE_ID",
                    "value": package_id
                },
                {
                    "name": "VIRUS_CHECK_OUTCOME",
                    "value": scan_result
                },
                {
                    "name": "SOURCE_BUCKET",
                    "value": bucket_name
                }
            ]
            task_id = run_task(
                ecs_client,
                config,
                'born_digital_validation',
                environment,
                object_bytes)
            logging.info(f"Task {task_id} with definition born_digital_validation started for package {package_id} from bucket {bucket_name}.")

        elif record.get('EventSource') == "aws:sns":
            """Handles events from Aurora."""

            logger.info("Received Aurora event")

            attributes = record['Sns']['MessageAttributes']

            package_id = attributes['package_id']['Value']
            package_db_id = attributes['package_db_id']['Value']

            environment = [
                {
                    "name": "PACKAGE_ID",
                    "value": package_id
                },
                {
                    "name": "PACKAGE_DB_ID",
                    "value": package_db_id
                }
            ]
            task_id = run_task(
                ecs_client,
                config,
                'born_digital_packaging',
                environment)

            logger.info(f"Task {task_id} with definition born_digital_packaging started for package {package_id}.")

        else:
            raise Exception('Unsure how to parse message')
