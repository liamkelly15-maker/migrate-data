#!/usr/bin/python3
#
#  DO NOT ALTER OR REMOVE COPYRIGHT NOTICES OR THIS HEADER.
#
#  Copyright (c) 2018 Ericsson Inc. All rights reserved.
#
#  The copyright to the computer program(s) herein is the property
#  of Ericsson Inc. The program(s) may be used and/or copied only with written permission from Ericsson Inc. or in
#  accordance with the terms and conditions stipulated in the agreement/contract under which the program(s) have been
#  supplied.

import logging

from migrate_functions.migrate_functions import DB_CONFIGURATION
from migrate_functions.migrate_functions import copy_db_dump_to_pod
from migrate_functions.migrate_functions import drop_db_schema
from migrate_functions.migrate_functions import mount_db_nfs_path
from migrate_functions.migrate_functions import restore_db_dump
from migrate_functions.migrate_functions import check_pod_status
from migrate_functions.migrate_functions import unmount_db_nfs_path
from migrate_functions.migrate_functions import clear_orphaned_db_lo
from migrate_functions.migrate_functions import restart_pods
from migrate_functions.migrate_functions import run_kube_command

logger = logging.getLogger('FunctionLibrary')


def migrate_nso_data(baseenv):
    """
    This method migrate NSO DB data from legacy to Cloud Native EO CM.
    We follow a certain procedure to make the NSO DB migration working.
    1. Mount the RDB NFS
    2. Check if NS LCM DB PODS are running
    3. Save camunda objects to a temp table
    4. Create a DB dump of the temp table
    5. Copy NSO DB dump from NFS to DB POD
    6. Drop the exisiting DB schemas
    7. Clear orphaned large objects
    8. DB restore the NSO DB dump
    9. Backup the configuration table values
    10. Delete the configuraion table entries
    11. Restore the temp table DB dump
    12. Fix the databasechangelog table with correct file name
    13. unmount the RDB NFS
    14. Restart the NS LCM service pods

    Args:
        baseenv (dict): dict

    Returns:
        boolean: returns True/False
    """
    logger.info('Migrate NSO database data')
    config = DB_CONFIGURATION['nso']
    status = False

    try:
        status = mount_db_nfs_path(baseenv)
        if not status: return status

        status = check_pod_status(config['pod']['name'], config['pod']['container'])
        if not status: return status

        # Save the selected camunda objects to a tmp table
        status = save_databasechangelog_camunda_objects(config)
        if not status: return status

        # Create a DB dump of the tmp table
        status = dump_databasechangelog_camunda_objects(config)
        if not status: return status

        status = copy_db_dump_to_pod(config)
        if not status: return status

        for schema in config['db']['db_schemas']:
            status = drop_db_schema(config,schema)
            if not status: return status

        status = clear_orphaned_db_lo(config)
        if not status: return status

        status = restore_db_dump(config)
        if not status: return status

        # Drop configuration table
        status = backup_table_configuration_values(config)
        if not status: return status

        status = delete_table_configuration_values(config)
        if not status: return status

        status = restore_databasechangelog_camunda_objects(config)
        if not status: return status

        # update databasechangelog set filename='changelog-sodb1-sequences.xml' where filename='./changelog-sodb1-sequences.xml';
        status = fix_changelog_seq_values(config)
        if not status: return status

        status = unmount_db_nfs_path()
        if not status: return status

        status = restart_pods(config['restart']['pod']['name'], config['restart']['pod']['type'])
    except Exception as e:
        logger.debug(e)
        logger.exception('Migrate NSO database data failed!')
        return status
    return status

def save_databasechangelog_camunda_objects(config):
    """
    Save the exisiting camunda objects in a tmp table

    Args:
        config (dict): db configuration dict

    Returns:
        boolean: return True if the command execution successful
    """
    # sql = "CREATE TABLE tmp_databasechangelog AS (SELECT * FROM databasechangelog WHERE databasechangelog.filename='lns-camunda_objects-7.14.0.xml' or databasechangelog.filename='lns-sodb1-configuration.xml');"
    sql = "CREATE TABLE tmp_databasechangelog AS (SELECT * FROM databasechangelog WHERE databasechangelog.filename='lns-camunda_objects-7.14.0.xml');"

    try:
        # Drop the schema
        command = f'/usr/bin/psql'
        command += f' --host={config["pod"]["db_host"]}'
        command += f' --port={config["pod"]["db_port"]}'
        command += f' --username=process_engine'
        command += f' --dbname={config["pod"]["db_name"]}'
        command += f' --command="{sql}"'
        return run_kube_command(config["pod"]['name'], config["pod"]['container'], command)
    except Exception as e:
        logger.debug(e)
        logger.exception(f'Tried to migrate databasechangelog where WHERE databasechangelog.filename=lns-camunda_objects-7.14.0.xml')
        return False

def dump_databasechangelog_camunda_objects(config):
    """
    Create a DB dump of the tmp table with camunda objects after processing the entries

    Args:
        config (dict): db configuration dict

    Returns:
        boolean: return True if the command execution successful
    """
    command = "pg_dump --host localhost --port 5432 --username process_engine --dbname=sodb1 --table tmp_databasechangelog | sed 's/process_engine.tmp_/process_engine./' > /tmp/lns_camunda_objects_7_14.sql"
    # Verify dump file not empty
    return run_kube_command(config["pod"]['name'], config["pod"]['container'], command)

def backup_table_configuration_values(config):
    """
    Take a backup of the configuration table. The backup is stored in the new configuration_bak table.

    Args:
        config (dict): db configuration dict

    Returns:
        boolean: return True if the command execution successful
    """
    sql_backup = "SELECT * INTO configuration_bak from configuration"
    command = f"/usr/bin/psql --host=localhost --port=5432 --dbname=sodb1 -U process_engine --command='{sql_backup}'"
    return run_kube_command(config["pod"]['name'], config["pod"]['container'], command)

def delete_table_configuration_values(config):
    """
    Delete the entries in the configuration table and make the table empty.

    Args:
        config (dict): db configuration dict

    Returns:
        boolean: return True if the command execution successful
    """
    sql_delete = "DELETE FROM configuration"
    command = f"/usr/bin/psql --host=localhost --port=5432 --dbname=sodb1 -U process_engine --command='{sql_delete}'"
    return run_kube_command(config["pod"]['name'], config["pod"]['container'], command)

def restore_databasechangelog_camunda_objects(config):
    """
    Restore the camunda objects DB dump

    Args:
        config (dict): db configuration dict

    Returns:
        boolean: return True if the command execution successful
    """
    command = "/usr/bin/psql --host=localhost --port=5432 --dbname=sodb1 -U process_engine < /tmp/lns_camunda_objects_7_14.sql"
    return run_kube_command(config["pod"]['name'], config["pod"]['container'], command)

def fix_changelog_seq_values(config):
    """
    Update the databasechangelog with the correct filename

    Args:
        config (dict): db configuration dict

    Returns:
        boolean: return True if the command execution successful
    """
    sql = "UPDATE databasechangelog SET filename='changelog-sodb1-sequences.xml' WHERE filename='./changelog-sodb1-sequences.xml';"
    command = f'/usr/bin/psql --host=localhost --port=5432 --dbname=sodb1 -U process_engine --command="{sql}"'
    return run_kube_command(config["pod"]['name'], config["pod"]['container'], command)