import logging
from ._utils import read_json, time_method, serialize, deserialize, progress_bar, log_before_import, log_after_import

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
    def import_to(self, dspace, metadatas, items):
        expected = len(self)
        log_key = "bundles"
        log_before_import(log_key, expected)

        for item_id, bundle_arr in progress_bar(self._itemid2bundle.items()):
            for bundle_id in bundle_arr:
                data = {}
                meta_bundle = metadatas.value(bundles.TYPE, bundle_id)
                if meta_bundle:
                    data['metadata'] = meta_bundle
                    data['name'] = meta_bundle['dc.title'][0]['value']

                try:
                    item_uuid = items.uuid(item_id)
                    if item_uuid is None:
                        _logger.critical(f'Item UUID not found for [{item_id}]')
                        continue
                    resp = dspace.put_bundle(item_uuid, data)
                    self._id2uuid[str(bundle_id)] = resp['uuid']
                    self._imported["bundles"] += 1
                except Exception as e:
                    _logger.error(f'put_bundle: [{item_id}] failed [{str(e)}]')

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
