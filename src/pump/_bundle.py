import logging
import threading
from ._utils import read_json, time_method, serialize, deserialize, progress_bar, log_before_import, log_after_import, run_tasks

_logger = logging.getLogger("pump.bundle")


class bundles:
    """
        Mapped tables: item2bundle, bundle
        SQL:
    """
    TYPE = 1
    validate_table = [
        ["bundle", {
        }],
    ]

    def __init__(self, bundle_file_str: str, item2bundle_file_str: str):
        self._bundles = read_json(bundle_file_str) or []
        self._item2bundle = read_json(item2bundle_file_str) or []
        self._imported = {
            "bundles": 0,
        }
        self._id2uuid = {}

        if not self._item2bundle:
            _logger.info(f"Empty input: [{item2bundle_file_str}].")

        if not self._bundles:
            _logger.info(f"Empty input: [{bundle_file_str}].")
            return

        self._itemid2bundle = {}
        for e in self._item2bundle:
            self._itemid2bundle.setdefault(e['item_id'], []).append(e['bundle_id'])

        self._primary = {}
        for b in self._bundles:
            primary_id = b['primary_bitstream_id']
            if primary_id:
                self._primary[primary_id] = b['bundle_id']

    def __len__(self):
        return len(self._bundles or {})

    def uuid(self, b_id: int):
        assert isinstance(list(self._id2uuid.keys() or [""])[0], str)
        return self._id2uuid.get(str(b_id), None)

    @property
    def primary(self):
        return self._primary

    @property
    def imported(self):
        return self._imported['bundles']

    @time_method
    def import_to(self, dspace, metadatas, items, bundle_workers: int = 1):
        expected = len(self)
        log_key = "bundles"
        log_before_import(log_key, expected)

        tasks = []
        for item_id, bundle_arr in progress_bar(self._itemid2bundle.items()):
            for bundle_id in bundle_arr:
                data = {}
                meta_bundle = metadatas.value(bundles.TYPE, bundle_id)
                if meta_bundle:
                    data['metadata'] = meta_bundle
                    data['name'] = meta_bundle['dc.title'][0]['value']
                tasks.append((item_id, bundle_id, data))

        workers = max(1, int(bundle_workers or 1))
        if workers > 1 and len(tasks) > 1:
            _logger.info(f"Parallel bundle import enabled with workers:[{workers}]")

        local = threading.local()

        def worker(task):
            item_id, _bundle_id, data = task
            item_uuid = items.uuid(item_id)
            if item_uuid is None:
                return None

            if workers == 1:
                return dspace.put_bundle(item_uuid, data)

            client = getattr(local, "dspace", None)
            if client is None:
                local.dspace = dspace.spawn_worker_client()
                client = local.dspace
            return client.put_bundle(item_uuid, data)

        for task, resp, err in run_tasks(
            tasks,
            worker,
            workers=workers,
            desc=f"Importing bundles ({workers} workers)",
        ):
            item_id, bundle_id, _data = task
            if items.uuid(item_id) is None:
                _logger.critical(f'Item UUID not found for [{item_id}]')
                continue

            if err is not None:
                _logger.error(f'put_bundle: [{item_id}] failed [{str(err)}]')
                continue

            if not isinstance(resp, dict) or 'uuid' not in resp:
                _logger.error(
                    f'put_bundle: [{item_id}] failed [Invalid response: {resp}]')
                continue

            self._id2uuid[str(bundle_id)] = resp['uuid']
            self._imported["bundles"] += 1

        log_after_import(log_key, expected, self.imported)

    # =============

    def serialize(self, file_str: str):
        # not needed _itemid2bundle, _primary
        data = {
            "bundles": self._bundles,
            "item2bundle": self._item2bundle,
            "id2uuid": self._id2uuid,
            "imported": self._imported,
        }
        serialize(file_str, data)

    def deserialize(self, file_str: str):
        data = deserialize(file_str)
        self._bundles = data["bundles"]
        self._item2bundle = data["item2bundle"]
        self._id2uuid = data["id2uuid"]
        self._imported = data["imported"]
