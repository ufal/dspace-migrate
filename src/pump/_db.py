import os
import sys
import logging
_logger = logging.getLogger("pump.db")


class conn:
    def __init__(self, env):
        self.name = env["name"]
        self.host = env["host"]
        self.user = env["user"]
        self.port = env.get("port", 5432)
        self.password = env["password"]
        self._conn = None
        self._cursor = None

    def connect(self):
        if self._conn is not None:
            return

        import psycopg2  # noqa
        self._conn = psycopg2.connect(
            database=self.name, host=self.host, port=self.port, user=self.user, password=self.password)
        _logger.debug(f"Connection to database [{self.name}] successful!")

    def __del__(self):
        self.close()

    def __enter__(self):
        self.connect()
        self._cursor = self._conn.cursor()
        return self._cursor

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            _logger.critical(
                f"An exception of type {exc_type} occurred with message: {exc_value}")
            return
        self._conn.commit()
        return self._cursor.close()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


class db:
    """
        TODO(jm): working but should be refactored, with semantics
    """

    def __init__(self, env: dict):
        self._conn = conn(env)

    # =============

    def fetch_all(self, sql: str, col_names: list = None):
        with self._conn as cursor:
            cursor.execute(sql)
            arr = cursor.fetchall()
            if col_names is not None:
                col_names += [x[0] for x in cursor.description]
            return arr

    def fetch_one(self, sql: str):
        with self._conn as cursor:
            cursor.execute(sql)
            res = cursor.fetchone()
            if res is None:
                return None

            return res[0]

    def exe_sql(self, sql_text: str):
        with self._conn as cursor:
            sql_lines = [x.strip() for x in sql_text.splitlines() if len(x.strip()) > 0]
            for sql in sql_lines:
                cursor.execute(sql)
            return

    # =============

    def delete_resource_policy(self):
        with self._conn as cursor:
            expected = self.fetch_one("SELECT COUNT(*) from public.resourcepolicy")

            # delete all data
            cursor.execute("DELETE FROM public.resourcepolicy")
            deleted = cursor.rowcount

        # control, if we deleted all data
        if expected != deleted:
            _logger.critical(
                f"Did not remove all entries from resourcepolicy table. Expected: {expected}, deleted: {deleted}")
            sys.exit(1)

    def get_admin_uuid(self, username):
        """
            Get uuid of the admin user
        """
        res = self.fetch_one(f"SELECT uuid FROM eperson WHERE email like '{username}'")

        # Check if there is a result and extract the ID
        if res is not None:
            return res

        _logger.error(f"No eperson records in the table for {username}")
        return None

    def get_last_id(self, table_name, id_column):
        """
            Get id of the last record from the specific table
            @return: id of the last record
        """
        sql = f"SELECT {id_column} FROM {table_name} ORDER BY {id_column} DESC LIMIT 1"
        last_record_id = self.fetch_one(sql)

        if not last_record_id:
            _logger.info(f"No records in [{table_name}] table.")
            # Default value - the table is empty
            return 1

        # Check if there is a result and extract the ID
        return last_record_id

    def all_tables(self):
        return self.fetch_all(
            "SELECT table_name FROM information_schema.tables WHERE is_insertable_into = 'YES' AND table_schema = 'public'")

    def table_count(self):
        d = {}
        tables = self.all_tables()
        for table in tables:
            name = table[0]
            # Use double quotes for table names because some of them are in uppercase.
            count = self.fetch_one(f"SELECT COUNT(*) FROM \"{name}\"")
            d[name] = count
        return d

    def status(self):
        d = self.table_count()
        zero = ""
        msg = ""
        for name in sorted(d.keys()):
            count = d[name]
            if count == 0:
                zero += f"{name},"
            else:
                msg += f"{name: >40}: {int(count): >8d}\n"

        _logger.info(f"\n{msg}Empty tables:\n\t{zero}")
        _logger.info(40 * "=")


class differ:

    def __init__(self, raw_db_dspace_5, raw_db_utilities_5, raw_db_7, repo=None):
        """
            Repo object might be needed by `"process":` to be able to compare values.
        """
        self.raw_db_dspace_5 = raw_db_dspace_5
        self.raw_db_utilities_5 = raw_db_utilities_5
        self.raw_db_7 = raw_db_7
        self._repo = repo

    def _fetch_all_vals(self, db5, table_name: str, sql: str = None):
        sql = sql or f"SELECT * FROM {table_name}"
        cols5 = []
        db5 = db5 or self.raw_db_dspace_5
        vals5 = db5.fetch_all(sql, col_names=cols5)
        cols7 = []
        vals7 = self.raw_db_7.fetch_all(sql, col_names=cols7)
        return cols5, vals5, cols7, vals7

    def _filter_vals(self, vals, col_names, only_names):
        idxs = [col_names.index(x) for x in only_names]
        filtered = []
        for row in vals:
            filtered.append([row[idx] for idx in idxs])
        return filtered

    def _cmp_values(self, table_name: str, vals5, only_in_5, vals7, only_in_7, do_not_show: bool):
        too_many_5 = ""
        too_many_7 = ""
        LIMIT = 5
        if len(only_in_5) > LIMIT:
            too_many_5 = f"!!! TOO MANY [{len(only_in_5)}] "
        if len(only_in_7) > LIMIT:
            too_many_7 = f"!!! TOO MANY [{len(only_in_7)}] "

        do_not_show = do_not_show or "CI" in os.environ or "GITHUB_ACTION" in os.environ
        # assume we do not have emails that we do not want to show in db7
        if do_not_show:
            only_in_5 = [x if "@" not in x else "....." for x in only_in_5]
            only_in_7 = [x if "@" not in x else "....." for x in only_in_7]

        _logger.info(f"Table [{table_name}]: v5:[{len(vals5)}], v7:[{len(vals7)}]\n"
                     f"  {too_many_5}only in v5:[{only_in_5[:LIMIT]}]\n"
                     f"  {too_many_7}only in v7:[{only_in_7[:LIMIT]}]")

    def diff_table_cmp_cols(self, db5, table_name: str, compare_arr: list, gdpr: bool = True):
        cols5, vals5, cols7, vals7 = self._fetch_all_vals(db5, table_name)
        do_not_show = gdpr and "email" in compare_arr

        filtered5 = self._filter_vals(vals5, cols5, compare_arr)
        vals5_cmp = ["|".join(str(x) for x in x) for x in filtered5]
        filtered7 = self._filter_vals(vals7, cols7, compare_arr)
        vals7_cmp = ["|".join(str(x) for x in x) for x in filtered7]

        only_in_5 = list(set(vals5_cmp).difference(vals7_cmp))
        only_in_7 = list(set(vals7_cmp).difference(vals5_cmp))
        if len(only_in_5) + len(only_in_7) == 0:
            _logger.info(f"Table [{table_name: >20}] is THE SAME in v5 and v7!")
            return
        self._cmp_values(table_name, vals5, only_in_5, vals7, only_in_7, do_not_show)

    def diff_table_cmp_len(self, db5, table_name: str, nonnull: list = None, gdpr: bool = True, sql: str = None):
        nonnull = nonnull or []
        sql_info = False
        cols5, vals5, cols7, vals7 = self._fetch_all_vals(db5, table_name)
        do_not_show = gdpr and "email" in nonnull

        if len(vals5) != len(vals7) and sql is not None:
            cols5, vals5, cols7, vals7 = self._fetch_all_vals(db5, table_name, sql)
            sql_info = True

        msg = " OK " if len(vals5) == len(vals7) else " !!! WARN !!! "
        _logger.info(
            f"Table [{table_name: >20}] {msg} compared by len only v5:[{len(vals5)}], v7:[{len(vals7)}]")

        for col_name in nonnull:
            vals5_cmp = [x for x in self._filter_vals(
                vals5, cols5, [col_name]) if x[0] is not None]
            vals7_cmp = [x for x in self._filter_vals(
                vals7, cols7, [col_name]) if x[0] is not None]

            msg = " OK " if len(vals5_cmp) == len(vals7_cmp) else " !!! WARN !!! "
            _logger.info(
                f"Table [{table_name: >20}] {msg}  NON NULL [{col_name:>15}] v5:[{len(vals5_cmp):3}], v7:[{len(vals7_cmp):3}]")

        if sql_info:
            _logger.info(
                f"Table [{table_name: >20}]  !!! WARN !!!  SQL request: {sql}")

    def diff_table_sql(self, db5, table_name: str, sql5, sql7, compare, process_ftor):
        cols5 = []
        vals5 = db5.fetch_all(sql5, col_names=cols5)
        cols7 = []
        vals7 = self.raw_db_7.fetch_all(sql7, col_names=cols7)
        # special case where we have different names of columns but only one column to compare
        if compare == 0:
            vals5_cmp = [x[0] for x in vals5 if x[0] is not None]
            vals7_cmp = [x[0] for x in vals7 if x[0] is not None]
        elif compare is None:
            vals5_cmp = vals5
            vals7_cmp = vals7
        else:
            vals5_cmp = [x[0] for x in self._filter_vals(
                vals5, cols5, [compare]) if x[0] is not None]
            vals7_cmp = [x[0] for x in self._filter_vals(
                vals7, cols7, [compare]) if x[0] is not None]

        if process_ftor is not None:
            vals5_cmp, vals7_cmp = process_ftor(self._repo, vals5_cmp, vals7_cmp)
            # ignored
            if vals5_cmp is None and vals7_cmp is None:
                return

        only_in_5 = list(set(vals5_cmp).difference(vals7_cmp))
        only_in_7 = list(set(vals7_cmp).difference(vals5_cmp))
        self._cmp_values(table_name, vals5, only_in_5, vals7, only_in_7, False)

    def validate(self, to_validate):
        for valid_defs in to_validate:
            for table_name, defin in valid_defs:
                _logger.info("=" * 10 + f" Validating {table_name} " + "=" * 10)
                db5_name = defin.get("db", "clarin-dspace")
                db5 = self.raw_db_dspace_5 if db5_name == "clarin-dspace" else self.raw_db_utilities_5

                cmp = defin.get("compare", None)
                if cmp is not None:
                    self.diff_table_cmp_cols(db5, table_name, cmp)

                cmp = defin.get("nonnull", None)
                if cmp is not None:
                    self.diff_table_cmp_len(db5, table_name, cmp)

                # compare only len
                if len(defin) == 0:
                    self.diff_table_cmp_len(db5, table_name)

                cmp = defin.get("len", None)
                if cmp is not None:
                    self.diff_table_cmp_len(db5, table_name, None, True, cmp["sql"])

                cmp = defin.get("sql", None)
                if cmp is not None:
                    self.diff_table_sql(
                        db5, table_name, cmp["5"], cmp["7"], cmp["compare"], cmp.get("process", None))
