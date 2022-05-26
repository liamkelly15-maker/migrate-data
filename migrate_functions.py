#!/usr/bin/python3
#
#  DO NOT ALTER OR REMOVE COPYRIGHT NOTICES OR THIS HEADER.
#
#  Copyright (c) 2021 Ericsson Inc. All rights reserved.
#
#  The copyright to the computer program(s) herein is the property
#  of Ericsson Inc. The program(s) may be used and/or copied only with written permission from Ericsson Inc. or in
#  accordance with the terms and conditions stipulated in the agreement/contract under which the program(s) have been
#  supplied.
#
import json
import logging
import os
import shlex
import requests
import subprocess
import yaml
import time
import base64
from configparser import ConfigParser
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from requests.packages.urllib3.exceptions import InsecureRequestWarning

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream
from kubernetes.stream.ws_client import STDOUT_CHANNEL,STDIN_CHANNEL,STDERR_CHANNEL,ERROR_CHANNEL

args = None
logger = logging.getLogger('FunctionLibrary')
# For developer debugging -> enable it True
is_debug = False
# To supress cert verify warning during REST call
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# Global Constants
KUBECTL_VERSION = "v1.17.0"
KUBECTL_PATH = '/usr/local/bin/kubectl'
KUBE_ENV_PATH = '/ecm-umi/upgrade/ecm/kube.env'

PSQL_PATH = '/usr/bin/psql'

MIGRATE_SCHEMA_PATH = "/ecm-umi/upgrade/ecm/migrate_schemas/"

# NFS related variables
ECM_OBJECTS_ABCD_MOUNT_PATH = "/ecm-umi/upgrade/ecm/migrate_folder/ecm_files/objects"

# DB related variables
DB_CONFIG_SCHEMA_PATH = "/ecm-umi/upgrade/ecm/migrate_schemas/db_config.json"
DB_CONFIGURATION = json.load(open(DB_CONFIG_SCHEMA_PATH,))
DB_DUMP_MOUNT_PATH = "/tmp/pgdump_save_folder"

# AM related variables
AMSTER_CONTAINER = "amster"

# Core configuration file variables
CORE_CONFIG_SCHEMA_PATH = "/ecm-umi/upgrade/ecm/migrate_schemas/core_config.json"
CORE_CONFIGURATION = json.load(open(CORE_CONFIG_SCHEMA_PATH,))

def check_kube_env_file():
    """
    Verify kube.env file has values in it.

    Returns:
        boolean: If empty False else True
    """
    with open(KUBE_ENV_PATH) as file:
        for line in file:
            if (not line.startswith('#') and not line == '\n'):
                k, v = line.rstrip().split(': ')
                if v == None or not v:
                    logger.error(f'{k} value is empty!')
                    logger.error(f'Verify the kube.env file at {KUBE_ENV_PATH}')
                    return False
        return True


def get_kube_env(key):
    """
    Get value based on key from kube.env file.

    Args:
        key (str): property name

    Returns:
        str: property value
    """
    env_map = {}
    with open(KUBE_ENV_PATH) as file:
        for line in file:
            if (not line.startswith('#') and not line == '\n'):
                k, v = line.rstrip().split(': ')
                if v == None:
                    logger.error(f'{k} value is empty!')
                    return False
                else:
                    env_map[k] = v
    return env_map[key]

# Global Constants
KUBE_CONTEXT = get_kube_env('context')
KUBE_NAMESPACE = get_kube_env('namespace')

def wait_for_pod_to_start(pod_name, sleep_seconds=2, counter_limit=10, ignore_pattern=""):
    status = False
    counter = 0
    logger.debug('Waiting for {} to come up...'.format(pod_name))
    while True:
        code, response = run_terminal_command("kubectl get pods {}".format(get_pod_name(pod_name)), True)
        counter = counter + 1
        logger.debug(response)
        # if there is another similarly named pod when our pod disappears to be ignored that until our pod starts
        if ignore_pattern and ignore_pattern in response:
            pass
        elif "Running" in response:
            status = True
            break
        if counter > counter_limit:
            break
        time.sleep(sleep_seconds)
    return status


def run_terminal_command(command, enable_stdout):
    """
    Run a command using the API

    Args:
        command (str): command to execute
        enable_stdout (boolean): enable command output logging

    Returns:
        integer: command execution return code
        str: command response from terminal
    """
    logger.debug(f'<<<Terminal Command>>>\n{command}')
    terminal_response = ''
    args = shlex.split(command)
    sp = subprocess.Popen(args, bufsize=0, universal_newlines=True,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)
    while True:
        return_code = sp.poll()
        line = sp.stdout.readline()
        if line:
            terminal_response += line
            if enable_stdout:
                logger.debug(f'<<<Terminal Output>>>\n{line.rstrip()}')
        # Check if the process is terminated (return code is not None)
        elif return_code is not None:
            break
    return return_code, terminal_response

def get_pod_name(pod_prefix):
    """
    Get pod name from pod prefix

    Args:
        pod_prefix (str): pod prefix name

    Returns:
        str: pod name
    """
    kube_client = client.CoreV1Api(api_client=config.new_client_from_config(context=KUBE_CONTEXT))
    pods = kube_client.list_namespaced_pod(KUBE_NAMESPACE)
    logger.debug(f'Get pod from pod prefix {pod_prefix}')
    for kpod in pods.items:
        if pod_prefix in kpod.metadata.name and kpod.status.phase == 'Running':
            pod = kpod.metadata.name
            logger.debug(f'Pod {pod} status {kpod.status.phase}')
            return pod

def gen_command(user, fqdn, debug, command):
    """
    Generate command from params

    Args:
        user (str): run command using user
        fqdn (str): run command using fqdn
        debug (boolean): enable debugging
        command (str): command to execute

    Returns:
        str: return generated command
    """
    return 'ssh {user}@{fqdn} -o {options} {debug} "{command}"'.format(
        user=user,
        fqdn=fqdn,
        options="StrictHostKeyChecking=no",
        debug=debug,
        command=command
    )


def run_remote_command(description, user, host, command):
    """
    Run a command on the remote nodes using the API

    Args:
        description (str): description of the command
        user (str): run command using user
        host (str): run command on node
        command (str): command to execute

    Raises:
        RuntimeError: command exception

    Returns:
        str: command response stdout
    """
    logger.info(f'[TASK] Running {description} on the {host} node')
    debug = '' if not is_debug else '-v'
    return_code, terminal_response = run_terminal_command(gen_command(user, host, debug, command), True)
    if return_code != 0:
        raise RuntimeError(f'[ERROR] Remote command execution failed!')
    return terminal_response


def run_kube_command(pod, container, command):
    """
    Run a command on K8s pod/container

    Args:
        pod (str): pod name
        container (str): container name
        command (str): command to execute

    Returns:
        boolean: command response code successful return True else False
    """
    logger.info('Executing kubectl command')
    response = None
    exec_command = ['/bin/bash','-c',command]
    logger.info(f"Command: {exec_command}")

    kube_client = client.CoreV1Api(api_client=config.new_client_from_config(context=KUBE_CONTEXT))
    try:
        response = kube_client.read_namespaced_pod(name=pod,
                                                   namespace=KUBE_NAMESPACE)
    except ApiException as e:
        if e.status != 404:
            logger.error(f"Unknown error: {e}")
            return False
    if not response:
        logger.error("Pod does not exist!")
        return False
    try:
        response = stream(kube_client.connect_get_namespaced_pod_exec,
                          pod,
                          KUBE_NAMESPACE,
                          container=container,
                          command=exec_command,
                          stderr=True,
                          stdin=False,
                          stdout=True,
                          tty=False,
                          _preload_content=False)
        while response.is_open():
            response.update(timeout=1)
            if response.peek_stdout():
                logger.debug(f'<<<Kube Exec Output>>>\n{response.read_stdout()}')
            if response.peek_stderr():
                logger.debug(f'<<<Kube Exec Output>>>\n{response.read_stderr()}')
        err = response.read_channel(3)
        err = yaml.safe_load(err)
        if err['status'] == 'Success':
            logger.debug(f'Return Code: 0')
            return True
        else:
            rc = int(err['details']['causes'][0]['message'])
            logger.debug(f'Return Code: {rc}')
            return False
    except Exception as e:
        logger.error("error in executing cmd")
        return False

def run_kube_command_with_return(pod, container, command):
    """
    Run a command on K8s pod/container and returns it's output

    Args:
        pod (str): pod name
        container (str): container name
        command (str): command to execute

    Returns:
        boolean, : command response code successful return True else False
    """
    logger.debug('Executing kubectl command')
    response = None
    exec_command = ['/bin/bash','-c',command]
    logger.debug(f"Command: {exec_command}")

    kube_client = client.CoreV1Api(api_client=config.new_client_from_config(context=KUBE_CONTEXT))
    try:
        response = stream(kube_client.connect_get_namespaced_pod_exec,
                          pod,
                          KUBE_NAMESPACE,
                          container=container,
                          command=exec_command,
                          stderr=True,
                          stdin=False,
                          stdout=True,
                          tty=False,
                          _preload_content=False)
        while response.is_open():
            response.update(timeout=1)
        err = response.read_channel(3)
        err = yaml.safe_load(err)
        if err['status'] == 'Success':
            logger.debug(f'Return Code: 0')
            return True, response.read_stdout()
        else:
            rc = int(err['details']['causes'][0]['message'])
            logger.debug(f'Return Code: {rc}')
            return False, str(rc)
    except ApiException as e:
        if e.status != 404:
            logger.error(f"Unknown error: {e}")
            return False, str(e)


def copy_file_remote_to_local(user, host, path_src_dest):
    """
    Copy files/directories from remote host to local host using the API

    Args:
        user (str): run command using user
        host (str): run command on node
        path_src_dest (dict): dict object with src:dest key value pair

    Raises:
        RuntimeError: copy exception

    Returns:
        list: response list with each copy command response
    """
    response_list = []
    for key, value in path_src_dest.items():
        source = key
        destination = value

        if not check_remote_path(user, host, source):
            raise RuntimeError(f'[ERROR] Path {source} do not exist on {host} node!')

        if not local_path_exists(value):
            logger.debug(f"Path {destination} not found on ABCD")
            p = Path(destination)
            dest_dir = str(p.parent)

            logger.debug(f"Creating directory {dest_dir} on ABCD")
            Path(dest_dir).mkdir(parents=True, exist_ok=True)

        logger.info(f'Copy {source} from {host} to {destination}')
        command = f'scp -pr -o StrictHostKeyChecking=no -v {user}@{host}:{source} {destination}'
        return_code, command_response = run_terminal_command(command, False)

        if return_code == 1:
            logger.debug(command_response)
            raise RuntimeError("Terminal command failed")

        response_list.append(command_response)
    return response_list


def copy_file_local_to_pod(pod, container, path_src_dest):
    """
    Copy file/directory from local to container in a pod

    Args:
        pod (str): pod name
        container (str): container name
        path_src_dest (dict): dict object with src:dest key value pair

    Returns:
        list: response list with each copy command response
    """
    response_list = []
    for key, value in path_src_dest.items():
        source = key
        destination = value
        logger.info(f'Copy {source} to pod "{pod}" container "{container}" path "{destination}"')

        if(local_path_exists(source)):
            command = f'{KUBECTL_PATH} cp {source} {KUBE_NAMESPACE}/{pod}:{destination} -c {container}'
            logger.info(f'Command: {command}')
            response_list.append(run_terminal_command(command, False))
        else:
            logger.exception(f'Local path {source} does not exist')
    return response_list

def local_path_exists(path):
    """
    Verify given path exists as directory or file.

    Args:
        path (str): /path/to/file/or/directory on ABCD.

    Returns:
        boolean: True if exists, False if not
    """
    return os.path.isdir(path) or os.path.isfile(path)

def copy_db_dump_to_pod(config):
    """
    Copy DB dump to the DB pod

    Args:
        config (dict): DB dictionary object

    Returns:
        boolean: return True when passed
    """
    path_src_dest = {}
    for schema in config['db']['db_schemas']:
        path_src_dest[os.path.join(DB_DUMP_MOUNT_PATH,f"{config['db']['db_name']}_{schema}.dump")] = os.path.join(config['pod']['data_path'],f"{config['db']['db_name']}_{schema}.dump")
    try:
        response_list = copy_file_local_to_pod(config['pod']['name'], config['pod']['container'], path_src_dest)
        logger.debug("Response of copy_file_local_to_pod:")
        logger.debug(response_list)

        # response_list value looks like:
        # response_list = [(1, 'error: Some error'), (1, 'error: Some other error')]
        for response in response_list:
            if response[0] == 1:
                logger.exception(response[1])
                return False
        return True
    except Exception as e:
        logger.debug(e)
        logger.exception('Copy DB dump failed!')
        return False


def generate_remote_md5(user, host, path):
    """
    Copy files/directories from remote host to local host using the API

    Args:
        user (str): run command using user
        host (str): run command on node
        path (str): file path

    Raises:
        RuntimeError: command run exception

    Returns:
        str: response output
    """
    file_name = os.path.basename(path)
    dir_name = os.path.dirname(path)
    logger.info(f'Generate MD5 checksum for "{file_name}"')
    command = f'cd {dir_name};md5sum {file_name} > {file_name}.md5;md5sum --check {file_name}.md5'
    logger.info(f'Running generate MD5 checksum on the {host} node')
    debug = '' if not is_debug else '-v'
    return_code, terminal_response = run_terminal_command(gen_command(user, host, debug, command), True)
    if return_code != 0:
        raise RuntimeError(f'Generate MD5 checksum failed!')
    return terminal_response


def check_md5(path):
    """
    Check and verify MD5 path

    Args:
        path (str): md5 file path

    Raises:
        RuntimeError: command run exception

    Returns:
        str: response output
    """
    file_name = os.path.basename(path)
    dir_name = os.path.dirname(path)
    logger.info(f'Verify MD5 checksum for "{file_name}"')
    command = f'cd {dir_name}; md5sum --check {file_name}'
    logger.debug(f'<<<Command>>>\n{command}')
    terminal_response = ''
    sp = subprocess.Popen(command, bufsize=0,
                          universal_newlines=True,
                          shell=True,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)
    while True:
        return_code = sp.poll()
        line = sp.stdout.readline()
        if line:
            terminal_response += line
            logger.debug(f'<<<Output>>>\n{line.rstrip()}')
        # Check if the process is terminated (return code is not None)
        elif return_code is not None:
            break
    if return_code != 0:
        raise RuntimeError(f'MD5 check "{command}" failed!')
    return terminal_response


def copy_map(source, destination):
    """
    Generate file map for file copy

    Args:
        source (str): source path
        destination (str): destination path

    Returns:
        dict: return dict 'source: source value, destination: destination value'
    """
    return {'source': source, 'destination': destination}


def check_remote_path(user, host, path):
    """
    Check path available in the remote host

    Args:
        user (str): remote host user
        host (str): remote host Ip/FQDN
        path (str): remote path to check

    Returns:
        str: response output
    """
    logger.debug(f'Check if path "{path}" exist on "{host}" node')
    command = f'stat {path}'
    debug = '' if not is_debug else '-v'
    return_code, terminal_response = run_terminal_command(gen_command(user, host, debug, command), True)
    if return_code != 0:
        return False
    return terminal_response


def check_pod_status(pod, container=''):
    """
    Check pod and container status

    Args:
        pod (str): pod name
        container (str, optional): container name. Defaults to ''.

    Returns:
        boolean: True if success else False
    """
    status = False
    if pod:
        kube_client = client.CoreV1Api(api_client=config.new_client_from_config(context=KUBE_CONTEXT))
        pods = kube_client.list_namespaced_pod(KUBE_NAMESPACE)
        logger.info(f'Checking pod {pod} status')

        pod_exists = False
        for kpod in pods.items:
            if kpod.metadata.name == pod and kpod.status.phase == 'Running':
                logger.debug(f'Pod {kpod.metadata.name} status {kpod.status.phase}')
                logger.debug(f'Pod {kpod.metadata.name} status verified')
                status = True
                pod_exists = True
                if container:
                    logger.info(f'Checking container {container} status')
                    for kcontainer in kpod.status.container_statuses:
                        if kcontainer.name == container and kcontainer.started:
                            logger.debug(f'Container {container} status verified')
                            status = True

        if not pod_exists:
            logger.error(f'ERROR: Pod {pod} not in list of environment pods')

    return status


def __check_nfs_mount(path):
    """
    Check nfs mount path is mounted or not

    Args:
        path (str): nfs mount path

    Returns:
        boolean: True if success else False
    """
    if os.path.ismount(path):
        return True


def __mount_nfs_path(nfs_host, nfs_location, nfs_mount_path):
    """
    Mount NFS to the given path

    Args:
        nfs_host (str): NFS host
        nfs_location (str): NFS location
        nfs_mount_path (str): NFS mount path

    Returns:
        boolean: True if success else False
    """
    logger.info('Mount NFS path')

    if not os.path.exists(nfs_mount_path):
        os.makedirs(nfs_mount_path)

    command = f'mount'
    command = command + f' {nfs_host}:{nfs_location}'
    command = command + f' {nfs_mount_path}'
    logger.info(f'Command: {command}')
    if not __check_nfs_mount(nfs_mount_path):
        return_code, terminal_response = run_terminal_command(command, True)
        if return_code != 0:
            logger.error(f'NFS mount failed!')
            return False
        else:
            logger.info(f'Response: {terminal_response}')
    return True


def __unmount_nfs_path(nfs_mount_path):
    """
    Un-mount NFS mount path

    Args:
        nfs_mount_path (str): NFS mount path

    Returns:
        boolean: True if success else False
    """
    logger.info('Un-mount NFS path')
    command = f'umount'
    command = command + f' {nfs_mount_path}'
    logger.info(f'Command: {command}')
    if __check_nfs_mount(nfs_mount_path):
        return_code, terminal_response = run_terminal_command(command, True)
        if return_code != 0:
            logger.error(f'NFS unmount failed!')
            return False
        else:
            logger.debug(f'Response: {terminal_response}')
    return True


def mount_db_nfs_path(baseenv):
    """
    Mount DB NFS to the given path

    Args:
        baseenv (obj): baseenv object

    Returns:
        boolean: True if success else False
    """
    return __mount_nfs_path(baseenv.get('DB_DUMP_NFS_HOST'), baseenv.get('DB_DUMP_LOCATION'), DB_DUMP_MOUNT_PATH)


def unmount_db_nfs_path():
    """
    Un-mount DB NFS to the given path

    Returns:
        boolean: True if success else False
    """
    return __unmount_nfs_path(DB_DUMP_MOUNT_PATH)


def mount_objects_nfs_path(baseenv):
    """
    Mount ECM Images NFS to the given path

    Args:
        baseenv (obj): baseenv object

    Returns:
        boolean: True if success else False
    """
    return __mount_nfs_path(baseenv.get('ECM_OBJECTS_NFS_HOST'), baseenv.get('ECM_OBJECTS_NFS_LOCATION'), ECM_OBJECTS_ABCD_MOUNT_PATH)


def unmount_objects_nfs_path():
    """
    Un-mount ECM Images NFS to the given path

    Returns:
        boolean: True if success else False
    """
    return __unmount_nfs_path(ECM_OBJECTS_ABCD_MOUNT_PATH)


def drop_db_schema(config, schema):
    """
    Drop DB schema. This method cleans the DB schema before restore.

    Args:
        config (dict): DB Configuration dictionary

    Returns:
        boolean: returns True if passed
    """
    try:
        # Drop the schema
        command = f'{PSQL_PATH}'
        command += f' --host={config["pod"]["db_host"]}'
        command += f' --port={config["pod"]["db_port"]}'
        command += f' --username={config["pod"]["db_admin_user"]}'
        command += f' --dbname={config["pod"]["db_name"]}'
        command += f' --command="DROP SCHEMA IF EXISTS {schema} CASCADE;"'
        return run_kube_command(config["pod"]['name'], config["pod"]['container'], command)
    except Exception as e:
        logger.debug(e)
        logger.exception(f'DB schema {schema} drop failed!')
        return False

def backup_table(table_name, config):
    sql = f"SELECT * INTO { table_name }_bak from { table_name }"
    return run_sql_command(sql, config)

def run_sql_command(sql, config):
    try:
        # Drop the schema
        command = f'{PSQL_PATH}'
        command += f' --host={config["pod"]["db_host"]}'
        command += f' --port={config["pod"]["db_port"]}'
        command += f' --username={config["pod"]["db_admin_user"]}'
        command += f' --dbname={config["pod"]["db_name"]}'
        command += f' --command="{sql}"'
        return run_kube_command(config["pod"]['name'], config["pod"]['container'], command)
    except Exception as e:
        logger.debug(e)
        logger.exception(f'SQL command failed: {command}')
        return False

def restore_db_dump(config):
    """
    Restore DB dump using pg_restore.

    Args:
        config (dict): DB Configuration dictionary

    Returns:
        boolean: returns True if passed
    """
    try:
        for schema in config["db"]['db_schemas']:
            # Restore the schema from dump
            command = '/usr/bin/pg_restore'
            command += f' --host={config["pod"]["db_host"]}'
            command += f' --port={config["pod"]["db_port"]}'
            command += f' --username={config["pod"]["db_user"]}'
            command += f' --dbname={config["pod"]["db_name"]}'
            command += f' {config["restore"]["flags"]}'
            command += ' --format=directory'
            command += f' {config["pod"]["data_path"]}/{config["db"]["db_name"]}_{schema}.dump'
            status = run_kube_command(config["pod"]['name'], config["pod"]['container'], command)
            if not status:
                return status
        return status
    except Exception as e:
        logger.debug(e)
        logger.exception('DB dump restore failed!')
        return False


def clear_orphaned_db_lo(config):
    """
    Clear DB orphaned largeobject metadata after drop schema.

    Args:
        config (dict): DB Configuration dictionary

    Returns:
        boolean: returns True if passed
    """
    try:
        command = '/usr/bin/vacuumlo'
        command += f' -h {config["pod"]["db_host"]}'
        command += f' -p {config["pod"]["db_port"]}'
        command += f' -U {config["pod"]["db_admin_user"]}'
        command += f' {config["pod"]["db_name"]}'
        command += ' -v'
        return run_kube_command(config["pod"]['name'], config["pod"]['container'], command)
    except Exception as e:
        logger.debug(e)
        logger.exception('DB orphaned largeobject metadata cleanup failed!')
        return False


def delete_db_tables_relations(config, schema):
    """
    Delete all tables and relations and keep the empty public schema

    Args:
        config (dict): DB Configuration dictionary

    Returns:
        boolean: returns True if passed
    """
    try:
        # Delete tables
        sql_command = f"""DO \$\$ DECLARE tabname RECORD;BEGIN FOR tabname IN (SELECT tablename FROM pg_tables WHERE schemaname = '{schema}') LOOP EXECUTE 'DROP TABLE IF EXISTS ' || quote_ident(tabname.tablename) || ' CASCADE';END LOOP;END \$\$;"""
        command = f'{PSQL_PATH}'
        command += f' --host={config["pod"]["db_host"]}'
        command += f' --port={config["pod"]["db_port"]}'
        command += f' --username={config["pod"]["db_admin_user"]}'
        command += f' --dbname={config["pod"]["db_name"]}'
        command += f' --command="{sql_command}"'
        return run_kube_command(config["pod"]['name'], config["pod"]['container'], command)
    except Exception as e:
        logger.debug(e)
        logger.exception(f'Delete tables from {schema} failed!')
        return False


def copy_db_tables_data(config, from_schema, to_schema):
    """
    Copy all tables from one schema to another

    Args:
        config (dict): DB Configuration dictionary

    Returns:
        boolean: returns True if passed
    """
    try:
        # Copy tables
        sql_command = f"""DO \$\$ DECLARE table_record regclass; BEGIN SET LOCAL search_path = {from_schema}; FOR table_record IN SELECT  table_name  FROM information_schema.tables WHERE table_schema='{from_schema}' LOOP EXECUTE format('ALTER TABLE %s SET SCHEMA {to_schema}',table_record); END LOOP; END; \$\$;"""
        command = f'{PSQL_PATH}'
        command += f' --host={config["pod"]["db_host"]}'
        command += f' --port={config["pod"]["db_port"]}'
        command += f' --username={config["pod"]["db_admin_user"]}'
        command += f' --dbname={config["pod"]["db_name"]}'
        command += f' --command="{sql_command}"'
        return run_kube_command(config["pod"]['name'], config["pod"]['container'], command)
    except Exception as e:
        logger.debug(e)
        logger.exception(f'Copy tables from {from_schema} to {to_schema} failed!')
        return False

def get_pod_name(pod_prefix):
    """
    Get pod name from pod prefix

    Args:
        pod_prefix (str): pod prefix name

    Returns:
        str: pod name
    """
    kube_client = client.CoreV1Api(api_client=config.new_client_from_config(context=KUBE_CONTEXT))
    pods = kube_client.list_namespaced_pod(KUBE_NAMESPACE)
    logger.debug(f'Get pod from pod prefix {pod_prefix}')
    for kpod in pods.items:
        if pod_prefix in kpod.metadata.name and kpod.status.phase == 'Running':
            pod = kpod.metadata.name
            logger.debug(f'Pod {pod} status {kpod.status.phase}')
            return pod


def run_remote_command(description, user, host, command):
    """
    Run a command on the remote nodes using the API

    Args:
        description (str): description of the command
        user (str): run command using user
        host (str): run command on node
        command (str): command to execute

    Raises:
        RuntimeError: command exception

    Returns:
        str: command response stdout
    """
    logger.info(f'[TASK] Running {description} on the {host} node')
    debug = '' if not is_debug else '-v'
    return_code, terminal_response = run_terminal_command(gen_command(user, host, debug, command), True)
    if return_code != 0:
        raise RuntimeError(f'[ERROR] Remote command execution failed!')
    return terminal_response

def run_amster_script(script_name):
    """
    Run amster script

    Args:
        script_name (str): script name

    Returns:
        str: returns amster command response
    """
    try:
        am_pod = get_pod_name("eric-eo-cm-idam-openam-")
        if am_pod is None:
            raise RuntimeError('AM pod not running')
        pod_script_path = f'/tmp/{script_name}'
        path_src_dest = {}
        path_src_dest[f'{MIGRATE_SCHEMA_PATH}/{script_name}'] = pod_script_path
        status = copy_file_local_to_pod(am_pod, AMSTER_CONTAINER, path_src_dest)
        if not status: return status

        response = None
        exec_command = ['/bin/bash','-c',f'/opt/amster/amster {pod_script_path} -v']
        logger.info(f"Command: {exec_command}")

        kube_client = client.CoreV1Api(api_client=config.new_client_from_config(context=KUBE_CONTEXT))
        try:
            response = kube_client.read_namespaced_pod(name=am_pod,
                                                       namespace=KUBE_NAMESPACE)
        except ApiException as e:
            if e.status != 404:
                logger.error(f"Unknown error: {e}")
                return False
        if not response:
            logger.error("Pod does not exist!")
            return False
        response = stream(kube_client.connect_get_namespaced_pod_exec,
                          am_pod,
                          KUBE_NAMESPACE,
                          container=f'{AMSTER_CONTAINER}',
                          command=exec_command,
                          stderr=True,
                          stdin=False,
                          stdout=True,
                          tty=False,
                          _preload_content=False)
        while response.is_open():
            response.update(timeout=1)
        output = response.read_stdout()
        start = output.find("===> ") + len("===> ")
        end = output.find("\nBye!")
        amster_output = output[start:end]
        logger.info(f'Amster Command Output: {amster_output}')
        err = response.read_channel(3)
        err = yaml.safe_load(err)
        if err['status'] == 'Success':
            logger.debug(f'Return Code: 0')
            return amster_output
        else:
            rc = int(err['details']['causes'][0]['message'])
            logger.debug(f'Return Code: {rc}')
            return False

    except Exception as e:
        logger.debug(e)
        logger.exception(f'Amster commands failed!')
        return False


def read_config_file(configfile, config):
    """
    Read configuration file

    Args:
        configfile (str): configuration file
        config (dict): configuration(core or wfmgmt)

    Returns:
        params: parsed parameters extracted from file
    """
    try:
        parser = ConfigParser()
        params = {}
        with open(configfile) as stream:
            parser.read_string("[top]\n" + stream.read())

            for parameter in config['parameters']['template_var']:
                if config == CORE_CONFIGURATION['core']:
                    param_bytes = parser.get('top', config['parameters']['param_dict'][parameter]).encode("ascii")
                    param_base64 = base64.b64encode(param_bytes)
                    params[config['parameters']['param_dict'][parameter]] = param_base64.decode("ascii")
                else:
                    params[config['parameters']['param_dict'][parameter]] = parser.get('top', config['parameters']['param_dict'][parameter],
                                                                                       fallback=config['parameters']['defaults'][parameter])

        logger.info('Read {0} file, needed values: {1}'.format(configfile, params))
        return params
    except Exception as e:
        logger.debug(e)
        logger.exception('Migrate '+config["files"]["config_file"]+' file read failed!')
        return False

def generate_kube_command(params, configuration):
    """
    Generate Kubectl command

    Args:
        :param configuration: configuration name
        :param params: Dictionary of config parameters

    Returns:
        kube_command: kubectl command
    """
    try:
        rendered_config = ''
        logger.info('Preparing kubernetes config change ')
        t_env = Environment(loader=FileSystemLoader('/ecm-umi/upgrade/ecm/migrate_schemas/'))
        template_file = configuration['files']['template_file']
        template = t_env.get_template(template_file)
        for parameter in configuration['parameters']['template_var']:
            rendered_config = template.render(parameter=params[configuration['parameters']['param_dict'][parameter]])

        text_file = open(configuration['common']['abcd_directory'] + configuration['files']['text_file'], "w")
        text_file.write(rendered_config)
        text_file.close()
        kube_command = f'kubectl apply -f {configuration["common"]["abcd_directory"]}{configuration["files"]["text_file"]}'

        return kube_command
    except Exception as e:
        logger.debug(e)
        logger.exception('Generate Kube command failed!')
        return


def rest_api_call(request, url, headers, payload, return_code):
    """
    Generic method for REST API calls using requests lib

    Args:
        request (str): API request type (GET, POST, PUT,...)
        url (str): API url
        headers (dict): API call headers
        payload (dict): API body JSON format
        return_code (int): API response code to check against

    Returns:
        boolean: True is passed, False if failed
    """
    try:
        logger.debug(f'REQUEST: {request}')
        logger.debug(f'URL: {url}')
        logger.debug(f'HEADERS: {headers}')
        logger.debug(f'BODY: {payload}')
        response = requests.request(request, url, headers=headers, data=payload, verify=False)
        for code in return_code:
            if response.status_code == code:
                logger.info(f'RESPONSE: {response.status_code}')
                logger.debug(f'Full Response: {response.text}')
                return True
        logger.error(f'API Request "{request} {url}" failed: [{response.status_code}] {response.text}')
        return False
    except Exception as e:
        logger.debug(e)
        logger.exception(f'API Request "{request} {url}" failed!')
        return False

def record_files_directories_path(path):
    """
    Takes a ABCD path as input and creates a json with recurssive path.
    identifies files and directories, creates json tree.
    Use json dump to dump the result as proper JSON.

    Args:
        path (str): Directory path

    Returns:
        dict: json with all the files and directories info
    """
    logger.debug(f'Start recording objects in the path "{path}"')
    try:
        if not local_path_exists(path):
            logger.error(f"Path {path} not found on ABCD")
            return None
        json_obj = {'name': os.path.basename(path)}
        if os.path.isdir(path):
            json_obj['type'] = "directory"
            json_obj['path'] = path
            json_obj['children'] = [record_files_directories_path(os.path.join(path,x)) for x in os.listdir(path)]
        else:
            json_obj['type'] = "file"
            json_obj['path'] = path
        return json_obj
    except Exception as e:
        logger.debug(e)
        logger.exception(f'Start recording objects in the path "{path}" failed!')
        return None

def restart_pods(pod_name, pod_type):
    """
    Restart a given pod

    Args:
        pod_name (str): Name of the POD
        pod_type (str): Type of the POD (deployment, statefulset)

    Returns:
        bool: True if restarted successfully else False
    """
    timeout = '600s'

    restart_commands = [
        f"kubectl rollout restart {pod_type}/{pod_name}",
        f'kubectl rollout status --watch --timeout={timeout} {pod_type}/{pod_name}'
    ]

    status = False

    logger.info(f"Restarting pod {pod_name}")

    for command in restart_commands:
        logger.debug(f'COMMAND: {command}')
        output = run_terminal_command(command, True)
        logger.debug(f'OUTPUT: {output}')
        if output[0] == 0:
            status = True
        else:
            logger.error(f'Pod {pod_name} restart failed!')
            status = False
            break

    if status: logger.info(f"Pod {pod_name} restarted")

    return status

def config_setup(baseenv):
    command = f'kubectl config set-context --current --namespace={KUBE_NAMESPACE}'
    logger.debug(f'COMMAND: {command}')
    output = run_terminal_command(command, True)
    logger.debug(f'OUTPUT: {output}')
    if output[0] == 0:
        return True
    else:
        logger.error(f'Namespace configured to {KUBE_NAMESPACE}')
        return False

def run_ghost_command(command):
    """
    Run a background command using the API

    Args:
        command (str): command to execute

    Returns:
        boolean: return True when success
    """
    logger.debug(f'<<<Terminal Command>>>\n{command}')
    terminal_response = ''
    sp = subprocess.Popen(command, shell=True)
    sp.wait()
    if sp.returncode == 0:
        return True
    else:
        return False


def enable_k8s_port_forwarding(pod, port):
    command = f'{KUBECTL_PATH} port-forward -n {KUBE_NAMESPACE} pod/{pod} {port}:{port} &'
    status = run_ghost_command(command)
    time.sleep(2)
    return status


def disable_k8s_port_forwarding(port):
    command = f'kill -9 `lsof -t -i:{port}` &'
    status = run_ghost_command(command)
    return status