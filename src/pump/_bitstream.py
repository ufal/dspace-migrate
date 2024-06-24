import logging
from ._utils import read_json, time_method, serialize, deserialize, progress_bar, log_before_import, log_after_import, path_exists

_logger = logging.getLogger("pump.bitstream")


class bitstreams:
    """
        SQL:
        Mapped tables: bitstream, bundle2bitstream, metadata, most_recent_checksum
            and checksum_result
    """
    TYPE = 0
    validate_table = [
        ["bitstream", {
            "compare": ["checksum", "internal_id", "deleted"],
        }],
        ["bundle2bitstream", {
        }],
        ["checksum_results", {
            "compare": ["result_description", "result_code"],
        }],

    ]

    def __init__(self, bitstream_file_str: str, bundle2bitstream_file_str: str):
        self._bs = read_json(bitstream_file_str)
        self._bundle2bs = read_json(bundle2bitstream_file_str)

        self._id2uuid = {}
        self._imported = {
            "bitstream": 0,
            "com_logo": 0,
            "col_logo": 0,
        }

        if len(self._bs) == 0:
            _logger.info(f"Empty input: [{bitstream_file_str}].")
            return

        self._bs2bundle = {}
        for e in self._bundle2bs:
            self._bs2bundle[e['bitstream_id']] = e['bundle_id']
        self._done = []

    def __len__(self):
        return len(self._bs)

    def uuid(self, b_id: int):
        return self._id2uuid.get(str(b_id), None)

    @staticmethod
    def bitstream_path(internal_id: str):
        return f'{internal_id[:2]}/{internal_id[2:4]}/{internal_id[4:6]}/{internal_id}'

    @property
    def imported(self):
        return self._imported['bitstream']

    @property
    def imported_com_logos(self):
        return self._imported['com_logo']

    @property
    def imported_col_logos(self):
        return self._imported['col_logo']

    @time_method
    def import_to(self, env, cache_file, dspace, metadatas, bitstreamformatregistry, bundles, communities, collections):
        if "bs" in self._done:
            _logger.info("Skipping bitstream import")
        else:
            self._done.append("bs")
            self._bitstream_import_to(env, dspace, metadatas,
                                      bitstreamformatregistry, bundles, communities, collections)
            self.serialize(cache_file)

        if "logos" in self._done:
            _logger.info("Skipping logo import")
        else:
            self._done.append("logos")
            # add logos (bitstreams) to collections and communities
            self._logo2com_import_to(dspace, communities)
            self._logo2col_import_to(dspace, collections)
            self.serialize(cache_file)

    def _logo2col_import_to(self, dspace, collections):
        if not collections.logos:
            _logger.info("There are no logos for collections.")
            return

        expected = len(collections.logos.items())
        log_key = "collection logos"
        log_before_import(log_key, expected)

        for key, value in progress_bar(collections.logos.items()):
            col_uuid = collections.uuid(key)
            bs_uuid = self.uuid(value)
            if col_uuid is None or bs_uuid is None:
                continue

            params = {
                'collection_id': col_uuid,
                'bitstream_id': bs_uuid
            }
            try:
                resp = dspace.put_col_logo(params)
                self._imported["col_logo"] += 1
            except Exception as e:
                _logger.error(f'put_col_logo [{col_uuid}]: failed. Exception: [{str(e)}]')

        log_after_import(log_key, expected, self.imported_col_logos)

    def _logo2com_import_to(self, dspace, communities):
        """
            Add bitstream to community as community logo.
            Logo has to exist in database.
        """
        if not communities.logos:
            _logger.info("There are no logos for communities.")
            return

        expected = len(communities.logos.items())
        log_key = "communities logos"
        log_before_import(log_key, expected)

        for key, value in progress_bar(communities.logos.items()):
            com_uuid = communities.uuid(key)
            bs_uuid = self.uuid(value)
            if com_uuid is None or bs_uuid is None:
                continue

            params = {
                'community_id': com_uuid,
                'bitstream_id': bs_uuid,
            }
            try:
                resp = dspace.put_com_logo(params)
                self._imported["com_logo"] += 1
            except Exception as e:
                _logger.error(f'put_com_logo [{com_uuid}]: failed. Exception: [{str(e)}]')

        log_after_import(log_key, expected, self.imported_com_logos)

    def _bitstream_import_to(self, env, dspace, metadatas, bitstreamformatregistry, bundles, communities, collections):
        expected = len(self)
        log_key = "bitstreams"
        log_before_import(log_key, expected)

        # TODO(jm): fake bitstreams
        TEST_DEV5 = "http://dev-5.pc" in env["backend"]["endpoint"]
        if TEST_DEV5 and env["assetstore"] == "":
            _logger.error(
                'Location of assetstore folder is not defined but it should be checked!')

        for i, b in enumerate(progress_bar(self._bs)):
            b_id = b['bitstream_id']
            b_deleted = b['deleted']

            # do bitstream checksum
            # do this after every 500 imported bitstreams,
            # because the server may be out of memory
            if (i + 1) % 500 == 0:
                try:
                    dspace.add_checksums()
                except Exception as e:
                    _logger.error(f'add_checksums failed: [{str(e)}]')

            data = {}
            b_meta = metadatas.value(bitstreams.TYPE, b_id,
                                     log_missing=b_deleted is False)
            if b_meta is not None:
                data['metadata'] = b_meta
            else:
                com_logo = b_id in communities.logos.values()
                col_logo = b_id in collections.logos.values()
                if b_deleted or com_logo or col_logo:
                    log_fnc = _logger.debug
                else:
                    log_fnc = _logger.warning
                log_fnc(
                    f'No metadata for bitstream [{b_id}] deleted: [{b_deleted}] com logo:[{com_logo}] col logo:[{col_logo}]')

            data['sizeBytes'] = b['size_bytes']
            data['checkSum'] = {
                'checkSumAlgorithm': b['checksum_algorithm'],
                'value': b['checksum']
            }

            if not b['bitstream_format_id']:
                unknown_id = bitstreamformatregistry.unknown_format_id
                _logger.info(f'Using unknown format for bitstream {b_id}')
                b['bitstream_format_id'] = unknown_id

            bformat_mimetype = bitstreamformatregistry.mimetype(b['bitstream_format_id'])
            if bformat_mimetype is None:
                _logger.critical(f'Bitstream format not found for [{b_id}]')

            params = {
                'internal_id': b['internal_id'],
                'storeNumber': b['store_number'],
                'bitstreamFormat': bformat_mimetype,
                'deleted': b['deleted'],
                'sequenceId': b['sequence_id'],
                'bundle_id': None,
                'primaryBundle_id': None
            }

            # TODO(jm): fake bitstreams
            path = self.bitstream_path(params['internal_id'])
            if TEST_DEV5 and not path_exists(f'{env["assetstore"]}{path}'):
                data['sizeBytes'] = 1748
                data['checkSum'] = {
                    'checkSumAlgorithm': b['checksum_algorithm'],
                    'value': '8a4605be74aa9ea9d79846c1fba20a33'
                }
                params['internal_id'] = '77893754617268908529226218097860272513'

            # if bitstream has bundle, set bundle_id from None to id
            if b_id in self._bs2bundle:
                bundle_int_id = self._bs2bundle[b_id]
                params['bundle_id'] = bundles.uuid(bundle_int_id)

            # if bitstream is primary bitstream of some bundle,
            # set primaryBundle_id from None to id
            if b_id in bundles.primary:
                params['primaryBundle_id'] = bundles.uuid(bundles.primary[b_id])
            try:
                resp = dspace.put_bitstream(params, data)
                self._id2uuid[str(b_id)] = resp['id']
                self._imported["bitstream"] += 1
            except Exception as e:
                _logger.error(f'put_bitstream [{b_id}]: failed. Exception: [{str(e)}]')

        # do bitstream checksum for the last imported bitstreams
        # these bitstreams can be less than 500, so it is not calculated in a loop
        try:
            dspace.add_checksums()
        except Exception as e:
            _logger.error(f'add_checksums failed: [{str(e)}]')

        log_after_import(log_key, expected, self.imported)

    # =============

    def serialize(self, file_str: str):
        data = {
            "bs": self._bs,
            "bundle2bs": self._bundle2bs,
            "id2uuid": self._id2uuid,
            "imported": self._imported,
            "done": self._done,
        }
        serialize(file_str, data)

    def deserialize(self, file_str: str):
        data = deserialize(file_str)
        self._bs = data["bs"]
        self._bundle2bs = data["bundle2bs"]
        self._id2uuid = data["id2uuid"]
        self._imported = data["imported"]
        self._done = data["done"]
