#!/usr/bin/python3
#
# INSTRUCTIONS
# cd /ecm-umi/upgrade/ecm
#
# For coverage report:
# pytest --cov-report term --cov=migrate_functions/ migrate_functions/
#
# To view missing coverage:
# pytest --cov-report term-missing --cov=migrate_functions/ migrate_functions/

import json
from unittest.mock import patch


@patch("migrate_functions.migrate_nso_data.run_kube_command", return_value=True)
def test_nso_save_databasechangelog_camunda_objects(run_kube_command):
    """
    Test the save_databasechangelog_camunda_objects function
    """
    # Given
    from migrate_functions.migrate_nso_data import save_databasechangelog_camunda_objects
    config_file = json.load(open('./migrate_schemas/db_config.json', ))
    nso_config = config_file['nso']
    expected_value = True

    # When
    actual_value = save_databasechangelog_camunda_objects(nso_config)

    # Then
    assert expected_value == actual_value


@patch("migrate_functions.migrate_nso_data.run_kube_command", return_value=True)
def test_dump_databasechangelog_camunda_objects(run_kube_command):
    """
    Test the dump_databasechangelog_camunda_objects function
    """
    # Given
    from migrate_functions.migrate_nso_data import dump_databasechangelog_camunda_objects
    config_file = json.load(open('./migrate_schemas/db_config.json', ))
    nso_config = config_file['nso']
    expected_value = True

    # When
    actual_value = dump_databasechangelog_camunda_objects(nso_config)

    # Then
    assert expected_value == actual_value


@patch("migrate_functions.migrate_nso_data.run_kube_command", return_value=True)
def test_backup_table_configuration_values(run_kube_command):
    """
    Test the backup_table_configuration_values function
    """
    # Given
    from migrate_functions.migrate_nso_data import backup_table_configuration_values
    config_file = json.load(open('./migrate_schemas/db_config.json', ))
    nso_config = config_file['nso']
    expected_value = True

    # When
    actual_value = backup_table_configuration_values(nso_config)

    # Then
    assert expected_value == actual_value


@patch("migrate_functions.migrate_nso_data.run_kube_command", return_value=True)
def test_delete_table_configuration_values(run_kube_command):
    """
    Test the delete_table_configuration_values function
    """
    # Given
    from migrate_functions.migrate_nso_data import delete_table_configuration_values
    config_file = json.load(open('./migrate_schemas/db_config.json', ))
    nso_config = config_file['nso']
    expected_value = True

    # When
    actual_value = delete_table_configuration_values(nso_config)

    # Then
    assert expected_value == actual_value


@patch("migrate_functions.migrate_nso_data.run_kube_command", return_value=True)
def test_restore_databasechangelog_camunda_objects(run_kube_command):
    """
    Test the restore_databasechangelog_camunda_objects function
    """
    # Given
    from migrate_functions.migrate_nso_data import restore_databasechangelog_camunda_objects
    config_file = json.load(open('./migrate_schemas/db_config.json', ))
    nso_config = config_file['nso']
    expected_value = True

    # When
    actual_value = restore_databasechangelog_camunda_objects(nso_config)

    # Then
    assert expected_value == actual_value


@patch("migrate_functions.migrate_nso_data.run_kube_command", return_value=True)
def test_fix_changelog_seq_values(run_kube_command):
    """
    Test the fix_changelog_seq_values function
    """
    # Given
    from migrate_functions.migrate_nso_data import fix_changelog_seq_values
    config_file = json.load(open('./migrate_schemas/db_config.json', ))
    nso_config = config_file['nso']
    expected_value = True

    # When
    actual_value = fix_changelog_seq_values(nso_config)

    # Then
    assert expected_value == actual_value