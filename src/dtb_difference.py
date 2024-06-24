import logging

import settings
import tul_settings
from utils import init_logging, update_settings

_logger = logging.getLogger()

env = update_settings(settings.env, tul_settings.settings)
init_logging(_logger, env["log_file"])

import dspace  # noqa
import pump


def difference_dtb(old_dtb: dict, new_dtb: dict):
    msg = ""
    no_exist7 = ""
    no_exist5 = ""
    for name in sorted(old_dtb.keys()):
        if name not in new_dtb:
            no_exist7 += f"{name},"
        else:
            difference = int(new_dtb[name]) - int(old_dtb[name])
            result = "surplus " if difference > 0 else (
                "deficit " if difference < 0 else "")
            msg += f"{name: >40}: {int(difference): >8d} {result}\n"
        del new_dtb[name]
    for name in sorted(new_dtb.keys()):
        no_exist5 += f"{name},"
    _logger.info(
        f"\n{msg}Nonexistent tables in DSpace 7:\n\t{no_exist7}\nNonexistent tables in DSpace 5:\n\t{no_exist5}")
    _logger.info(40 * "=")


if __name__ == "__main__":
    _logger.info("Loading repo objects")

    _logger.info("Database difference:")
    raw_db_7 = pump.db(env["db_dspace_7"])
    raw_db_tul = pump.db(env["db_tul"])
    difference_dtb(raw_db_tul.table_count(), raw_db_7.table_count())
