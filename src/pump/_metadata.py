import logging
import re
from ._utils import read_json, time_method, serialize, deserialize, progress_bar, log_before_import, log_after_import

_logger = logging.getLogger("pump.metadata")


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
        return text.replace("\u2028", "\n").rstrip()

    rec_complex_funds = re.compile("(euFunds|nationalFunds|ownFunds|@@Other)")
    v5data_new = []
    V5_FIELD_ID_APPROX_DATE = repo.metadatas.get_field_id_by_name_v5(
        "approximateDate.issued")
    specific_fields = {
        V5_FIELD_ID_APPROX_DATE: [],
        repo.metadatas.V5_DATE_ISSUED: [],
    }

    for res_id, res_type_id, text, field_id in v5data:
        # ignore '0000', 15 -> we do not store unknown dates
        if field_id == repo.metadatas.V5_DATE_ISSUED and text == "0000":
            continue

        # ignore file preview in metadata
        if field_id in metadatas.IGNORE_FIELDS:
            continue

        uuid = repo.uuid(res_type_id, res_id)
        if uuid is None:
            _logger.debug(
                f"Cannot find uuid for [{res_type_id}] [{res_id}] [{str(text)}]")

        if field_id in specific_fields.keys():
            specific_fields[field_id].append(uuid)

        field_id_v7 = repo.metadatas.get_field_id(field_id)
        #
        if "@@" in text:
            splits = text.split("@@")
            new_splits = splits

            if field_id_v7 not in (repo.metadatas.V7_FIELD_ID_PROVENANCE,):
                if len(splits) == 5:
                    new_splits = [splits[-2], splits[1], splits[0], splits[2], splits[-1]]
                # special case - older complex field impl.
                elif len(splits) == 4 and rec_complex_funds.search(text) is not None:
                    new_splits = [splits[3], splits[1], splits[0], splits[2], '']
            text = ";".join(new_splits)

        # license def
        if field_id_v7 == repo.metadatas.V7_FIELD_ID_LIC:
            text = norm_lic(text)

        # groups have titles in table
        if field_id_v7 == repo.metadatas.V7_FIELD_ID_TITLE and res_type_id == repo.groups.TYPE:
            continue

        text = norm_text(text)
        v5data_new.append((uuid, text, field_id_v7))

    # cleanup
    # has local.approximateDate.issued -> ignore dc.date.issued
    to_check_dates_uuids = set(specific_fields[V5_FIELD_ID_APPROX_DATE]).intersection(
        set(specific_fields[repo.metadatas.V5_DATE_ISSUED])
    )
    for to_check_uuid in to_check_dates_uuids:
        for i, v in enumerate(v5data_new):
            if v is None:
                continue
            if to_check_uuid == v[0] and v[2] == repo.metadatas.V7_FIELD_DATE_ISSUED:
                v5data_new[i] = None
                break
    v5data_new = [x for x in v5data_new if x is not None]

    v7data_new = []
    for uuid, text, field_id in v7data:
        # added language description in addition to language code
        if field_id == repo.metadatas.V7_FIELD_LANG_ADDED:
            continue

        # should be already ignored # imported preview data
        # if field_id == 147:
        #     continue

        # license def
        if field_id == repo.metadatas.V7_FIELD_ID_LIC:
            text = norm_lic(text)

        if field_id == repo.metadatas.V7_FIELD_ID_IDENTIFIER_URI:
            text = text.replace("http://dev-5.pc:88/handle/", "http://hdl.handle.net/")

        text = norm_text(text)
        v7data_new.append((uuid, text, field_id))

    _logger.info(
        f"Changed v5 metadata values to match v7: {len(v5data)} -> {len(v5data_new)}")
    _logger.info(
        f"Changed v7 metadata values to match v7: {len(v7data)} -> {len(v7data_new)}")
    return v5data_new, v7data_new


class metadatas:
    """
        SQL:
            delete from metadatavalue ; delete from metadatafieldregistry ; delete from metadataschemaregistry ;
    """

    # clarin-dspace=# select * from metadatafieldregistry  where metadata_field_id=176 ;
    #  metadata_field_id | metadata_schema_id |  element  | qualifier |               scope_note
    # -------------------+--------------------+-----------+-----------+----------------------------------------
    #                176 |                  3 | bitstream | file      | Files inside a bitstream if an archive
    IGNORE_FIELDS = [
        176
    ]

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

    def __init__(self, env, dspace, value_file_str: str, field_file_str: str, schema_file_str: str):
        self._dspace = dspace
        self._values = {}

        self._fields = read_json(field_file_str)
        self._fields_id2v7id = {}
        self._fields_id2js = {x['metadata_field_id']: x for x in self._fields}
        self._v5_fields_name2id = {}
        self._v7_fields_name2id = {}
        for f in self.fields:
            self._v5_fields_name2id[f"{f['element']}.{f['qualifier']}"] = f['metadata_field_id']

        self._schemas = read_json(schema_file_str)
        self._schemas_id2id = {}
        self._schemas_id2js = {x['metadata_schema_id']: x for x in self._schemas}

        # read dynamically
        self._versions = {}

        self._imported = {
            "schema_imported": 0,
            "schema_existed": 0,
            "field_imported": 0,
            "field_existed": 0,
        }

        # Find out which field is `local.sponsor`, check only `sponsor` string
        sponsor_field_id = -1
        sponsors = [x for x in self._fields if x['element'] == 'sponsor']
        if len(sponsors) != 1:
            _logger.warning(f"Found [{len(sponsors)}] elements with name [sponsor]")
        else:
            sponsor_field_id = sponsors[0]['metadata_field_id']

        # norm
        js_value = read_json(value_file_str)
        for val in js_value:
            # replace separator @@ by ;
            val['text_value'] = val['text_value'].replace("@@", ";")

            # replace `local.sponsor` data sequence
            # from `<ORG>;<PROJECT_CODE>;<PROJECT_NAME>;<TYPE>`
            # to `<TYPE>;<PROJECT_CODE>;<ORG>;<PROJECT_NAME>`
            if val['metadata_field_id'] == sponsor_field_id:
                val['text_value'] = metadatas._fix_local_sponsor(val['text_value'])

        # ignore file preview in metadata and others
        orig_len = len(js_value)
        js_value = [x for x in js_value if x["metadata_field_id"]
                    not in metadatas.IGNORE_FIELDS]
        if orig_len != len(js_value):
            _logger.warning(
                f"Ignoring metadata fields [{metadatas.IGNORE_FIELDS}], len:[{orig_len}->{len(js_value)}]")

        # fill values
        for val in js_value:
            res_type_id = str(val['resource_type_id'])
            res_id = str(val['resource_id'])
            arr = self._values.setdefault(res_type_id, {}).setdefault(res_id, [])
            arr.append(val)

        # fill values
        for val in js_value:
            # Store item handle and item id connection in dict
            if not val['text_value'].startswith(env["dspace"]["handle_prefix"]):
                continue

            # metadata_field_id 25 is Item's handle
            if val['metadata_field_id'] == self.V5_DC_IDENTIFIER_URI_ID:
                d = self._versions.get(val['text_value'], {})
                d['item_id'] = val['resource_id']
                self._versions[val['text_value']] = d

    def __len__(self):
        return sum(len(x) for x in self._values.values())

    # =====

    def get_field_id_by_name_v5(self, name: str):
        """
            Note:
                Multiple schemas should not have the same key, v7 would not allow it.

            select * from metadatafieldregistry where metadata_field_id=XXX ;
        """
        return self._v5_fields_name2id.get(name, None)

    @property
    def V5_DC_RELATION_REPLACES_ID(self):
        from_map = self.get_field_id_by_name_v5('relation.replaces')
        assert 50 == from_map
        return from_map

    @property
    def V5_DC_RELATION_ISREPLACEDBY_ID(self):
        from_map = self.get_field_id_by_name_v5('relation.isreplacedby')
        assert 51 == from_map
        return from_map

    @property
    def V5_DC_IDENTIFIER_URI_ID(self):
        from_map = self.get_field_id_by_name_v5('identifier.uri')
        assert 25 == from_map
        return from_map

    @property
    def V5_DATE_ISSUED(self):
        from_map = self.get_field_id_by_name_v5('date.issued')
        assert 15 == from_map
        return from_map

    @property
    def V7_FIELD_ID_LIC(self):
        return 63
        from_map = self.get_field_id_by_name('date.issued')
        assert 63 == from_map
        return from_map

    @property
    def V7_FIELD_DATE_ISSUED(self):
        return 22

    @property
    def V7_FIELD_LANG_ADDED(self):
        return 149
        from_map = self.get_field_id_by_name('date.issued')
        assert 149 == from_map
        return from_map

    @property
    def V7_FIELD_ID_IDENTIFIER_URI(self):
        return 34
        from_map = self.get_field_id_by_name('identififer.uri')
        assert 34 == from_map
        return from_map

    @property
    def V7_FIELD_ID_TITLE(self):
        return 74
        from_map = self.get_field_id_by_name('title.None')
        assert 74 == from_map
        return from_map

    @property
    def V7_FIELD_ID_PROVENANCE(self):
        return 37
        from_map = self.get_field_id_by_name('provenance.None')
        assert 37 == from_map
        return from_map

    # =====

    @property
    def schemas(self):
        return self._schemas

    @property
    def fields(self):
        return self._fields

    @property
    def versions(self):
        return self._versions

    @property
    def imported_schemas(self):
        return self._imported['schema_imported']

    @property
    def existed_schemas(self):
        return self._imported['schema_existed']

    @property
    def imported_fields(self):
        return self._imported['field_imported']

    @property
    def existed_fields(self):
        return self._imported['field_existed']

    @time_method
    def import_to(self, dspace):
        self._import_schema(dspace)
        self._import_fields(dspace)

    # =============

    def schema_id(self, internal_id: int):
        return self._schemas_id2id.get(str(internal_id), None)

    # =============

    def serialize(self, file_str: str):
        data = {
            "schemas_id2id": self._schemas_id2id,
            "fields_id2v7id": self._fields_id2v7id,
            "imported": self._imported,
            "v5_fields_name2id": self._v5_fields_name2id,
            "v7_fields_name2id": self._v7_fields_name2id,
        }
        serialize(file_str, data)

    def deserialize(self, file_str: str):
        data = deserialize(file_str)
        self._schemas_id2id = data["schemas_id2id"]
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
        expected = len(self._schemas)
        log_key = "metadata schemas"
        log_before_import(log_key, expected)

        # get all existing data from database table
        existed_schemas = dspace.fetch_metadata_schemas() or []

        def find_existing_with_ns(short_id: str, ns: str):
            return next((e for e in existed_schemas if e['prefix'] == short_id and e['namespace'] == ns), None)

        def find_existing_prefix(short_id: str):
            return next((e for e in existed_schemas if e['prefix'] == short_id), None)

        for schema in progress_bar(self._schemas):
            meta_id = schema['metadata_schema_id']

            # exists in the database
            existing = find_existing_with_ns(schema['short_id'], schema['namespace'])
            if existing is not None:
                _logger.debug(
                    f'Metadataschemaregistry prefix: {schema["short_id"]} already exists!')
                self._imported["schema_existed"] += 1
                self._schemas_id2id[str(meta_id)] = existing['id']
                continue

            # only prefix exists, but there is unique constraint on prefix in the databse
            existing = find_existing_prefix(schema['short_id'])
            if existing is not None:
                _logger.warning(
                    f'Metadata_schema short_id {schema["short_id"]} '
                    f'exists in database with different namespace: {existing["namespace"]}.')
                self._imported["schema_existed"] += 1
                self._schemas_id2id[str(meta_id)] = existing['id']
                continue

            data = {
                'namespace': schema['namespace'],
                'prefix': schema['short_id']
            }
            try:
                resp = dspace.put_metadata_schema(data)
                self._schemas_id2id[str(meta_id)] = resp['id']
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
        expected = len(self._fields)
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
        for field in progress_bar(self._fields):
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

    def _get_key_v2(self, val):
        """
            Using data.
        """
        int_meta_field_id = val['metadata_field_id']
        field_js = self._fields_id2js.get(int_meta_field_id, None)
        if field_js is None:
            return None
        # get metadataschema
        schema_id = field_js["metadata_schema_id"]
        schema_js = self._schemas_id2js.get(schema_id, None)
        if schema_js is None:
            return None
        # define and insert key and value of dict
        key = schema_js['short_id'] + '.' + field_js['element']
        if field_js['qualifier']:
            key += '.' + field_js['qualifier']
        return key

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

        vals = [x for x in vals if self.exists_field(x['metadata_field_id'])]
        if len(vals) == 0:
            return {}

        # special case - return only text_value
        if text_for_field_id is not None:
            vals = [x['text_value']
                    for x in vals if x['metadata_field_id'] == text_for_field_id]
            return vals

        res_d = {}
        # create list of object metadata
        for val in vals:
            # key = self._get_key_v1(val)
            key = self._get_key_v2(val)

            # if key != key2:
            #     _logger.critical(f"Incorrect v2 impl.")

            d = {
                'value': val['text_value'],
                'language': val['text_lang'],
                'authority': val['authority'],
                'confidence': val['confidence'],
                'place': val['place']
            }
            res_d.setdefault(key, []).append(d)

        return res_d

    def exists_field(self, id: int) -> bool:
        return str(id) in self._fields_id2v7id

    def get_field_id(self, id: int) -> int:
        return self._fields_id2v7id[str(id)]
