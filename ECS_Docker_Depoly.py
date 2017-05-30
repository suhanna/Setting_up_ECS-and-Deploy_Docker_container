#!/usr/bin/env python
import time
import boto3
import logging
import sys
import botocore
# Configurations
session = boto3.Session()
credentials=session.get_credentials()
access_key = credentials.access_key
secret_key = credentials.secret_key
region = botocore.session.get_session().get_config_variable('region')
conn_args = {
    'aws_access_key_id': access_key,
    'aws_secret_access_key': secret_key,
    'region_name': region
}

""" Set logger """
logger = logging.getLogger('__AWS__')		# create logger
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler(sys.stdout)		# create handler and set level to debug
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s : %(message)s')		# create formatter
ch.setFormatter(formatter)	# add formatter to ch
logger.addHandler(ch)		# add ch to logger

ecs = session.client('ecs',**conn_args) 
ec2 = session.resource('ec2',**conn_args)
elb = session.client('elb',**conn_args) 
iam = session.resource('iam',**conn_args)
asg = session.client('autoscaling',**conn_args)

""" Create Key pair """
key_pair = ec2.create_key_pair(KeyName='my-ec2-key-pair')
logger.debug("--- Create Key pair : my-ec2-key-pair")

""" Create Security group """
mysg = ec2.create_security_group(GroupName="ssh-and-http-from-anywhere",Description='This is my test security group')
mysg.authorize_ingress(IpProtocol="tcp",CidrIp="0.0.0.0/0",FromPort=80,ToPort=80)
mysg.authorize_ingress(IpProtocol="tcp",CidrIp="0.0.0.0/0",FromPort=22,ToPort=22) 
logger.debug("--- create Security Group : ssh-and-http-from-anywhere")

""" ECS Setup """
""" 1. create Cluster """
Cluster = ecs.create_cluster(
    clusterName='my-ecs-cluster'
)
logger.debug("--- Create Cluster : my-ecs-cluster")

""" 2. create Elastic Load Balancer """
Load_balancer = elb.create_load_balancer(
    LoadBalancerName='ecs-load-balancer',
    Listeners=[
        {
            'Protocol': 'HTTP',
            'LoadBalancerPort': 80,
            'InstanceProtocol': 'HTTP',
            'InstancePort': 80
        },
    ],
    AvailabilityZones=[
        'ap-northeast-1a','ap-northeast-1c'
    ],
    SecurityGroups=[
        mysg.id,
    ],
)
logger.debug("--- create Elastic Load Balancer : ecs-load-balancer")
# Add Health Check to ELB
Load_balancer_healthcheck = elb.configure_health_check(
    LoadBalancerName='ecs-load-balancer',
    HealthCheck={
        'Target': 'HTTP:80/',
        'Interval': 30,
        'Timeout': 5,
        'UnhealthyThreshold': 2,
        'HealthyThreshold': 10
    }
)
logger.debug("--- configure Health check to Elastic Load Balancer : ecs-load-balancer")

""" 3. create IAM Roles """
""" create two permissions that to communicate EC2 to Cluster and communicate Cluster to ELB"""
# Create Role (permission for EC2 -> ECS)
role = iam.create_role(
    RoleName='ecs-instance-role-test',
    AssumeRolePolicyDocument='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":["ec2.amazonaws.com"]},"Action":["sts:AssumeRole"]}]}'
)
logger.debug("--- create AWS Role : ecs-instance-role-test")
# Attach Policy to Role
response = role.attach_policy(
    PolicyArn='arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role'
)
logger.debug("--- Attach policy : AmazonEC2ContainerServiceforEC2Role to AWS Role : ecs-instance-role-test")
# create Role (permission for ECS -> ELB)
role = iam.create_role(
    RoleName='ecs-service-role-test',
    AssumeRolePolicyDocument='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":["ecs.amazonaws.com"]},"Action":["sts:AssumeRole"]}]}'
)
logger.debug("--- create AWS Role : ecs-service-role-test")
# Attach Policy To Role
response = role.attach_policy(
    PolicyArn='arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceRole'
)
logger.debug("--- Attach policy : AmazonEC2ContainerServiceRole to AWS Role : ecs-service-role-test")
#create instance profile
instance_profile = iam.create_instance_profile(
    InstanceProfileName='ecs-instance-profile',
)
logger.debug("--- create Instance Profile : ecs-instance-profile")
# Add Role to Instance Profile
response = instance_profile.add_role(
    RoleName='ecs-instance-role-test'
)
logger.debug("--- Add role : ecs-instance-role-test to Instance Profile : ecs-instance-profile")
time.sleep(20)  	# Wait until Instance Profile is ready (So that this can be used for Launch Configuration)

""" 4. Create Auto Scaling Group """
#Create Launch Configuration
userdata = """
#!/bin/bash
echo ECS_CLUSTER=my-ecs-cluster > /etc/ecs/ecs.config
"""
try:
	Launch_conf = asg.create_launch_configuration(
	    LaunchConfigurationName='ecs-launch-configuration',
	    ImageId='ami-4309aa22',
	    KeyName='my-ec2-key-pair',
	    SecurityGroups=[
	        'ssh-and-http-from-anywhere',
	    ],
	    UserData=userdata,
	    InstanceType='t2.micro',
	    IamInstanceProfile='ecs-instance-profile'
	)
	logger.debug("--- create Launch Configuration : ecs-launch-configuration")
except botocore.exceptions.ClientError as e:
    sys.exit('ERROR: {}'.format(e)) 	# Exit if there is an error

# Create Auto Scaling Group
try:
	Auto_scaling_group = asg.create_auto_scaling_group(
	    AutoScalingGroupName='ecs-auto-scaling-group',
	    LaunchConfigurationName='ecs-launch-configuration',
	    MinSize=1,
	    MaxSize=3,
	    DesiredCapacity=2,
	    DefaultCooldown=123,
	    AvailabilityZones=[
	        'ap-northeast-1a','ap-northeast-1c'
	    ],
	    HealthCheckType='EC2',
	    HealthCheckGracePeriod=300,
	)
	logger.debug("--- create Auto Scaling Group : ecs-auto-scaling-group")
except botocore.exceptions.ClientError as e:
    sys.exit('ERROR: {}'.format(e)) 	# Exit if there is an error

""" Deploy Docker Container """
""" 1. Task Definition """
Task_definition = ecs.register_task_definition(
    family='hello-world-task',
    containerDefinitions=[
        {
            'name': 'hello-world-container',
            'image': 'training/webapp:latest',
            'memory': 128,
            'portMappings': [
                {
                    'containerPort': 5000,
                    'hostPort': 80,
                    'protocol': 'tcp'
                },
            ],
        },
    ],
)
logger.debug("--- create Task Definition : hello-world-task")

""" 2. create Service """
service = ecs.create_service(
    cluster='my-ecs-cluster',
    serviceName='hello-world-service',
    taskDefinition='hello-world-task',
    loadBalancers=[
        {
            'loadBalancerName': 'ecs-load-balancer',
            'containerName': 'hello-world-container',
            'containerPort': 5000
        },
    ],
    desiredCount=1,
    role='ecs-service-role-test',
)
logger.debug("--- create Service : hello-world-service")
logger.debug("--- The End")