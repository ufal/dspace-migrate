import os
import sys
import logging
import time

from ._db_config import *
from typing import Optional

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
        if self._conn is not None and not getattr(self._conn, 'closed', True):
            return

        import psycopg2  # noqa
        try:
            self._conn = psycopg2.connect(
                database=self.name, host=self.host, port=self.port, user=self.user, password=self.password,
                connect_timeout=DB_CONNECT_TIMEOUT,
                keepalives_idle=DB_KEEPALIVES_IDLE,
                keepalives_interval=DB_KEEPALIVES_INTERVAL,
                keepalives_count=DB_KEEPALIVES_COUNT
            )
        except Exception as e:
            _logger.error(f"Failed to connect to database [{self.name}]: {e}")
            raise

    def __del__(self):
        self.close()

    def __enter__(self):
        self.connect()
        self._cursor = self._conn.cursor()
        return self._cursor

    def __exit__(self, exc_type, exc_value, traceback):
        if self._cursor:
            self._cursor.close()
            self._cursor = None
        if exc_type is not None:
            _logger.critical(
                f"An exception of type {exc_type} occurred with message: {exc_value}")
            if self._conn and not getattr(self._conn, 'closed', True):
                self._conn.rollback()
            return
        if self._conn and not getattr(self._conn, 'closed', True):
            self._conn.commit()

    def close(self):
        if self._cursor:
            self._cursor.close()
            self._cursor = None
        if self._conn and not getattr(self._conn, 'closed', True):
            self._conn.close()
            self._conn = None

    def is_connected(self):
        """Check if the connection is still active"""
        try:
            if self._conn is None or self._conn.closed:
                return False
            # Test the connection with a simple query
            with self._conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return True
        except Exception:
            return False

    def reconnect(self):
        """Force reconnection to the database"""
        self.close()
        self.connect()


class db:
    """
        TODO(jm): working but should be refactored, with semantics
    """

    def __init__(self, env: dict):
        self._conn = conn(env)

    def _ensure_connection(self, attempt: int, max_retries: int):
        if self._conn.is_connected():
            return

        had_previous_connection = self._conn._conn is not None
        if had_previous_connection:
            _logger.warning(
                f"Database connection dropped, reconnecting... (attempt {attempt + 1}/{max_retries})"
            )
        else:
            _logger.info(
                f"Connecting to database [{self._conn.name}] at {self._conn.host}:{self._conn.port} "
                f"(attempt {attempt + 1}/{max_retries})"
            )
        self._conn.reconnect()

    def cursor_context(self):
        """Return the underlying connection context manager (yields a cursor)."""
        return self._conn

    def _exponential_backoff_sleep(self, attempt: int):
        """Calculate and perform exponential backoff sleep with max delay limit."""
        delay = DB_RETRY_BASE_DELAY * (2 ** attempt)
        # Cap the delay at DB_RETRY_MAX_DELAY to prevent excessive wait times
        delay = min(delay, DB_RETRY_MAX_DELAY)
        time.sleep(delay)

    # =============

    def fetch_all(self, sql: str, col_names: list = None, chunk_size: int = None):
        """Fetch all results with optional chunking and retry logic"""
        max_retries = DB_MAX_RETRIES

        for attempt in range(max_retries):
            try:
                self._ensure_connection(attempt, max_retries)

                if chunk_size:
                    return self._fetch_all_chunked(sql, col_names, chunk_size)
                else:
                    return self._fetch_all_simple(sql, col_names)

            except Exception as e:
                if attempt == max_retries - 1:
                    _logger.warning(
                        f"Database operation failed after {max_retries} attempts: {e}")
                    raise

                # Reconnect immediately if it's a connection-related error
                if "connection" in str(e).lower() or "abort" in str(e).lower():
                    self._conn.reconnect()

                self._exponential_backoff_sleep(attempt)

    def _fetch_all_simple(self, sql: str, col_names: list = None):
        """Simple fetch all without chunking"""
        with self._conn as cursor:
            cursor.execute(sql)
            arr = cursor.fetchall()
            if col_names is not None:
                col_names += [x[0] for x in cursor.description]
            return arr

    def _fetch_all_chunked(self, sql: str, col_names: list = None, chunk_size: int = None):
        """Fetch all results in chunks to avoid memory issues and connection timeouts"""
        chunk_size = chunk_size or DB_CHUNK_SIZE

        # Check if SQL already has LIMIT/OFFSET - if so, use simple fetch
        sql_upper = sql.upper()
        if 'LIMIT' in sql_upper or 'OFFSET' in sql_upper:
            return self._fetch_all_simple(sql, col_names)

        # First, get total count for progress tracking
        count_sql = self._optimize_count_query(sql)
        total_count = self.fetch_one(count_sql)
        if total_count > DB_LARGE_TABLE_THRESHOLD:  # Only log for large tables
            _logger.info(f"Chunking large table: {total_count} rows")

        # Fetch data in chunks using LIMIT and OFFSET
        all_results = []
        offset = 0
        chunk_num = 0

        while True:
            chunk_sql = f"{sql} LIMIT {chunk_size} OFFSET {offset}"

            with self._conn as cursor:
                cursor.execute(chunk_sql)
                chunk_results = cursor.fetchall()

                # Get column names from first chunk
                if col_names is not None and chunk_num == 0:
                    col_names += [x[0] for x in cursor.description]

                if not chunk_results:
                    break

                all_results.extend(chunk_results)
                chunk_num += 1
                offset += chunk_size

                # Add a small delay to prevent overwhelming the database
                time.sleep(DB_CHUNK_DELAY)

        return all_results

    def _optimize_count_query(self, sql: str) -> str:
        """
        Optimize count query by avoiding subquery when possible.
        For simple SELECT queries, extract table name and use direct COUNT.
        Fall back to subquery for complex queries.
        """
        import re

        sql_clean = sql.strip()
        sql_upper = sql_clean.upper()

        # Only optimize simple SELECT queries without complex clauses
        if not sql_upper.startswith('SELECT'):
            return f"SELECT COUNT(*) FROM ({sql}) AS count_query"

        # Skip optimization for complex queries
        complex_keywords = ['UNION', 'GROUP BY', 'HAVING', 'DISTINCT', 'CTE', 'WITH']
        if any(keyword in sql_upper for keyword in complex_keywords):
            return f"SELECT COUNT(*) FROM ({sql}) AS count_query"

        # Try to extract table name from simple SELECT queries
        # Pattern: SELECT ... FROM table_name [WHERE ...] [ORDER BY ...]
        from_match = re.search(
            r'\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?)', sql_upper)

        if from_match:
            table_name = from_match.group(1)

            # Check if there's a WHERE clause to preserve
            where_match = re.search(
                r'\bWHERE\b(.*)(?:\bORDER\s+BY\b|\bLIMIT\b|\bOFFSET\b|$)', sql_upper)

            if where_match:
                where_clause = where_match.group(1).strip()
                # Remove ORDER BY, LIMIT, OFFSET from WHERE clause if they got captured
                where_clause = re.sub(
                    r'\b(ORDER\s+BY|LIMIT|OFFSET)\b.*$', '', where_clause).strip()
                if where_clause:
                    return f"SELECT COUNT(*) FROM {table_name} WHERE {where_clause}"

            # Simple table query without WHERE
            return f"SELECT COUNT(*) FROM {table_name}"

        # Fall back to subquery if we can't parse the table name
        return f"SELECT COUNT(*) FROM ({sql}) AS count_query"

    def fetch_one(self, sql: str):
        max_retries = DB_MAX_RETRIES

        for attempt in range(max_retries):
            try:
                # Check connection health before executing
                self._ensure_connection(attempt, max_retries)

                with self._conn as cursor:
                    cursor.execute(sql)
                    res = cursor.fetchone()
                    if res is None:
                        return None
                    return res[0]

            except Exception as e:
                if attempt == max_retries - 1:
                    _logger.warning(
                        f"Database operation failed after {max_retries} attempts: {e}")
                    raise

                # Reconnect immediately if it's a connection-related error
                if "connection" in str(e).lower() or "abort" in str(e).lower():
                    self._conn.reconnect()

                self._exponential_backoff_sleep(attempt)

    def exe_sql(self, sql_text: str, params: Optional[dict] = None):
        """Execute SQL statement(s) with optional parameters.

        Args:
            sql_text: SQL to execute
            params: Optional dict of named parameters for parameterized query.
                   Only dict format is supported (e.g., {'name': 'value'} for %(name)s placeholders).
                   Positional parameters (tuple/list) are not supported.

        Behavior:
            - With params: Performs a single cursor.execute(sql_text, params) call. If sql_text
              contains multiple statements, execution behavior is determined by the database/driver.
            - Without params: Splits sql_text into non-empty lines and executes each line as a
              separate statement.
        """
        max_retries = DB_MAX_RETRIES

        for attempt in range(max_retries):
            try:
                self._ensure_connection(attempt, max_retries)

                with self._conn as cursor:
                    if params is not None:
                        # Execute parameterized query (single statement)
                        cursor.execute(sql_text, params)
                    else:
                        # Execute multiple statements (original behavior)
                        sql_lines = [x.strip()
                                     for x in (sql_text or "").splitlines() if x.strip()]
                        for sql in sql_lines:
                            cursor.execute(sql)
                return

            except Exception as e:
                _logger.warning(
                    f"Database exe_sql failed (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    raise

                # Reconnect immediately if it's a connection-related error
                if "connection" in str(e).lower() or "abort" in str(e).lower():
                    self._conn.reconnect()

                self._exponential_backoff_sleep(attempt)

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

    def table_count(self, exact: bool = True):
        """Return {table_name: row_count} for all user tables.

        When *exact* is True (default), issues COUNT(*) per table for
        accurate results.
        Set *exact=False* to use pg_stat_user_tables.n_live_tup
        (faster but may be stale).
        """
        if not exact:
            sql = (
                "SELECT relname, COALESCE(n_live_tup, 0) "
                "FROM pg_stat_user_tables "
                "ORDER BY relname"
            )
            rows = self.fetch_all(sql) or []
            return {name: int(cnt or 0) for name, cnt in rows}

        d = {}
        tables = self.all_tables()
        for table in tables:
            name = table[0]
            # Use double quotes for table names because some of them are in uppercase.
            count = self.fetch_one(f"SELECT COUNT(*) FROM \"{name}\"")
            d[name] = count
        return d

    def status(self, exact: bool = True):
        d = self.table_count(exact=exact)
        zero = ""
        msg = ""
        for name in sorted(d.keys()):
            count = d[name]
            if count == 0:
                zero += f"{name},"
            else:
                msg += f"{name: >40}: {int(count): >8d}\n"

        approx_text = "(estimated counts)" if not exact else "(exact counts)"
        _logger.info(f"\n{msg}Empty tables:\n\t{zero}\nStatus mode: {approx_text}")
        _logger.info(40 * "=")


class tester:
    """
        A class for running tests by comparing two parts, processing them based on their type.

        Test example:
            {
                "name": [TEST NAME],
                "left": [LEFT PART OF TEST],
                "right": [RIGHT PART OF TEST],
                "compare": [TYPE OF COMPARING -> =, <, >, default is = ]
            }

        Part structure example:
            TYPES -> sql, val
            For "sql":
                ["sql", [DATABASE -> dspace5, utilities5, db7], [FETCH -> one, all], [SELECT QUERY]]
            For "val":
                ["val", [VALUE]]
    """

    def __init__(self, raw_db_dspace_5, raw_db_utilities_5, raw_db_7, repo=None):
        """
            Repo object might be needed by `"process":` to be able to compare values.
        """
        self.raw_db_dspace_5 = raw_db_dspace_5
        self.raw_db_utilities_5 = raw_db_utilities_5
        self.raw_db_7 = raw_db_7
        self._repo = repo
        self._test_name_width = 42

    def _test_label(self, test_name: str) -> str:
        return f"{str(test_name or 'Test'):>{self._test_name_width}}"

    @staticmethod
    def get_list_val(part: list, pos: int):
        if part is not None:
            if pos < 0:
                return None
            if 0 <= pos < len(part):
                return part[pos]
        return None

    @staticmethod
    def log_error(msg: str, test_n: str, part_type: str = None) -> list:
        _logger.error(f"Test [{test_n}] [{part_type}]: {msg}")
        return []

    def process(self, test_n: str, part: list, part_type: str):
        """
            Processes a test part based on its type.
        """

        # Determine the type and fetch values accordingly
        part_val = self.get_list_val(part, 0)

        if part_val == "sql":
            db_type = self.get_list_val(part, 1)
            db = {
                "dspace5": self.raw_db_dspace_5,
                "utilities5": self.raw_db_utilities_5,
                "db7": self.raw_db_7
            }.get(db_type)

            if not db:
                self.log_error("Invalid db!", test_n, part_type)
                return

            sql = self.get_list_val(part, 3)
            if sql:
                fetch_type = self.get_list_val(part, 2)
                if fetch_type == "one":
                    return db.fetch_one(sql)
                elif fetch_type == "all":
                    return db.fetch_all(sql, self.get_list_val(part, 4))
                else:
                    self.log_error("Invalid fetch option!", test_n, part_type)
                    return
            else:
                self.log_error("Invalid sql!", test_n, part_type)
                return

        elif part_val == "val":
            return self.get_list_val(part, 1)

        self.log_error("Invalid type!", test_n, part_type)
        return

    def run_tests(self, tests: list):
        """
        Iterates over a list of test groups and runs each test.
        """
        for test_group in tests:
            for test in test_group:
                self.run_test(test)

    def run_test(self, test: dict):
        """
            Executes a test by comparing its two parts.
            If the comparison is valid, it logs the result as "OK", otherwise "FAILED."
        """
        test_n = test.get("name", "Test")
        part_l = test.get("left")
        part_r = test.get("right")

        msg = "Incorrect executed part!"
        if not part_l:
            self.log_error(msg, test_n, "left")
            return
        elif not part_r:
            self.log_error(msg, test_n, "right")
            return

        vals_l = self.process(test_n, part_l, "left")
        vals_r = self.process(test_n, part_r, "right")

        # Error msg is already logged
        if vals_l is None or vals_r is None:
            _logger.error(f"Test [{self._test_label(test_n)}]: FAILED")
            return

        compare = test.get("compare", "=")
        ok = False
        comparison_operations = {
            "=": vals_l == vals_r,
            ">": vals_l > vals_r,
            "<": vals_l < vals_r,
        }

        if compare in comparison_operations:
            ok = comparison_operations[compare]
        else:
            _logger.error(
                f"Test [{self._test_label(test_n)}]: Invalid comparison operator!")

        if ok:
            _logger.info(f"Test [{self._test_label(test_n)}]: OK")
        else:
            _logger.error(f"Test [{self._test_label(test_n)}]: FAILED")


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

        _logger.debug(f"Fetching data from {table_name}...")

        # Use chunked fetching for large tables (>100k estimated rows)
        # Check estimated row count first
        try:
            count_sql = f"SELECT COUNT(*) FROM {table_name}"
            row_count = db5.fetch_one(count_sql)
            use_chunking = row_count and row_count > DB_LARGE_TABLE_THRESHOLD
            chunk_size = DB_CHUNK_SIZE if use_chunking else None

            if use_chunking:
                _logger.info(
                    f"Large table {table_name}: {row_count} rows, using chunking")

            vals5 = db5.fetch_all(sql, col_names=cols5, chunk_size=chunk_size)
            cols7 = []
            vals7 = self.raw_db_7.fetch_all(sql, col_names=cols7, chunk_size=chunk_size)

        except Exception as e:
            _logger.error(f"Error fetching data from {table_name}: {e}")
            raise

        return cols5, vals5, cols7, vals7

    def _filter_vals(self, vals, col_names, only_names):
        idxs = [col_names.index(x) for x in only_names]
        filtered = []
        for row in vals:
            filtered.append([row[idx] for idx in idxs])
        return filtered

    @staticmethod
    def _normalize_bool(value):
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in [0, 1]:
            return bool(value)
        if isinstance(value, str):
            val = value.strip().lower()
            if val in ["t", "true", "1", "yes", "y"]:
                return True
            if val in ["f", "false", "0", "no", "n"]:
                return False
        return None

    def _cmp_values(self, table_name: str, vals5, only_in_5, vals7, only_in_7, do_not_show: bool):
        too_many_5 = ""
        too_many_7 = ""
        LIMIT = 5
        if len(only_in_5 or []) > LIMIT:
            too_many_5 = f"!!! TOO MANY [{len(only_in_5)}] "
        if len(only_in_7 or []) > LIMIT:
            too_many_7 = f"!!! TOO MANY [{len(only_in_7)}] "

        do_not_show = do_not_show or "CI" in os.environ or "GITHUB_ACTION" in os.environ
        # assume we do not have emails that we do not want to show in db7
        if do_not_show:
            only_in_5 = [x if "@" not in x else "....." for x in only_in_5]
            only_in_7 = [x if "@" not in x else "....." for x in only_in_7]

        _logger.info(
            f"Table [{table_name}]: v5:[{len(vals5 or [])}], "
            f"v7:[{len(vals7 or [])}]\n"
            f"  {too_many_5 or ''}only in v5:[{(only_in_5[:LIMIT] if only_in_5 else [])}]\n"
            f"  {too_many_7 or ''}only in v7:[{(only_in_7[:LIMIT] if only_in_7 else [])}]"
        )

    def diff_table_cmp_cols(self, db5, table_name: str, compare_arr: list, gdpr: bool = True):
        cols5, vals5, cols7, vals7 = self._fetch_all_vals(db5, table_name)
        do_not_show = gdpr and "email" in compare_arr

        filtered5 = self._filter_vals(vals5, cols5, compare_arr)
        vals5_cmp = ["|".join(str(x) for x in x) for x in filtered5]
        filtered7 = self._filter_vals(vals7, cols7, compare_arr)
        vals7_cmp = ["|".join(str(x) for x in x) for x in filtered7]

        only_in_5 = list(set(vals5_cmp).difference(vals7_cmp))
        only_in_7 = list(set(vals7_cmp).difference(vals5_cmp))
        if not (only_in_5 or only_in_7):
            _logger.info(f"Table [{table_name: >20}] is THE SAME in v5 and v7!")
            return
        self._cmp_values(table_name, vals5, only_in_5, vals7, only_in_7, do_not_show)

    def diff_table_cmp_len(self, db5, table_name: str, nonnull: list = None, gdpr: bool = True, sql: str = None):
        nonnull = nonnull or []
        sql_info = False
        cols5, vals5, cols7, vals7 = self._fetch_all_vals(db5, table_name)
        do_not_show = gdpr and "email" in nonnull

        def _status_meta(v5_count: int, v7_count: int):
            if v5_count == v7_count:
                return "OK", _logger.info
            if v7_count < v5_count:
                return "CRIT", _logger.critical
            return "WARN", _logger.warning

        len_vals5 = len(vals5 or [])
        len_vals7 = len(vals7 or [])

        if len_vals5 != len_vals7 and sql:
            cols5, vals5, cols7, vals7 = self._fetch_all_vals(db5, table_name, sql)
            sql_info = True

        status, log_fnc = _status_meta(len_vals5, len_vals7)
        counts = f"v5:[{len_vals5:>8}], v7:[{len_vals7:>8}]"
        log_fnc(
            f"Table [{table_name: >20}] {status:<12} {counts}  compared by len only")

        for col_name in nonnull:
            vals5_cmp = [x[0] for x in self._filter_vals(vals5 or [], cols5 or [],
                                                         [col_name]) if x[0] is not None]
            vals7_cmp = [x[0] for x in self._filter_vals(vals7 or [], cols7 or [],
                                                         [col_name]) if x[0] is not None]

            status, log_fnc = _status_meta(len(vals5_cmp), len(vals7_cmp))
            extra = ""
            if str(col_name).lower() in ["withdrawn", "in_archive", "discoverable", "deleted"]:
                vals5_true = sum(1 for v in vals5_cmp if self._normalize_bool(v) is True)
                vals5_false = sum(
                    1 for v in vals5_cmp if self._normalize_bool(v) is False)
                vals5_other = len(vals5_cmp) - vals5_true - vals5_false

                vals7_true = sum(1 for v in vals7_cmp if self._normalize_bool(v) is True)
                vals7_false = sum(
                    1 for v in vals7_cmp if self._normalize_bool(v) is False)
                vals7_other = len(vals7_cmp) - vals7_true - vals7_false

                vals5_other_txt = f", other={vals5_other}" if vals5_other else ""
                vals7_other_txt = f", other={vals7_other}" if vals7_other else ""
                extra = (
                    f" (values v5:[true={vals5_true}, false={vals5_false}{vals5_other_txt}], "
                    f"v7:[true={vals7_true}, false={vals7_false}{vals7_other_txt}])"
                )
            counts = f"v5:[{len(vals5_cmp):>8}], v7:[{len(vals7_cmp):>8}]"
            log_fnc(
                f"Table [{table_name: >20}] {status:<12} {counts}  NOT NULL [{col_name:>15}]{extra}")

        if sql_info:
            len_sql_v5 = len(vals5 or [])
            len_sql_v7 = len(vals7 or [])
            status, log_fnc = _status_meta(len_sql_v5, len_sql_v7)
            counts = f"v5:[{len_sql_v5:>8}], v7:[{len_sql_v7:>8}]"
            log_fnc(
                f"Table [{table_name: >20}] {status:<12} {counts}  SQL request: {sql}")

    def diff_table_sql(self, db5, table_name: str, sql5, sql7, compare, process_ftor):
        _logger.debug(f"[SQL-VALIDATION] {table_name}: preparing fetch v5 and v7")

        chunk_size_5 = None
        chunk_size_7 = None
        sql5_normalized = str(sql5 or "").strip().lower()
        sql7_normalized = str(sql7 or "").strip().lower()
        expected_sql = f"select * from {table_name}".lower()
        if sql5_normalized == expected_sql and sql7_normalized == expected_sql:
            try:
                row_count_5 = db5.fetch_one(f"SELECT COUNT(*) FROM {table_name}")
                row_count_7 = self.raw_db_7.fetch_one(
                    f"SELECT COUNT(*) FROM {table_name}")

                if row_count_5 and row_count_5 > DB_LARGE_TABLE_THRESHOLD:
                    chunk_size_5 = DB_CHUNK_SIZE
                if row_count_7 and row_count_7 > DB_LARGE_TABLE_THRESHOLD:
                    chunk_size_7 = DB_CHUNK_SIZE

                if chunk_size_5 or chunk_size_7:
                    _logger.debug(
                        f"[SQL-VALIDATION] {table_name}: large table detected "
                        f"(v5:{row_count_5}, v7:{row_count_7}) -> chunked fetch "
                        f"(v5:{chunk_size_5 or 'off'}, v7:{chunk_size_7 or 'off'})"
                    )
            except Exception as e:
                _logger.warning(
                    f"[SQL-VALIDATION] {table_name}: could not pre-check row count, continuing without chunk hint. [{e}]"
                )

        cols5 = []
        vals5 = db5.fetch_all(sql5, col_names=cols5, chunk_size=chunk_size_5)
        _logger.debug(
            f"[SQL-VALIDATION] {table_name}: fetched v5 rows [{len(vals5 or [])}]")

        cols7 = []
        vals7 = self.raw_db_7.fetch_all(sql7, col_names=cols7, chunk_size=chunk_size_7)
        _logger.debug(
            f"[SQL-VALIDATION] {table_name}: fetched v7 rows [{len(vals7 or [])}]")

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
            _logger.debug(f"[SQL-VALIDATION] {table_name}: normalizing datasets")
            vals5_cmp, vals7_cmp = process_ftor(self._repo, vals5_cmp, vals7_cmp)
            # ignored
            if vals5_cmp is None and vals7_cmp is None:
                return

        _logger.debug(f"[SQL-VALIDATION] {table_name}: computing set differences")
        only_in_5 = list(set(vals5_cmp).difference(vals7_cmp))
        only_in_7 = list(set(vals7_cmp).difference(vals5_cmp))
        self._cmp_values(table_name, vals5, only_in_5, vals7, only_in_7, False)

    def validate(self, to_validate):
        total_validations = sum(len(valid_defs) for valid_defs in to_validate)
        current_validation = 0

        _logger.info(f"Starting validation of {total_validations} table definitions...")

        for valid_defs in to_validate:
            for table_name, defin in valid_defs:
                current_validation += 1
                progress = f"[{current_validation}/{total_validations}]"

                _logger.info(f"{progress} Validating {table_name}")

                try:
                    db5_name = defin.get("db", "db_dspace_5")
                    db5 = self.raw_db_dspace_5 if db5_name == "db_dspace_5" else self.raw_db_utilities_5

                    cmp = defin.get("compare", None)
                    if cmp is not None:
                        _logger.debug(f"{progress} Comparing columns for {table_name}...")
                        self.diff_table_cmp_cols(db5, table_name, cmp)

                    cmp = defin.get("nonnull", None)
                    if cmp is not None:
                        _logger.debug(
                            f"{progress} Checking non-null constraints for {table_name}...")
                        self.diff_table_cmp_len(db5, table_name, cmp)

                    # compare only len
                    if not defin:
                        _logger.debug(
                            f"{progress} Comparing table length for {table_name}...")
                        self.diff_table_cmp_len(db5, table_name)

                    cmp = defin.get("len", None)
                    if cmp is not None:
                        _logger.debug(
                            f"{progress} Comparing custom length query for {table_name}...")
                        self.diff_table_cmp_len(db5, table_name, None, True, cmp["sql"])

                    cmp = defin.get("sql", None)
                    if cmp is not None:
                        _logger.debug(
                            f"{progress} Running custom SQL comparison for {table_name}...")
                        self.diff_table_sql(
                            db5, table_name, cmp["5"], cmp["7"], cmp["compare"], cmp.get("process", None))

                    _logger.debug(f"{progress} Completed validation of {table_name}")

                except Exception as e:
                    _logger.error(
                        f"{progress} [FAILED] Validation failed for {table_name}: {e}")
                    continue

        _logger.info(f"Validation complete: {current_validation} tables processed")
