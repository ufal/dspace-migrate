import logging
import re
import json
import os
import time
from typing import Optional
from tqdm import tqdm

from ._utils import read_json, time_method, serialize, deserialize, progress_bar, log_before_import, log_after_import

_logger = logging.getLogger("pump.metadata")

# Validation-time URL normalization for handle identifiers.
# The dev prefix is replaced with the canonical HDL prefix so v5/v7
# values compare equal regardless of the server that minted them.
_V7_DEV_HANDLE_PREFIX = "http://dev-5.pc:88/handle/"
_V7_CANONICAL_HANDLE_PREFIX = "http://hdl.handle.net/"


def _iter_jsonl_with_progress(file_name: str):
    total_bytes = os.path.getsize(file_name)
    read_bytes = 0
    with open(file_name, mode='rb') as fin:
        for raw_line in fin:
            read_bytes += len(raw_line)
            line = raw_line.decode('utf-8', errors='replace').strip()
            if not line:
                continue
            yield json.loads(line), read_bytes, total_bytes


def _metadatavalue_process(repo, v5data: list, v7data: list):
    """
        v5: ['COLLECTION_17_DEFAULT_READ', 'COLLECTION_20_WORKFLOW_STEP_2']
        v7: ['COLLECTION_f3c65f29-355e-4ca2-a05b-f3e30883e09f_BITSTREAM_DEFAULT_READ']
    """
    if repo is None:
        _logger.critical(
            "Cannot validate using _metadatavalue_process because repo is None")
        return None, None

    def norm_lic(text):
        # normalize it, not 100% because of licence-UD-2.2
        return text.split('/')[-1].split('.')[0]

    def norm_text(text):
        # this should not be a reasonable list of replacements but rather
        # instance specific use cases
        if "\u2028" in text:
            text = text.replace("\u2028", "\n")
        if len(text) > 0 and text[-1].isspace():
            text = text.rstrip()
        return text

    md = repo.metadatas
    get_field_id = md.get_field_id
    ignored_or_replaced = set(md.ignored_fields) | set(md.replaced_fields)
    group_type = repo.groups.TYPE
    v5_date_issued = md.V5_DATE_ISSUED
    v5_field_id_approx_date = md.get_field_id_by_name_v5("approximateDate.issued")
    v7_field_date_issued = md.V7_FIELD_DATE_ISSUED
    v7_field_id_lic = md.V7_FIELD_ID_LIC
    v7_field_id_title = md.V7_FIELD_ID_TITLE
    v7_field_id_provenance = md.V7_FIELD_ID_PROVENANCE
    v7_field_id_lang_added = md.V7_FIELD_LANG_ADDED
    v7_field_id_identifier_uri = md.V7_FIELD_ID_IDENTIFIER_URI
    debug_enabled = _logger.isEnabledFor(logging.DEBUG)

    def to_int_key_map(source: dict) -> dict:
        out = {}
        for key, value in (source or {}).items():
            try:
                out[int(key)] = value
            except Exception:
                continue
        return out

    field_id_v5_to_v7 = to_int_key_map(getattr(md, "_fields_id2v7id", {}))

    uuid_map_by_type = {
        repo.communities.TYPE: to_int_key_map(getattr(repo.communities, "_id2uuid", {})),
        repo.collections.TYPE: to_int_key_map(getattr(repo.collections, "_id2uuid", {})),
        repo.items.TYPE: to_int_key_map(getattr(repo.items, "_id2uuid", {})),
        repo.bitstreams.TYPE: to_int_key_map(getattr(repo.bitstreams, "_id2uuid", {})),
        repo.bundles.TYPE: to_int_key_map(getattr(repo.bundles, "_id2uuid", {})),
        repo.epersons.TYPE: to_int_key_map(getattr(repo.epersons, "_id2uuid", {})),
        group_type: to_int_key_map(getattr(repo.groups, "_id2uuid", {})),
    }

    v5data_new = []
    v5data_new_append = v5data_new.append
    norm_text_local = norm_text
    norm_lic_local = norm_lic
    v5_dropped_unknown_year = 0
    v5_dropped_ignored_or_replaced = 0
    v5_dropped_group_titles = 0
    v5_complex_normalized = 0
    v5_license_normalized = 0
    specific_fields = {
        v5_field_id_approx_date: set(),
        v5_date_issued: set(),
    }

    for res_id, res_type_id, text, field_id in progress_bar(v5data, desc="Normalize metadatavalue v5"):
        # ignore '0000', 15 -> we do not store unknown dates
        if field_id == v5_date_issued and text == "0000":
            v5_dropped_unknown_year += 1
            continue

        # ignore file preview in metadata
        if field_id in ignored_or_replaced:
            v5_dropped_ignored_or_replaced += 1
            continue

        type_uuid_map = uuid_map_by_type.get(res_type_id)
        raw_uuid = type_uuid_map.get(res_id) if type_uuid_map is not None else None
        if res_type_id == group_type:
            uuid = raw_uuid[0] if isinstance(
                raw_uuid, list) and len(raw_uuid) > 0 else None
        else:
            uuid = raw_uuid
        if uuid is None and debug_enabled:
            _logger.debug(
                "Cannot find uuid for [%s] [%s] [%s]",
                res_type_id,
                res_id,
                text,
            )

        if field_id in specific_fields:
            specific_fields[field_id].add(uuid)

        field_id_v7 = field_id_v5_to_v7.get(field_id)
        if field_id_v7 is None:
            field_id_v7 = get_field_id(field_id)
        #
        if "@@" in text:
            splits = text.split("@@")
            new_splits = splits

            if field_id_v7 != v7_field_id_provenance:
                if len(splits) == 5:
                    new_splits = [splits[-2], splits[1], splits[0], splits[2], splits[-1]]
                # special case - older complex field impl.
                elif len(splits) == 4 and (
                    "euFunds" in text
                    or "nationalFunds" in text
                    or "ownFunds" in text
                    or "@@Other" in text
                ):
                    new_splits = [splits[3], splits[1], splits[0], splits[2], '']
            text = ";".join(new_splits)
            if new_splits != splits:
                v5_complex_normalized += 1

        # license def
        if field_id_v7 == v7_field_id_lic:
            text = norm_lic_local(text)
            v5_license_normalized += 1

        # groups have titles in table
        if field_id_v7 == v7_field_id_title and res_type_id == group_type:
            v5_dropped_group_titles += 1
            continue

        text = norm_text_local(text)
        v5data_new_append((uuid, text, field_id_v7))

    # cleanup
    # has local.approximateDate.issued -> ignore dc.date.issued
    to_check_dates_uuids = specific_fields[v5_field_id_approx_date].intersection(
        specific_fields[v5_date_issued]
    )
    v5_removed_date_issued_due_to_approx = 0
    if to_check_dates_uuids:
        before_cleanup_len = len(v5data_new)
        v5data_new = [
            (uuid, text, field_id)
            for uuid, text, field_id in v5data_new
            if not (uuid in to_check_dates_uuids and field_id == v7_field_date_issued)
        ]
        v5_removed_date_issued_due_to_approx = before_cleanup_len - len(v5data_new)

    v7data_new = []
    v7data_new_append = v7data_new.append
    v7_dropped_lang_added = 0
    v7_license_normalized = 0
    v7_handle_normalized = 0
    for uuid, text, field_id in progress_bar(v7data, desc="Normalize metadatavalue v7"):
        # added language description in addition to language code
        if field_id == v7_field_id_lang_added:
            v7_dropped_lang_added += 1
            continue

        # should be already ignored # imported preview data
        # if field_id == 147:
        #     continue

        # license def
        if field_id == v7_field_id_lic:
            text = norm_lic_local(text)
            v7_license_normalized += 1

        if field_id == v7_field_id_identifier_uri:
            new_text = text.replace(_V7_DEV_HANDLE_PREFIX,
                                    _V7_CANONICAL_HANDLE_PREFIX)
            if new_text != text:
                v7_handle_normalized += 1
            text = new_text

        text = norm_text_local(text)
        v7data_new_append((uuid, text, field_id))

    _logger.info(
        f"Changed v5 metadata values to match v7: {len(v5data)} -> {len(v5data_new)} "
        f"(dropped: unknown-year={v5_dropped_unknown_year}, ignored/replaced={v5_dropped_ignored_or_replaced}, "
        f"group-title={v5_dropped_group_titles}, approxDate-cleanup={v5_removed_date_issued_due_to_approx}; "
        f"normalized: complex={v5_complex_normalized}, license={v5_license_normalized})"
    )
    _logger.info(
        f"Changed v7 metadata values to match v7: {len(v7data)} -> {len(v7data_new)} "
        f"(dropped: lang-added={v7_dropped_lang_added}; "
        f"normalized: license={v7_license_normalized}, handle-url={v7_handle_normalized})"
    )
    return v5data_new, v7data_new


class metadatas:
    """
        SQL:
            delete from metadatavalue ; delete from metadatafieldregistry ; delete from metadataschemaregistry ;
    """
    validate_table = [
        ["metadataschemaregistry", {
            "compare": ["namespace", "short_id"],
        }],
        ["metadatafieldregistry", {
            "compare": ["element", "qualifier"],
        }],
        ["metadatavalue", {
            "sql": {
                "5": "select resource_id, resource_type_id, text_value, metadata_field_id from metadatavalue",
                "7": "select dspace_object_id, text_value, metadata_field_id from metadatavalue",
                "compare": None,
                "process": _metadatavalue_process,
            }
        }],
    ]

    test_table = [
        {
            "name": "ignored_hasMetadata",
            "left": ["sql", "db7", "one", "select count(*) from metadatafieldregistry "
                                          "where element = 'hasMetadata'"],
            "right": ["val", 0]
        }
    ]

    def __init__(self, env, dspace, field_file_v7_str: str, schema_file_v7_str: str, value_file_v5_str: str,
                 field_file_v5_str: str, schema_file_v5_str: str):
        self._dspace = dspace
        self._values = {}

        self._field_v7 = read_json(field_file_v7_str) or []
        self._schemas_v7 = read_json(schema_file_v7_str) or []
        if not self._field_v7:
            _logger.info(f"Empty input: [{field_file_v7_str}].")
        if not self._schemas_v7:
            _logger.info(f"Empty input: [{schema_file_v7_str}].")

        self._schemas_id2short_id_v7 = {}
        for f in self._schemas_v7:
            self._schemas_id2short_id_v7[f['metadata_schema_id']] = f['short_id']
        self._field_v7_existed = {}
        for f in self._field_v7:
            self._field_v7_existed[
                f"{self._schemas_id2short_id_v7[f['metadata_schema_id']]}.{f['element']}.{f['qualifier']}"] =\
                f['metadata_field_id']

        self._fields_v5 = read_json(field_file_v5_str) or []
        self._schemas_v5 = read_json(schema_file_v5_str) or []
        if not self._fields_v5:
            _logger.info(f"Empty input: [{field_file_v5_str}].")
        if not self._schemas_v5:
            _logger.info(f"Empty input: [{schema_file_v5_str}].")

        self._schema_id2short_id_v5 = {}
        for f in self._schemas_v5:
            self._schema_id2short_id_v5[f['metadata_schema_id']] = f['short_id']
        self._fields_id2v7id = {}
        self._fields_id2js_v5 = {x['metadata_field_id']: x for x in self._fields_v5}
        self._v5_fields_name2id = {}
        for f in self._fields_v5:
            self._v5_fields_name2id[(f"{self._schema_id2short_id_v5[f['metadata_schema_id']]}"
                                     f".{f['element']}.{f['qualifier']}")] = f['metadata_field_id']
        self._v7_fields_name2id = {}
        self._schemas_id2id_v5 = {}
        self._schemas_id2js_v5 = {x['metadata_schema_id']: x for x in self._schemas_v5}

        # read dynamically
        self._versions = {}

        self._imported = {
            "schema_imported": 0,
            "schema_existed": 0,
            "field_imported": 0,
            "field_existed": 0,
            "replaced_field": 0,
        }

        # clarin-dspace
        self._ignored_fields = [
            self.get_field_id_by_name_v5(name) for name in env["ignore"]["fields"]
        ]

        # dspace
        # fields which will be replaced in metadata
        self._replaced_fields = [
            self.get_field_id_by_name_v5(name) for name in env["replaced"]["fields"]
        ]

        # Find out which field is `local.sponsor`, check only `sponsor` string
        sponsor_field_id = -1
        sponsors = [x for x in self._fields_v5 if x['element'] == 'sponsor']
        if len(sponsors) != 1:
            _logger.warning(f"Found [{len(sponsors)}] elements with name [sponsor]")
        else:
            sponsor_field_id = sponsors[0]['metadata_field_id']

        # norm
        started = time.perf_counter()
        _logger.info("[METADATA] init: start")
        is_jsonl = value_file_v5_str.lower().endswith(".jsonl")
        if is_jsonl:
            values_iter = _iter_jsonl_with_progress(value_file_v5_str)
            orig_len = 0
            _logger.info(
                f"[METADATA] values loading from {value_file_v5_str} (streaming jsonl)")
        else:
            js_value = read_json(value_file_v5_str) or []
            if not js_value:
                _logger.info(f"Empty input: [{value_file_v5_str}].")
            values_iter = iter(js_value)
            orig_len = len(js_value)
            _logger.info(
                f"[METADATA] values loading from {value_file_v5_str} ({orig_len} rows)")

        handle_prefixes = env["dspace"]["handle_prefix"]
        if isinstance(handle_prefixes, str):
            handle_prefixes = [handle_prefixes]

        # normalize + filter + build indexes in a single pass to avoid extra large-list copies
        kept_len = 0
        pbar = None
        last_bytes = 0
        jsonl_total_bytes = os.path.getsize(value_file_v5_str) if is_jsonl else 0
        if is_jsonl:
            pbar = tqdm(
                total=jsonl_total_bytes if jsonl_total_bytes > 0 else None,
                desc="Index metadatavalue",
                unit="B",
                unit_scale=True,
                mininterval=1,
                dynamic_ncols=True,
            )
        else:
            pbar = tqdm(
                total=orig_len if orig_len > 0 else None,
                desc="Index metadatavalue",
                unit="rows",
                mininterval=1,
                dynamic_ncols=True,
            )

        for idx, val in enumerate(values_iter, start=1):
            bytes_read = 0
            total_bytes = 0
            if is_jsonl:
                val, bytes_read, total_bytes = val
                orig_len = idx
            field_id = val['metadata_field_id']
            # replace separator @@ by ;
            text_value = val['text_value'].replace("@@", ";")

            # replace `local.sponsor` data sequence
            # from `<ORG>;<PROJECT_CODE>;<PROJECT_NAME>;<TYPE>`
            # to `<TYPE>;<PROJECT_CODE>;<ORG>;<PROJECT_NAME>`
            if field_id == sponsor_field_id:
                text_value = metadatas._fix_local_sponsor(text_value)

            # ignore file preview in metadata and others
            if field_id in self.ignored_fields:
                continue

            kept_len += 1
            res_type_id = str(val['resource_type_id'])
            res_id = str(val['resource_id'])
            arr = self._values.setdefault(res_type_id, {}).setdefault(res_id, [])
            arr.append((
                field_id,
                text_value,
                val.get('text_lang'),
                val.get('authority'),
                val.get('confidence'),
                val.get('place'),
            ))

            # metadata_field_id 25 is Item's handle
            if field_id == self.V5_DC_IDENTIFIER_URI_ID and any(
                text_value.startswith(prefix) for prefix in handle_prefixes
            ):
                d = self._versions.get(text_value, {})
                d['item_id'] = val['resource_id']
                self._versions[text_value] = d

            if pbar is not None:
                if is_jsonl:
                    delta = max(0, bytes_read - last_bytes)
                    if delta > 0:
                        pbar.update(delta)
                        last_bytes = bytes_read
                else:
                    pbar.update(1)

        pbar.close()

        if orig_len != kept_len:
            ignored_field_names = []
            for field_id in self.ignored_fields:
                field = self._fields_id2js_v5.get(field_id)
                if field is None:
                    ignored_field_names.append(f"<unknown:{field_id}>")
                    continue

                schema_short_id = self._schema_id2short_id_v5.get(
                    field['metadata_schema_id'], "?")
                ignored_field_names.append(
                    f"{schema_short_id}.{field['element']}.{field['qualifier']}"
                )

            _logger.warning(
                f"Ignoring metadata fields [{self.ignored_fields}] names:[{ignored_field_names}], "
                f"len:[{orig_len}->{kept_len}]")

        # release large source array reference as early as possible
        if not is_jsonl:
            del js_value

        elapsed = time.perf_counter() - started
        _logger.info(
            f"[METADATA] init: done rows={orig_len} kept={kept_len} elapsed={elapsed:.1f}s"
        )

    def __len__(self):
        return sum(len(x) for x in self._values.values())

    # =====

    def prepare_field_name(self, name: str) -> Optional[str]:
        """
        Prepares the field name for lookup in v5 or v7 dictionaries.

        Rules:
        - If name contains more than two dots ('.'), return None (invalid).
        - If name contains less than one dot, return None (invalid).
        - If name contains exactly one dot, append '.None' suffix.
        - Otherwise, return name as is.
        """
        dots = name.count('.')
        if dots == 1:
            return f"{name}.None"
        if dots == 2:
            return name
        return None

    def get_field_id_by_name_v5(self, name: str):
        """
            Note:
                Multiple schemas should not have the same key, v7 would not allow it.

            select * from metadatafieldregistry where metadata_field_id=XXX ;
        """
        prepared_name = self.prepare_field_name(name)
        return self._v5_fields_name2id.get(prepared_name, None)

    def get_existed_field_by_name_v7(self, name: str):
        prepared_name = self.prepare_field_name(name)
        return self._field_v7_existed.get(prepared_name, None)

    @property
    def V5_DC_RELATION_REPLACES_ID(self):
        from_map = self.get_field_id_by_name_v5('dc.relation.replaces')
        if from_map is None:
            raise ValueError("Field ID for 'dc.relation.replaces' in v5 not found.")
        return from_map

    @property
    def V5_DC_RELATION_ISREPLACEDBY_ID(self):
        from_map = self.get_field_id_by_name_v5('dc.relation.isreplacedby')
        if from_map is None:
            raise ValueError("Field ID for 'dc.relation.isreplacedby' in v5 not found.")
        return from_map

    @property
    def V5_DC_IDENTIFIER_URI_ID(self):
        from_map = self.get_field_id_by_name_v5('dc.identifier.uri')
        if from_map is None:
            raise ValueError("Field ID for 'dc.identifier.uri' in v5 not found.")
        return from_map

    @property
    def V5_DATE_ISSUED(self):
        from_map = self.get_field_id_by_name_v5('dc.date.issued')
        if from_map is None:
            raise ValueError("Field ID for 'dc.date.issued' in v5 not found.")
        return from_map

    @property
    def V7_FIELD_ID_LIC(self):
        from_map = self.get_existed_field_by_name_v7('dc.rights.uri')
        if from_map is None:
            raise ValueError("Field ID for 'dc.rights.uri' in v7 not found.")
        return from_map

    @property
    def V7_FIELD_DATE_ISSUED(self):
        from_map = self.get_existed_field_by_name_v7('dc.date.issued')
        if from_map is None:
            raise ValueError("Field ID for 'dc.date.issued' in v7 not found.")
        return from_map

    @property
    def V7_FIELD_LANG_ADDED(self):
        from_map = self.get_existed_field_by_name_v7('local.language.name')
        if from_map is None:
            raise ValueError("Field ID for 'local.language.name' in v7 not found.")
        return from_map

    @property
    def V7_FIELD_ID_IDENTIFIER_URI(self):
        from_map = self.get_existed_field_by_name_v7('dc.identifier.uri')
        if from_map is None:
            raise ValueError("Field ID for 'dc.identifier.uri' in v7 not found.")
        return from_map

    @property
    def V7_FIELD_ID_TITLE(self):
        from_map = self.get_existed_field_by_name_v7('dc.title')
        if from_map is None:
            raise ValueError("Field ID for 'dc.title' in v7 not found.")
        return from_map

    @property
    def V7_FIELD_ID_PROVENANCE(self):
        from_map = self.get_existed_field_by_name_v7('dc.description.provenance')
        if from_map is None:
            raise ValueError("Field ID for 'dc.description.provenance' in v7 not found.")
        return from_map

    @property
    def schemas(self):
        return self._schemas_v5

    @property
    def fields(self):
        return self._fields_v5

    @property
    def versions(self):
        return self._versions

    @property
    def imported_schemas(self):
        return self._imported['schema_imported']

    def collection_default_read_groups(self):
        rec = re.compile(r"COLLECTION_(\d+)_DEFAULT_READ")
        out = {}
        for group_id_str, group_meta in self._values.get(str(6), {}).items():
            for _, text_value, _, _, _, _ in group_meta:
                m = rec.search(text_value or '')
                if m is None:
                    continue
                out[int(m.group(1))] = int(group_id_str)
        return out

    @property
    def existed_schemas(self):
        return self._imported['schema_existed']

    @property
    def imported_fields(self):
        return self._imported['field_imported']

    @property
    def existed_fields(self):
        return self._imported['field_existed']

    @property
    def replaced_fields(self):
        return self._replaced_fields

    @property
    def ignored_fields(self):
        return self._ignored_fields

    @time_method
    def import_to(self, dspace):
        self._import_schema(dspace)
        self._import_fields(dspace)

    # =============

    def schema_id(self, internal_id: int):
        return self._schemas_id2id_v5.get(str(internal_id), None)

    # =============

    def serialize(self, file_str: str):
        data = {
            "schemas_id2id_v5": self._schemas_id2id_v5,
            "fields_id2v7id": self._fields_id2v7id,
            "imported": self._imported,
            "v5_fields_name2id": self._v5_fields_name2id,
            "v7_fields_name2id": self._v7_fields_name2id,
        }
        serialize(file_str, data)

    def deserialize(self, file_str: str):
        data = deserialize(file_str)
        self._schemas_id2id_v5 = data["schemas_id2id_v5"]
        self._fields_id2v7id = data["fields_id2v7id"]
        self._imported = data["imported"]
        self._v5_fields_name2id = data["v5_fields_name2id"]
        self._v7_fields_name2id = data["v7_fields_name2id"]

    # =============

    @staticmethod
    def _fix_local_sponsor(wrong_sequence_str):
        """
            Replace `local.sponsor` data sequence
            from `<ORG>;<PROJECT_CODE>;<PROJECT_NAME>;<TYPE>;<EU_IDENTIFIER>`
            to `<TYPE>;<PROJECT_CODE>;<ORG>;<PROJECT_NAME>;<EU_IDENTIFIER>`
        """
        sep = ';'
        # sponsor list could have length 4 or 5
        sponsor_list = wrong_sequence_str.split(sep)
        org, p_code, p_name, p_type = sponsor_list[0:4]
        eu_id = '' if len(sponsor_list) < 5 else sponsor_list[4]
        # compose the `local.sponsor` sequence in the right way
        return sep.join([p_type, p_code, org, p_name, eu_id])

    def _import_schema(self, dspace):
        """
            Import data into database.
            Mapped tables: metadataschemaregistry
        """
        expected = len(self._schemas_v5)
        log_key = "metadata schemas"
        log_before_import(log_key, expected)

        # get all existing data from database table
        existed_schemas = dspace.fetch_metadata_schemas() or []

        def find_existing_with_ns(short_id: str, ns: str):
            return next((e for e in existed_schemas if e['prefix'] == short_id and e['namespace'] == ns), None)

        def find_existing_prefix(short_id: str):
            return next((e for e in existed_schemas if e['prefix'] == short_id), None)

        for schema in progress_bar(self._schemas_v5):
            meta_id = schema['metadata_schema_id']

            # exists in the database
            existing = find_existing_with_ns(schema['short_id'], schema['namespace'])
            if existing is not None:
                _logger.debug(
                    f'Metadataschemaregistry prefix: {schema["short_id"]} already exists!')
                self._imported["schema_existed"] += 1
                self._schemas_id2id_v5[str(meta_id)] = existing['id']
                continue

            # only prefix exists, but there is unique constraint on prefix in the database
            existing = find_existing_prefix(schema['short_id'])
            if existing is not None:
                _logger.warning(
                    f'Metadata_schema short_id {schema["short_id"]} '
                    f'exists in database with different namespace: {existing["namespace"]}.')
                self._imported["schema_existed"] += 1
                self._schemas_id2id_v5[str(meta_id)] = existing['id']
                continue

            data = {
                'namespace': schema['namespace'],
                'prefix': schema['short_id']
            }
            try:
                resp = dspace.put_metadata_schema(data)
                self._schemas_id2id_v5[str(meta_id)] = resp['id']
                self._imported["schema_imported"] += 1
            except Exception as e:
                _logger.error(
                    f'put_metadata_schema [{meta_id}] failed. Exception: {str(e)}')

        log_after_import(f'{log_key} [existed:{self.existed_schemas}]',
                         expected, self.imported_schemas + self.existed_schemas)

    def _import_fields(self, dspace):
        """
            Import data into database.
            Mapped tables: metadatafieldregistry
        """
        expected = len(self._fields_v5)
        log_key = "metadata fields"
        log_before_import(log_key, expected)

        existed_fields = dspace.fetch_metadata_fields() or []

        def find_existing(field):
            schema_id = field['metadata_schema_id']
            sch_id = self.schema_id(schema_id)
            if sch_id is None:
                return None
            for e in existed_fields:
                if e['_embedded']['schema']['id'] != sch_id or \
                        e['element'] != field['element'] or \
                        e['qualifier'] != field['qualifier']:
                    continue
                return e
            return None

        existing_arr = []
        for field in progress_bar(self._fields_v5):
            field_id = field["metadata_field_id"]
            schema_id = field['metadata_schema_id']
            e = field['element']
            q = field['qualifier']

            existing = find_existing(field)
            if existing is not None:
                _logger.debug(f'Metadatafield: {e}.{q} already exists!')
                existing_arr.append(field)
                ext_field_id = existing['id']
                self._imported["field_existed"] += 1
            elif field_id in self.replaced_fields:
                self._imported["replaced_field"] += 1
                continue
            else:
                data = {
                    'element': field['element'],
                    'qualifier': field['qualifier'],
                    'scopeNote': field['scope_note']
                }
                params = {'schemaId': self.schema_id(schema_id)}
                try:
                    resp = dspace.put_metadata_field(data, params)
                    ext_field_id = resp['id']
                    self._imported["field_imported"] += 1
                except Exception as e:
                    _logger.error(
                        f'put_metadata_field [{str(field_id)}] failed. Exception: {str(e)}')
                    continue

            self._fields_id2v7id[str(field_id)] = ext_field_id

        log_after_import(f'{log_key} [existing:{self.existed_fields}]',
                         expected, self.imported_fields + self.existed_fields)

    def _get_key_v1(self, val):
        """
            Using dspace backend.
        """
        int_meta_field_id = val['metadata_field_id']
        try:
            ext_meta_field_id = self.get_field_id(int_meta_field_id)
            field_js = self._dspace.fetch_metadata_field(ext_meta_field_id)
            if field_js is None:
                return None
        except Exception as e:
            _logger.error(f'fetch_metadata_field request failed. Exception: [{str(e)}]')
            return None

        # get metadataschema
        try:
            obj_id = field_js['_embedded']['schema']['id']
            schema_js = self._dspace.fetch_schema(obj_id)
            if schema_js is None:
                return None
        except Exception as e:
            _logger.error(f'fetch_schema request failed. Exception: [{str(e)}]')
            return None

        # define and insert key and value of dict
        key = schema_js['prefix'] + '.' + field_js['element']
        if field_js['qualifier']:
            key += '.' + field_js['qualifier']
        return key

    def _get_key_v2_by_field_id(self, int_meta_field_id: int):
        """Resolve a v5 metadata_field_id to its qualified schema key (e.g. 'dc.title.None')."""
        field_js = self._fields_id2js_v5.get(int_meta_field_id, None)
        if field_js is None:
            return None
        schema_id = field_js["metadata_schema_id"]
        schema_js = self._schemas_id2js_v5.get(schema_id, None)
        if schema_js is None:
            return None
        key = schema_js['short_id'] + '.' + field_js['element']
        if field_js['qualifier']:
            key += '.' + field_js['qualifier']
        return key

    def filter_res_d(self, res_d, ignored_mtd_fields):
        """
            Filter the resulting res_d dictionary based on custom ignored metadata fields.
        """
        return {key: val for key, val in (res_d or {}).items() if key not in ignored_mtd_fields}

    def replace_meta_val(self, res_d, replace_d):
        """
        Replace the mtd field in res_d with corresponding values from replace_d if the key exists in replace_d.
        """
        for old_key, val in list((res_d or {}).items()):
            if old_key in replace_d:
                res_d[replace_d[old_key]] = res_d.pop(old_key)
        return res_d

    def value(self, res_type_id: int, res_id: int, text_for_field_id: int = None, log_missing: bool = True):
        """
            Get metadata value for dspace object.
        """
        res_type_id = str(res_type_id)
        res_id = str(res_id)
        log_miss = _logger.info if log_missing else _logger.debug

        if res_type_id not in self._values:
            log_miss(f'Metadata missing [{res_type_id}] type')
            return None
        tp_values = self._values[res_type_id]
        if res_id not in tp_values:
            log_miss(f'Metadata for [{res_id}] are missing in [{res_type_id}] type')
            return None

        vals = tp_values[res_id]

        vals = [x for x in vals if (self.exists_field(
            x[0]) or x[0] in self.replaced_fields)]
        if len(vals) == 0:
            return {}

        # special case - return only text_value
        if text_for_field_id is not None:
            vals = [text_value for field_id, text_value, _, _,
                    _, _ in vals if field_id == text_for_field_id]
            return vals

        res_d = {}
        # create list of object metadata
        for field_id, text_value, text_lang, authority, confidence, place in vals:
            key = self._get_key_v2_by_field_id(field_id)
            if key is None:
                continue

            # if key != key2:
            #     _logger.critical(f"Incorrect v2 impl.")

            d = {
                'value': text_value,
                'language': text_lang,
                'authority': authority,
                'confidence': confidence,
                'place': place
            }
            res_d.setdefault(key, []).append(d)

        return res_d

    def exists_field(self, id: int) -> bool:
        return str(id) in self._fields_id2v7id

    def get_field_id(self, id: int) -> int:
        return self._fields_id2v7id[str(id)]
