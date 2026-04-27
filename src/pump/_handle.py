import logging
from ._utils import read_json, time_method, serialize, deserialize, log_before_import, log_after_import
from ._item import items

_logger = logging.getLogger("pump.handle")


class handles:
    """
        SQL:
            delete from handle ;
    """
    validate_table = [
        ["handle", {
            "compare": ["handle", "resource_type_id"],
        }],
    ]

    def __init__(self, file_str: str):
        self._handles = {}
        self._imported = 0

        js = read_json(file_str) or []
        for h in js:
            res_type_id = h['resource_type_id']
            res_id = h['resource_id']
            arr = self._handles.setdefault(
                str(res_type_id), {}).setdefault(str(res_id), [])
            arr.append(h)

    def __len__(self):
        return sum(
            len(res_arr)
            for by_res_type in (self._handles or {}).values()
            for res_arr in (by_res_type or {}).values()
        )

    @property
    def imported(self):
        return self._imported

    def expected_import_count(self):
        ext = len(self.get_handles_by_type(None, None) or [])
        item = len(self.get_handles_by_type(items.TYPE, None) or [])
        return ext + item

    # =============

    def serialize(self, file_str: str):
        # cannot serialize tuples as keys
        d = {
            "handles": self._handles,
            "imported": self._imported,
        }
        serialize(file_str, d, sorted=False)

    def deserialize(self, file_str: str):
        data = deserialize(file_str)
        self._handles = data["handles"]
        self._imported = data["imported"]

    # =============

    def get_handles_by_type(self, type_id: int = None, res_id: int = None):
        return self._handles.get(str(type_id), {}).get(str(res_id), [])

    # =============

    @time_method
    def import_to(self, dspace, raw_db_7=None):
        # external
        arr = self.get_handles_by_type(None, None) or []
        expected = len(arr)
        log_key = "external handles"
        log_before_import(log_key, expected)
        existing_external = None
        if raw_db_7 is not None:
            existing_external = raw_db_7.fetch_one(
                "SELECT COUNT(*) FROM handle WHERE resource_type_id IS NULL AND resource_id IS NULL"
            ) or 0

        if existing_external is not None and existing_external >= expected:
            _logger.info(
                f"Skipping external handle POSTs, already present in DB [{existing_external}/{expected}]"
            )
            cnt = expected
        else:
            cnt = dspace.put_handles(arr)
            log_after_import(log_key, expected, cnt)
            if cnt < expected:
                raise RuntimeError(
                    f"External handle import incomplete [{cnt}/{expected}]"
                )
        self._imported += cnt

        # no object
        arr = self.get_handles_by_type(items.TYPE, None) or []
        expected = len(arr)
        log_key = "handles"
        log_before_import(log_key, expected)
        existing_items_none = None
        if raw_db_7 is not None:
            existing_items_none = raw_db_7.fetch_one(
                f"SELECT COUNT(*) FROM handle WHERE resource_type_id = {items.TYPE} AND resource_id IS NULL"
            ) or 0

        if existing_items_none is not None and existing_items_none >= expected:
            _logger.info(
                f"Skipping item-without-object handle POSTs, already present in DB [{existing_items_none}/{expected}]"
            )
            cnt = expected
        else:
            cnt = dspace.clarin_put_handles(arr)
            log_after_import(log_key, expected, cnt)
            if cnt < expected:
                raise RuntimeError(
                    f"Handle import incomplete [{cnt}/{expected}]"
                )
        self._imported += cnt

    # =============

    def get(self, type_id: int, obj_id: int):
        """
            Get handle based on object type and its id.
        """
        arr = self.get_handles_by_type(type_id, obj_id) or []
        if len(arr) == 0:
            return None
        return arr[0]['handle']
