import sys
import argparse
import logging
import os
import json
from tqdm import tqdm

logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger()

_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_this_dir, "../src/"))


def get_data_as_json(db, out_dir: str):
    table_names = db.all_tables()

    os.makedirs(out_dir, exist_ok=True)
    _logger.info(f"Exporting data to {out_dir}")
    for table in tqdm(table_names):
        # access to 0. position, because name_t is tuple
        name = table[0]
        file_name = os.path.join(out_dir, name + ".json")
        with open(file_name, 'w', encoding='utf-8') as fout:
            js = db.fetch_one(f'SELECT json_agg(row_to_json(t)) FROM "{name}" t')
            json.dump(js, fout)

    _logger.info("Data successfully exported!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process database connection')
    parser.add_argument('--database', help='database name', required=True, type=str)
    parser.add_argument('--port', help='port', type=int, default=5432)
    parser.add_argument('--host', help='type of host', type=str, default="localhost")
    parser.add_argument('--user', help='database user', type=str)
    parser.add_argument('--password', help='database password', type=str)
    parser.add_argument('--output', help='output dir', type=str,
                        default=os.path.join(_this_dir, "../input/data"))
    args = parser.parse_args()

    if args.user is None:
        from project_settings import settings
        db_dspace_5 = settings["db_dspace_5"]
        db_utilities_5 = settings["db_utilities_5"]
        if args.database == db_dspace_5["name"]:
            db = db_dspace_5
        elif args.database == db_utilities_5["name"]:
            db = db_utilities_5
        else:
            _logger.error("Unknown database, support username and password!")
            sys.exit(1)
        args.user = db["user"]
        args.password = db["password"]
        args.host = db["host"]

    from pump import db
    dspace5 = db({
        "name": args.database,
        "host": args.host,
        "user": args.user,
        "port": args.port,
        "password": args.password,
    })

    get_data_as_json(dspace5, args.output)
