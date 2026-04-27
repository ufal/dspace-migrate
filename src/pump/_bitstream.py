import os
import logging
from ._utils import read_json, time_method, serialize, deserialize, progress_bar, log_before_import, log_after_import

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

    ignored_fields = ["local.bitstream.redirectToURL"]
    TEST_FALLBACK_INTERNAL_ID = '57024294293009067626820405177604023574'
    TEST_FALLBACK_SIZE = 1748
    TEST_FALLBACK_CHECKSUM = 'bb9bdc0b3349e4284e09149f943790b4'

    test_table = [
        {
            "name": "bitstream_ignored_fields",
            "left": ["sql", "db7", "one", "select count(*) from metadatavalue "
                                   "where metadata_field_id in "
                                   "(select metadata_field_id from metadatafieldregistry "
                                   "where qualifier = 'redirectToURL')"],
            "right": ["val", 0]
        }
    ]

    def __init__(self, bitstream_file_str: str, bundle2bitstream_file_str: str):
        self._bs = read_json(bitstream_file_str) or []
        self._bundle2bs = read_json(bundle2bitstream_file_str) or []

        self._id2uuid = {}
        self._imported = {
            "bitstream": 0,
            "com_logo": 0,
            "col_logo": 0,
        }

        if not self._bundle2bs:
            _logger.info(f"Empty input: [{bundle2bitstream_file_str}].")

        if not self._bs:
            _logger.info(f"Empty input: [{bitstream_file_str}].")
            return

        self._bs2bundle = {}
        for e in self._bundle2bs:
            self._bs2bundle[e['bitstream_id']] = e['bundle_id']
        self._done = []

    def __len__(self):
        return len(self._bs or {})

    def uuid(self, b_id: int):
        return self._id2uuid.get(str(b_id), None)

    @staticmethod
    def bitstream_path(internal_id: str):
        return os.path.join(internal_id[:2], internal_id[2:4], internal_id[4:6], internal_id)

    @property
    def imported(self):
        return self._imported['bitstream']

    @property
    def imported_com_logos(self):
        return self._imported['com_logo']

    @property
    def imported_col_logos(self):
        return self._imported['col_logo']

    @property
    def done(self):
        return self._done

    @time_method
    def import_to(self, env, cache_file, dspace, metadatas, bitstreamformatregistry, bundles, communities, collections):
        if "bs" in self._done:
            _logger.info("Skipping bitstream import")
        else:
            self._bitstream_import_to(env, cache_file, dspace, metadatas,
                                      bitstreamformatregistry, bundles, communities, collections)
            self._done.append("bs")
            self.serialize(cache_file)

        if "logos" in self._done:
            _logger.info("Skipping logo import")
        else:
            # add logos (bitstreams) to collections and communities
            self._logo2com_import_to(dspace, communities)
            self._logo2col_import_to(dspace, collections)
            self._done.append("logos")
            self.serialize(cache_file)

    def _logo2col_import_to(self, dspace, collections):
        if not collections.logos:
            _logger.info("There are no logos for collections.")
            return

        expected = len((collections.logos or {}).items())
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

        expected = len((communities.logos or {}).items())
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

    def _bitstream_import_to(self, env, cache_file, dspace, metadatas, bitstreamformatregistry, bundles, communities, collections):
        skip_deleted = env["backend"].get("ignore_deleted_bitstreams", False)
        test_instance = env["backend"].get("testing", False)
        expected = len([b for b in (self._bs or []) if not (
            skip_deleted and b.get('deleted', False))])
        log_key = "bitstreams"
        log_before_import(log_key, expected)
        failed_ids = []
        skipped_deleted = 0
        skipped_already_imported = 0
        errored = 0
        subsequent_errors = 0
        repeated_error_warning_issued = False
        checkpoint_every = 2000
        checkpoint_counter = 0
        checkpoints_saved = 0
        diagnostic_invalid_response_logs = 0

        path_assetstore = env["assetstore"]
        fallback_rel_path = None
        fallback_full_path = None
        fallback_exists = None
        if test_instance and path_assetstore == "":
            _logger.critical(
                'Location of assetstore dir is not defined but it should be checked!')
        if test_instance and path_assetstore:
            fallback_rel_path = self.bitstream_path(self.TEST_FALLBACK_INTERNAL_ID)
            fallback_full_path = os.path.join(path_assetstore, fallback_rel_path)
            fallback_exists = os.path.exists(fallback_full_path)
            if not fallback_exists:
                _logger.warning(
                    f'backend.testing=true requires testing bitstream in server assetstore at '
                    f'[{fallback_rel_path}] (full path: [{fallback_full_path}]). '
                    f'If missing, put_bitstream may fail repeatedly with empty/invalid responses.')

        def _update_progress(pbar_ref):
            """Centralized progress-bar postfix update."""
            pbar_ref.set_postfix(
                imported=self._imported['bitstream'],
                skipped_deleted=skipped_deleted,
                resumed=skipped_already_imported,
                errored=errored,
                checkpoints=checkpoints_saved,
                to_checkpoint=checkpoint_every - checkpoint_counter,
            )

        def _record_error(b_id_val):
            """Record a failed bitstream id and emit diagnostics for repeated failures in testing mode."""
            nonlocal errored, subsequent_errors, repeated_error_warning_issued
            errored += 1
            subsequent_errors += 1
            if len(failed_ids) < 20:
                failed_ids.append(b_id_val)
            if (test_instance
                    and subsequent_errors >= 100
                    and not repeated_error_warning_issued):
                repeated_error_warning_issued = True
                exists_text = str(
                    fallback_exists) if fallback_exists is not None else 'unknown (assetstore path not configured)'
                _logger.warning(
                    f'Many consecutive put_bitstream errors detected in testing mode [{subsequent_errors}]. '
                    f'Verify testing fallback bitstream on server assetstore: '
                    f'relative_path=[{fallback_rel_path}] full_path=[{fallback_full_path}] exists=[{exists_text}].')

        pbar = progress_bar(self._bs)
        for i, b in enumerate(pbar):
            b_id = b['bitstream_id']
            b_deleted = b['deleted']

            if str(b_id) in self._id2uuid:
                skipped_already_imported += 1
                if skipped_already_imported % 200 == 0 or i == 0:
                    _update_progress(pbar)
                continue

            if skip_deleted and b_deleted:
                skipped_deleted += 1
                if skipped_deleted % 200 == 0 or i == 0:
                    _update_progress(pbar)
                continue

            # do bitstream checksum
            # do this after every 500 imported bitstreams,
            # because the server may be out of memory
            if (i + 1) % 500 == 0:
                try:
                    dspace.add_checksums()
                except Exception as e:
                    _logger.error(f'add_checksums failed: [{str(e)}]')

            data = {}
            b_meta = metadatas.filter_res_d(metadatas.value(
                bitstreams.TYPE, b_id, log_missing=b_deleted is False), self.ignored_fields)
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
                unknown_mimetype = bitstreamformatregistry.mimetype(
                    bitstreamformatregistry.unknown_format_id)
                if unknown_mimetype is not None:
                    _logger.warning(
                        f'Bitstream format not found for [{b_id}] id:[{b.get("bitstream_format_id")}] - using unknown mimetype [{unknown_mimetype}]')
                    bformat_mimetype = unknown_mimetype
                else:
                    bformat_mimetype = 'application/octet-stream'
                    _logger.warning(
                        f'Bitstream format not found for [{b_id}] id:[{b.get("bitstream_format_id")}] - using fallback mimetype [{bformat_mimetype}]')

            params = {
                'internal_id': b['internal_id'],
                'storeNumber': b['store_number'],
                'bitstreamFormat': bformat_mimetype,
                'deleted': b['deleted'],
                'sequenceId': b['sequence_id'],
                'bundle_id': None,
                'primaryBundle_id': None
            }

            path = self.bitstream_path(params['internal_id'])
            full_path = os.path.join(path_assetstore, path)
            # NOTE: if it is the testing instance AND we do not have the bitstream
            # use our testing one
            if test_instance and not os.path.exists(full_path):
                data['sizeBytes'] = self.TEST_FALLBACK_SIZE
                data['checkSum'] = {
                    'checkSumAlgorithm': b['checksum_algorithm'],
                    'value': self.TEST_FALLBACK_CHECKSUM
                }
                params['internal_id'] = self.TEST_FALLBACK_INTERNAL_ID

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
                if not isinstance(resp, dict) or 'id' not in resp:
                    if b['deleted']:
                        _logger.warning(
                            f'put_bitstream [{b_id}] returned invalid response for deleted bitstream: [{resp}] - skipping')
                        continue
                    if diagnostic_invalid_response_logs < 10:
                        diagnostic_invalid_response_logs += 1
                        _logger.error(
                            f'put_bitstream [{b_id}] diagnostics: internal_id=[{params.get("internal_id")}] '
                            f'bundle_id=[{params.get("bundle_id")}] primaryBundle_id=[{params.get("primaryBundle_id")}] '
                            f'bitstreamFormat=[{params.get("bitstreamFormat")}] deleted=[{params.get("deleted")}] '
                            f'sizeBytes=[{data.get("sizeBytes")}] checkSumAlgorithm=[{(data.get("checkSum") or {}).get("checkSumAlgorithm")}]')
                    _logger.error(
                        f'put_bitstream [{b_id}]: failed. Exception: [Invalid response from put_bitstream: [{resp}]]')
                    _record_error(b_id)
                    _update_progress(pbar)
                    continue
                self._id2uuid[str(b_id)] = resp['id']
                self._imported["bitstream"] += 1
                subsequent_errors = 0
                checkpoint_counter += 1
                if checkpoint_counter >= checkpoint_every:
                    checkpoint_counter = 0
                    checkpoints_saved += 1
                    self.serialize(cache_file)
                    _update_progress(pbar)
                if b['deleted']:
                    _logger.warning(f'Imported bitstream is deleted! UUID: {resp["id"]}')
            except Exception as e:
                _logger.error(f'put_bitstream [{b_id}]: failed. Exception: [{str(e)}]')
                _record_error(b_id)
                _update_progress(pbar)

            if (i + 1) % 200 == 0:
                _update_progress(pbar)

        _update_progress(pbar)

        if failed_ids:
            _logger.warning(
                f'Bitstream import completed with [{len(failed_ids)}] failed records. First failures: {failed_ids[:20]}')
        if skipped_already_imported:
            _logger.info(
                f'Bitstream import resumed by skipping [{skipped_already_imported}] already imported records from cache.')
        if skipped_deleted:
            _logger.info(
                f'Bitstream import skipped [{skipped_deleted}] deleted records (backend.ignore_deleted_bitstreams=true).')
        if errored:
            _logger.warning(
                f'Bitstream import skipped/errored [{errored}] records due to invalid/failed responses.')

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
