#!/usr/bin/python3

import argparse
import logging
import locale
from os import system, path

from CMMigrate import CmMigrate


#this can be run at the command line > python3 run_migrate.py --arguments - the function below will parse the arguments
#not sure how migrate_apply yaml (has playbooks and python scripts is brought in here

def parse_arguments():
    parser = argparse.ArgumentParser(description='Legacy To LnS Migration')
    phase_group = parser.add_mutually_exclusive_group(required=True)
    phase_group.add_argument('-p', '--prep', dest='phase_flag_prep', action='store_true', help='run prep phase')
    phase_group.add_argument('-a', '--apply', dest='phase_flag_apply', action='store_true', help='run apply phase')
    parser.add_argument('-b', '--baseenv-file', dest='baseenv_file', type=str, nargs='?', default='baseenv.migrate',
                        help='(optional) manually specify the baseenv file to use. (default: baseenv.migrate)')
    parser.add_argument('-o', '--option', dest='option', type=str, nargs='?', default=None,
                        help='(optional) directly run the specified option in the CLI without showing the menu. '
                             '(default: None)')
    parser.add_argument('-l', '--level', dest='validation_level', type=int, nargs='?', default=1,
                        help='Limits the validation levels to execute. (default: 1)',
                        choices=[0,1, 2, 3])
    log_settings_group = parser.add_mutually_exclusive_group(required=False)
    log_settings_group.add_argument('-d', '--debug', dest='debug', action='store_true', default=False,
                                    help='(optional) enable debug on-screen logging. (default: False)')
    log_settings_group.add_argument('-v', '--verbose', dest='verbose', action='store_true', default=False,
                                    help='(optional) enable verbose on-screen logging. (default: False)')
    args = parser.parse_args()

    return args


if __name__ == '__main__':
    # Initialize terminal colors
    system('')
    # set en.US.utf-8 locale
    locale.setlocale(locale.LC_ALL, 'en_US.utf8')
    _args = parse_arguments()
    base_folder = path.abspath('')

    # Required parameters
    if _args.phase_flag_prep:
        _phase = 'migrate_prep'
    elif _args.phase_flag_apply:
        _phase = 'migrate_apply'
    else:
        raise RuntimeError('Phase is not defined')

    # Optional parameters
    _baseenv_file = path.join(base_folder, _args.baseenv_file)
    _debug = _args.debug
    _verbose = _args.verbose
    _option = _args.option
    _level = _args.validation_level

    # Initialize run variables from input arguments
    _ansible_templates = [path.join(base_folder, 'ansible_templates'),
                          path.join(base_folder, 'migrate_folder', 'ecm_files')]
    _ansible_folder = path.join(base_folder, 'playbooks')
    _inventory_file = 'migrate.inventory'
    _menu_file = path.join(base_folder, 'menu', _phase + '.yaml')
    _library_packages = ['upgrade_functions','migrate_functions']
    _progress_bar = False if (_debug or _verbose) else True

    cm_migrate = CmMigrate(_phase,
                           _baseenv_file,
                           _ansible_templates,
                           _ansible_folder,
                           _library_packages,
                           progress_bar=_progress_bar,
                           log_folder=path.join(base_folder, 'logs'),
                           debug=_debug)

    # LOGGING
    if _debug:
        logging_level = 'DEBUG'
    elif _verbose:
        logging_level = CmMigrate.verbose_level
    else:
        logging_level = 'INFO'

    cm_migrate.configure_logging(logging_level)
    root_logger = logging.getLogger('')

    # Check baseenv syntax if validation not bypassed
    if _level > 0:
        cm_migrate.check_parse_and_validate_baseenv(level=_level)

    # Run the LnS Migration
    if _option:
        # IF OPTION SELECTED IN CLI, RUN OPTION
        result = cm_migrate.run_option(menu_file=_menu_file, selected_option=_option, inventory_file=_inventory_file,
                                       debug=_debug)
    else:
        # IF NO OPTION SELECTED IN CLI, RUN MENU
        result = cm_migrate.run_menu_loop(menu_file=_menu_file, inventory_file=_inventory_file, debug=_debug)

    if not result:
        exit(1)