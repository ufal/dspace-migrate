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
