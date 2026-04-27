import logging
import os
import time

from ._utils import time_method

from ._handle import handles
from ._metadata import metadatas

from ._group import groups
from ._community import communities
from ._collection import collections
from ._registrationdata import registrationdatas
from ._eperson import epersons
from ._eperson import groups as eperson_groups
from ._userregistration import userregistrations
from ._bitstreamformatregistry import bitstreamformatregistry
from ._license import licenses
from ._item import items
from ._bundle import bundles
from ._bitstream import bitstreams
from ._resourcepolicy import resourcepolicies
from ._usermetadata import usermetadatas
from ._db import db, differ, tester
from ._sequences import sequences

_logger = logging.getLogger("pump.repo")


def export_table(db, table_name: str, out_f: str):
    try:
        from tqdm import tqdm
    except Exception:
        tqdm = None

    started = time.perf_counter()
    total_rows = db.fetch_one(f'SELECT COUNT(*) FROM "{table_name}"') or 0
    _logger.debug(f"[EXPORT] {table_name}: start rows={total_rows} -> {out_f}")

    chunk_size = 20000
    logged_step = 100000
    exported = 0
    first = True
    is_jsonl = out_f.lower().endswith(".jsonl")

    progress = None
    if tqdm is not None and total_rows > 0:
        progress = tqdm(
            total=total_rows,
            desc=f"export:{table_name}",
            unit="rows",
            mininterval=2,
            dynamic_ncols=True,
        )

    with open(out_f, 'w', encoding='utf-8') as fout:
        if not is_jsonl:
            fout.write('[')
        with db.cursor_context() as cursor:
            cursor.itersize = chunk_size
            cursor.execute(f'SELECT row_to_json(t)::text FROM "{table_name}" t')
            while True:
                rows = cursor.fetchmany(chunk_size)
                if not rows:
                    break

                serialized_rows = [row[0] for row in rows if row and row[0] is not None]
                if serialized_rows:
                    if is_jsonl:
                        fout.write("\n".join(serialized_rows))
                        fout.write("\n")
                    else:
                        chunk_text = ",".join(serialized_rows)
                        if first:
                            fout.write(chunk_text)
                            first = False
                        else:
                            fout.write(',')
                            fout.write(chunk_text)

                exported += len(serialized_rows)
                if progress is not None:
                    progress.update(len(serialized_rows))
                if progress is None and (exported % logged_step == 0 or exported == total_rows):
                    took = time.perf_counter() - started
                    speed = exported / took if took > 0 else 0
                    _logger.info(
                        f"[EXPORT] {table_name}: exported={exported}/{total_rows} rows elapsed={took:.1f}s speed={speed:.0f} rows/s"
                    )

        if not is_jsonl:
            fout.write(']')

    if progress is not None:
        progress.close()

    took = time.perf_counter() - started
    size_mb = os.path.getsize(out_f) / (1024 * 1024)
    speed = exported / took if took > 0 else 0
    _logger.info(
        f"[EXPORT] {table_name}: done rows={exported}/{total_rows} size={size_mb:.1f}MB elapsed={took:.1f}s speed={speed:.0f} rows/s"
    )


class repo:
    @time_method
    def __init__(self, env: dict, dspace):
        self.raw_db_dspace_5 = db(env["db_dspace_5"])
        self.raw_db_utilities_5 = db(env["db_utilities_5"])
        self.raw_db_7 = db(env["db_dspace_7"])

        if not env["tempdb"]:
            run_id = time.strftime("run_%Y%m%d_%H%M%S") + f"_{os.getpid()}"
            env["input"]["tempdbexport_v5"] = os.path.join(
                env["input"]["tempdbexport_v5"], run_id)
            env["input"]["tempdbexport_v7"] = os.path.join(
                env["input"]["tempdbexport_v7"], run_id)
            _logger.info(
                f"Using temp export dirs v5=[{env['input']['tempdbexport_v5']}] v7=[{env['input']['tempdbexport_v7']}]"
            )

        export_cache_v5 = {}
        export_cache_v7 = {}

        tables_db_5 = [x for arr in self.raw_db_dspace_5.all_tables() for x in arr]
        tables_utilities_5 = [x for arr in self.raw_db_utilities_5.all_tables()
                              for x in arr]

        def _f(table_name):
            """
                Dynamically export the table to JSON or,
                if its name is in env["test"], load configured test JSON file for testing instead.
            """
            if table_name in env.get("test", []):
                test_json_path = os.path.join(
                    env["input"]["test"], env["input"]["test_json_filename"])
                if not os.path.exists(test_json_path):
                    raise FileNotFoundError(f"Test JSON file not found: {test_json_path}")
                return test_json_path
            if table_name in export_cache_v5:
                _logger.info(f"[TABLE] {table_name}: reusing exported file")
                return export_cache_v5[table_name]

            os.makedirs(env["input"]["tempdbexport_v5"], exist_ok=True)
            ext = "jsonl" if table_name == "metadatavalue" else "json"
            out_f = os.path.join(env["input"]["tempdbexport_v5"], f"{table_name}.{ext}")
            if not env["tempdb"]:
                if table_name in tables_db_5:
                    db = self.raw_db_dspace_5
                elif table_name in tables_utilities_5:
                    db = self.raw_db_utilities_5
                else:
                    _logger.warning(f"Table [{table_name}] not found in db.")
                    raise NotImplementedError(f"Table [{table_name}] not found in db.")
                _logger.info(f"[TABLE] {table_name}: exporting")
                export_table(db, table_name, out_f)
            export_cache_v5[table_name] = out_f
            return out_f

        def _f_7(table_name):
            """ Dynamically export the table to json file and return path to it for DSpace 7. """
            if table_name in export_cache_v7:
                _logger.info(f"[TABLE] {table_name}@v7: reusing exported file")
                return export_cache_v7[table_name]

            os.makedirs(env["input"]["tempdbexport_v7"], exist_ok=True)
            out_f = os.path.join(env["input"]["tempdbexport_v7"], f"{table_name}.json")
            if not env["tempdb"]:
                _logger.info(f"[TABLE] {table_name}@v7: exporting")
                export_table(self.raw_db_7, table_name, out_f)
            export_cache_v7[table_name] = out_f
            return out_f

        self.groups = groups(
            _f("epersongroup"),
            _f("group2group"),
        )
        self.groups.from_rest(dspace)

        self.handles = handles(_f("handle"))

        self.metadatas = metadatas(
            env,
            dspace,
            _f_7("metadatafieldregistry"),
            _f_7("metadataschemaregistry"),
            _f("metadatavalue"),
            _f("metadatafieldregistry"),
            _f("metadataschemaregistry"),
        )

        self.communities = communities(
            _f("community"),
            _f("community2community"),
        )

        collection_default_read_groups = self.metadatas.collection_default_read_groups()

        self.collections = collections(
            _f("collection"),
            _f("community2collection"),
            None,
            collection_default_read_groups,
        )

        self.registrationdatas = registrationdatas(
            _f("registrationdata")
        )

        self.epersons = epersons(
            _f("eperson")
        )

        self.egroups = eperson_groups(
            _f("epersongroup2eperson")
        )

        self.userregistrations = userregistrations(
            _f("user_registration")
        )

        self.bitstreamformatregistry = bitstreamformatregistry(
            _f("bitstreamformatregistry"), _f("fileextension")
        )

        self.licenses = licenses(
            _f("license_label"),
            _f("license_definition"),
            _f("license_label_extended_mapping")
        )

        self.items = items(
            _f("item"),
            _f("workspaceitem"),
            _f("workflowitem"),
            _f("collection2item"),
        )

        self.bundles = bundles(
            _f("bundle"),
            _f("item2bundle"),
        )

        self.bitstreams = bitstreams(
            _f("bitstream"),
            _f("bundle2bitstream"),
        )

        self.usermetadatas = usermetadatas(
            _f("user_metadata"),
            _f("license_resource_user_allowance"),
            _f("license_resource_mapping")
        )

        self.resourcepolicies = resourcepolicies(
            _f("resourcepolicy")
        )

        self.sequences = sequences()

    def diff(self, to_validate=None):
        if to_validate is None:
            to_validate = [
                getattr(getattr(self, x), "validate_table")
                for x in dir(self) if hasattr(getattr(self, x), "validate_table")
            ]
        else:
            if not hasattr(to_validate, "validate_table"):
                _logger.warning(f"Missing validate_table in {to_validate}")
                return
            to_validate = [to_validate.validate_table]

        diff = differ(self.raw_db_dspace_5, self.raw_db_utilities_5,
                      self.raw_db_7, repo=self)
        diff.validate(to_validate)

    def test(self, to_test=None):
        if to_test is None:
            to_test = [
                getattr(getattr(self, x), "test_table")
                for x in dir(self) if hasattr(getattr(self, x), "test_table")
            ]
        else:
            if not hasattr(to_test, "test_table"):
                _logger.warning(f"Missing test_table in {to_test}")
                return
            to_test = [to_test.test_table]
        test = tester(self.raw_db_dspace_5, self.raw_db_utilities_5,
                      self.raw_db_7, repo=self)
        test.run_tests(to_test)

    # =====
    def uuid(self, res_type_id: int, res_id: int):
        # find object id based on its type
        try:
            if res_type_id == self.communities.TYPE:
                return self.communities.uuid(res_id)
            if res_type_id == self.collections.TYPE:
                return self.collections.uuid(res_id)
            if res_type_id == self.items.TYPE:
                return self.items.uuid(res_id)
            if res_type_id == self.bitstreams.TYPE:
                return self.bitstreams.uuid(res_id)
            if res_type_id == self.bundles.TYPE:
                return self.bundles.uuid(res_id)
            if res_type_id == self.epersons.TYPE:
                return self.epersons.uuid(res_id)
            if res_type_id == self.groups.TYPE:
                arr = self.groups.uuid(res_id)
                if len(arr or []) > 0:
                    return arr[0]
        except Exception as e:
            return None
        return None
