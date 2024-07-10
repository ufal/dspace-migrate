import sys
import os
import logging
import argparse
import importlib

_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_this_dir, "../../src"))

import settings
import project_settings
from utils import init_logging, update_settings

_logger = logging.getLogger()

env = update_settings(settings.env, project_settings.settings)
init_logging(_logger, env["log_file"])

import dspace  # noqa


def table_diff(old_db_d: dict, new_db_d: dict):
    """
      Compare the counts of data in two databases and log the differences.

      The result is based on the counts in new_db_d.
      Example: If table1 in old_db_d has 6 items and new_db_d has 5 items, the output is:
          table1:     1 deficit -> because in new_db_d there is 1 item missing (based on count)
      If table1 in old_db_d has 6 items and new_db_d has 7 items, the output is:
          table1:     1 surplus -> because in new_db_d there is 1 item more (based on count)
      If the counts are equal, the output is:
          table1:     0
      """
    msg = ""
    no_exist7 = []
    for name in sorted(old_db_d.keys()):
        if name not in new_db_d:
            no_exist7.append(name)
            continue
        dif = int(new_db_d[name]) - int(old_db_d[name])
        result = "more " if dif > 0 else ("less " if dif < 0 else "")
        msg += f"{name: >40}: {dif: >8d} {result}\n"
        del new_db_d[name]

    no_exist_old = sorted(new_db_d.keys())

    _logger.info(
        f"\n{msg}Missing tables in v7: {', '.join(no_exist7)}\nCount: {len(no_exist7)}"
        f"\nMissing tables in v5/6: {', '.join(no_exist_old)}\nCount: {len(no_exist_old)}")
    _logger.info(40 * "=")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Diff databases before/after import')
    parser.add_argument('--use', help='Instance to diff', required=True, type=str)
    args = parser.parse_args()

    # update settings with selected one from the command line
    try:
        use_settings_m = importlib.import_module(f"diff_settings.{args.use}")
    except Exception as e:
        _logger.error(f"Unknown instance [{args.use}]")
        sys.exit(1)
    use_env = use_settings_m.settings
    # hack v6 because it is the same as v5 for this context and
    # repo uses `db_dspace_5` to connect to the database
    if "db_dspace_6" in use_env:
        use_env["db_dspace_5"] = use_env["db_dspace_6"]
    # override settings with selected instance
    env.update(use_env)

    from pump._db import db, differ
    from pump._handle import handles
    from pump._bitstreamformatregistry import bitstreamformatregistry
    from pump._community import communities
    from pump._collection import collections
    from pump._registrationdata import registrationdatas
    from pump._userregistration import userregistrations
    from pump._item import items
    from pump._bundle import bundles
    from pump._bitstream import bitstreams

    raw_db_7 = db(env["db_dspace_7"])
    raw_db_old_key = "db_dspace_6" if "db_dspace_6" in use_env else \
        "db_dspace_5"
    raw_db_dspace_old = db(env[raw_db_old_key])

    # table diff
    table_diff(raw_db_dspace_old.table_count(), raw_db_7.table_count())

    # value/count diff
    diff = differ(raw_db_dspace_old, None, raw_db_7)

    diff.validate([handles.validate_table])
    diff.validate([bitstreamformatregistry.validate_table])
    diff.validate([communities.validate_table])
    diff.validate([collections.validate_table])
    diff.validate([registrationdatas.validate_table])
    diff.validate([userregistrations.validate_table])

    diff.validate([items.validate_table])
    diff.validate([bundles.validate_table])
    diff.validate([bitstreams.validate_table])
