import logging
from datetime import datetime
from ._utils import read_json, serialize, deserialize, time_method, progress_bar, log_before_import, log_after_import

_logger = logging.getLogger("pump.item")

# Format (strptime format, suffix to append, description)
DATE_FORMATS = [
    ('%Y-%m-%d', None, 'YYYY-MM-DD'),     # 2024-01-15
    ('%Y-%m', '-01', 'YYYY-MM'),          # 2024-01 → 2024-01-01
    ('%Y', '-01-01', 'YYYY'),             # 2024 → 2024-01-01
    ('%d %b. %Y', None, 'DD MMM. YYYY'),  # 15 Jan. 2024
    ('%d %b %Y', None, 'DD MMM YYYY'),    # 15 Jan 2024
]


class items:
    """
        SQL:
            delete from workspaceitem ;

            Required Configuration in project_settings.py:
                version_date_fields: List of date fields to try when migrating versions.
                                    Fields are tried in order until one with a value is found.
                                    If none are found, version import is skipped with critical error.
                                    This configuration is REQUIRED and must be explicitly set.
    """
    TYPE = 2
    validate_table = [
        ["item", {
            # do not use compare because of email field (GDPR)
            "nonnull": ["in_archive", "withdrawn"],
        }],
        ["item2bundle", {
            # do not use compare because of email field (GDPR)
            "nonnull": ["bundle_id"],
        }],
        ["versionhistory", {
        }],
        ["workspaceitem", {
        }],
        ["collection2item", {
            "len": {
                "sql": "select distinct collection_id, item_id from public.collection2item group by collection_id, item_id",
            }
        }],
    ]

    replaced_fields = {'local.hasMetadata': 'local.hasCMDI'}

    test_table = [
        {
            "name": "item_hasMetadata_to_hasCMDI",
            "left": ["sql", "db7", "one", "SELECT count(*) FROM metadatavalue where metadata_field_id in "
                                          "(select metadata_field_id from metadatafieldregistry where element = 'hasCMDI')"],
            "right": ["sql", "dspace5", "one", "SELECT count(*) FROM metadatavalue where metadata_field_id in "
                                               "(select metadata_field_id from metadatafieldregistry where element = 'hasMetadata')"]
        },
        {
            "name": "item_hasMetadata",
            "left": ["sql", "db7", "one", "SELECT count(*) FROM metadatavalue where metadata_field_id in "
                                          "(select metadata_field_id from metadatafieldregistry where element = 'hasMetadata')"],
            "right": ["val", 0]
        }
    ]

    def __init__(self,
                 item_file_str: str,
                 ws_file_str: str,
                 wf_file_str: str,
                 col2item_file_str: str):

        self._items = read_json(item_file_str) or []
        if not self._items:
            _logger.info(f"Empty input: [{item_file_str}].")

        self._ws_items = read_json(ws_file_str) or []
        if not self._ws_items:
            _logger.info(f"Empty input: [{ws_file_str}].")

        self._wf_items = read_json(wf_file_str) or []
        if not self._wf_items:
            _logger.info(f"Empty input: [{wf_file_str}].")

        self._col2item = read_json(col2item_file_str) or []
        if not self._col2item:
            _logger.info(f"Empty input: [{col2item_file_str}].")

        self._id2item = {str(e['item_id']): e for e in self._items}
        self._id2uuid = {}
        self._ws_id2v7id = {}
        self._ws_id2uuid = {}
        self._wf_id2workflow_id = {}
        self._wf_item_ids = []
        self._col_id2uuid = {}
        self._migrated_versions = []

        self._imported = {
            "items": 0,
            "wf": 0,
            "ws": 0,
            "cols": 0,
            "versions": 0,
        }
        self._done = []
        self._versions = {
            "not_imported_handles": [],
            "withdrawn": [],
            "not_imported": [],
        }

    def __len__(self):
        return len(self._items or {})

    def find_by_uuid(self, uuid: str):
        for k, item_uuid in self._id2uuid.items():
            if uuid == item_uuid:
                return self._id2item[k]
        return None

    def uuid(self, eid: int):
        assert isinstance(list(self._id2uuid.keys() or [""])[0], str)
        return self._id2uuid.get(str(eid), None)

    def wf_id(self, wfid: int):
        return self._wf_id2workflow_id.get(str(wfid), None)

    @property
    def imported_ws(self):
        return self._imported['ws']

    @property
    def imported_wf(self):
        return self._imported['wf']

    @property
    def imported_cols(self):
        return self._imported['cols']

    @property
    def imported(self):
        return self._imported['items']

    def item(self, item_id: int):
        return self._id2item[str(item_id)]

    @time_method
    def import_to(self, cache_file, dspace, handles, metadatas, epersons, collections):
        """
            Import data into database.
            Mapped tables: item, collection2item, workspaceitem, cwf_workflowitem,
            metadata, handle
        """
        if "ws" in self._done:
            _logger.info("Skipping workspace import")
        else:
            if self._ws_items is not None:
                self._ws_import_to(dspace, handles, metadatas, epersons, collections)
            self._done.append("ws")
            self.serialize(cache_file)

        if "wf" in self._done:
            _logger.info("Skipping workflow import")
        else:
            if self._wf_items is not None:
                self._wf_import_to(dspace, handles, metadatas, epersons, collections)
            self._done.append("wf")
            self.serialize(cache_file)

        if "item" in self._done:
            _logger.info("Skipping item import")
        else:
            self._item_import_to(dspace, handles, metadatas, epersons, collections)
            self._done.append("item")
            self.serialize(cache_file)

        if "itemcol" in self._done:
            _logger.info("Skipping itemcol import")
        else:
            self._itemcol_import_to(dspace, handles, metadatas, epersons, collections)
            self._done.append("itemcol")
            self.serialize(cache_file)

    def _import_item(self, dspace, generic_item_d, item, handles, metadatas, epersons, collections, what: str) -> bool:
        i_id = item['item_id']

        data = {
            'discoverable': item['discoverable'],
            'inArchive': item['in_archive'],
            'lastModified': item['last_modified'],
            'withdrawn': item['withdrawn']
        }
        i_meta = metadatas.replace_meta_val(metadatas.value(
            items.TYPE, i_id, None, True), self.replaced_fields)
        if i_meta is not None:
            data['metadata'] = i_meta

        i_handle = handles.get(items.TYPE, i_id)
        if i_handle is not None:
            data['handle'] = i_handle
        else:
            log_fnc = _logger.info
            # workspace do not need to have handle
            if what == "workspace":
                log_fnc = _logger.debug
            log_fnc(f"Cannot find handle for item in {what} [{i_id}]")

        # the params are workspaceitem attributes
        params = {
            'owningCollection': collections.uuid(generic_item_d['collection_id']),
            'multipleTitles': generic_item_d['multiple_titles'],
            'publishedBefore': generic_item_d['published_before'],
            'multipleFiles': generic_item_d['multiple_files'],
            'stageReached': generic_item_d.get('stage_reached', -1),
            'pageReached': generic_item_d.get('page_reached', -1),
            'epersonUUID': epersons.uuid(item['submitter_id'])
        }

        try:
            resp = dspace.put_ws_item(params, data)
            ws_id = resp['id']
            if what == "workspace":
                self._ws_id2v7id[str(i_id)] = ws_id
        except Exception as e:
            _logger.error(f'put_ws_item: [{i_id}] failed [{str(e)}]')
            return False, None

        try:
            resp = dspace.fetch_item(ws_id)
            i_uuid = resp['id']
            self._id2uuid[str(i_id)] = i_uuid
            if what == "workspace":
                self._ws_id2uuid[str(i_id)] = i_uuid
        except Exception as e:
            _logger.error(f'fetch_item: [{ws_id}] failed [{str(e)}]')
            return False, None

        return True, ws_id

    def _ws_import_to(self, dspace, handles, metadatas, epersons, collections):
        expected = len(self._ws_items or {})
        log_key = "workspaceitems"
        log_before_import(log_key, expected)

        for ws in progress_bar(self._ws_items):
            item = self.item(ws['item_id'])
            ret, _1 = self._import_item(dspace, ws, item, handles,
                                        metadatas, epersons, collections, "workspace")
            if ret:
                self._imported["ws"] += 1

        log_after_import(log_key, expected, self.imported_ws)

    def _wf_import_to(self, dspace, handles, metadatas, epersons, collections):
        expected = len(self._wf_items or {})
        log_key = "workflowitems"
        log_before_import(log_key, expected)

        # create workflowitem
        # workflowitem is created from workspaceitem
        # -1, because the workflowitem doesn't contain this attribute
        for wf in progress_bar(self._wf_items):
            wf_id = wf['item_id']
            item = self.item(wf_id)
            ret, ws_id = self._import_item(dspace, wf, item, handles,
                                           metadatas, epersons, collections, "workflow")
            if not ret:
                continue

            # create workflowitem from created workspaceitem
            params = {'id': str(ws_id)}
            try:
                resp = dspace.put_wf_item(params)
                self._wf_id2workflow_id[str(wf['workflow_id'])
                                        ] = resp.headers['workflowitem_id']
                self._wf_item_ids.append(wf_id)
                self._imported["wf"] += 1
            except Exception as e:
                _logger.error(f'put_wf_item: [{wf_id}] failed [{str(e)}]')

        log_after_import(log_key, expected, self.imported_wf)

    def _item_import_to(self, dspace, handles, metadatas, epersons, collections):
        expected = len(self._items or {})
        log_key = "items"
        log_before_import(log_key, expected)

        without_col = 0

        ws_items = 0
        wf_items = 0

        # create other items
        for item in progress_bar(self._items):
            i_id = item['item_id']

            # is it already imported in WS?
            if str(i_id) in self._ws_id2v7id:
                ws_items += 1
                continue
            if i_id in self._wf_item_ids:
                wf_items += 1
                continue

            data = {
                'discoverable': item['discoverable'],
                'inArchive': item['in_archive'],
                'lastModified': item['last_modified'],
                'withdrawn': item['withdrawn']
            }

            i_meta = metadatas.replace_meta_val(metadatas.value(
                items.TYPE, i_id, None, True), self.replaced_fields)
            if i_meta:
                data['metadata'] = i_meta

            i_handle = handles.get(items.TYPE, i_id)
            if i_handle is None:
                _logger.critical(f"Cannot find handle for item [{i_id}]")
                continue

            data['handle'] = i_handle

            if item['owning_collection'] is None:
                _logger.critical(f"Item without collection [{i_id}] is not valid!")
                without_col += 1
                continue

            col_uuid = collections.uuid(item['owning_collection'])
            params = {
                'owningCollection': col_uuid,
                'epersonUUID': epersons.uuid(item['submitter_id']),
            }

            if col_uuid is None:
                _logger.critical(
                    f"Item without collection [{i_id}] cannot be imported here")
                continue

            try:
                resp = dspace.put_item(params, data)
                if resp is None:
                    _logger.error(
                        f'put_item: [{i_id}] failed - server returned None (detailed error logged above by REST client)')
                else:
                    self._id2uuid[str(i_id)] = resp['id']
                    self._imported["items"] += 1
            except Exception as e:
                _logger.error(f'put_item: [{i_id}] failed [{str(e)}]')

        log_after_import(f'{log_key} no owning col:[{without_col}], ws items:[{ws_items}] wf items:[{wf_items}]',
                         expected, self.imported + without_col + ws_items + wf_items)

    def _itemcol_import_to(self, dspace, handles, metadatas, epersons, collections):
        # Find items which are mapped in more collections and store them into dictionary in this way
        # {'item_uuid': [collection_uuid_1, collection_uuid_2]}
        for col in self._col2item:
            col_item_id = col['item_id']
            # Every item should have mapped only one collection - the owning collection except the items which
            # are mapped into more collections
            item_uuid = self.uuid(col_item_id)
            if item_uuid is None:
                _logger.critical(f"Cannot find collection of item [{col_item_id}]")
                continue
            col_uuid = collections.uuid(col['collection_id'])
            self._col_id2uuid.setdefault(item_uuid, []).append(col_uuid)

        to_import = [x for x in (self._col_id2uuid or {}).items() if len(x[1]) > 1]
        expected = len(to_import)
        log_key = "items coll"
        log_before_import(log_key, expected)

        # Call Vanilla REST endpoint which add relation between Item and Collection into the collection2item table
        for item_uuid, cols in progress_bar(to_import):
            if len(cols) < 2:
                continue
            try:
                data = self._col_id2uuid[item_uuid]
                dspace.put_item_to_col(item_uuid, data)
                self._imported['cols'] += 1
            except Exception as e:
                _logger.error(f'put_item_to_col: [{item_uuid}] failed [{str(e)}]')

        log_after_import(log_key, expected, self.imported_cols)

    # =============

    def serialize(self, file_str: str):
        data = {
            "items": self._items,
            "ws_items": self._ws_items,
            "wf_items": self._wf_items,
            "col2item": self._col2item,
            "id2item": self._id2item,
            "id2uuid": self._id2uuid,
            "ws_id2v7id": self._ws_id2v7id,
            "ws_id2uuid": self._ws_id2uuid,
            "wf_id2uuid": self._wf_id2workflow_id,
            "wf_item_ids": self._wf_item_ids,
            "col_id2uuid": self._col_id2uuid,
            "imported": self._imported,
            "done": self._done,
            "versions": self._versions,
            "migrated_versions": self._migrated_versions,
        }
        serialize(file_str, data)

    def deserialize(self, file_str: str):
        data = deserialize(file_str)
        self._items = data["items"]
        self._ws_items = data["ws_items"]
        self._wf_items = data["wf_items"]
        self._col2item = data["col2item"]
        self._id2item = data["id2item"]
        self._id2uuid = data["id2uuid"]
        self._ws_id2v7id = data["ws_id2v7id"]
        self._ws_id2uuid = data["ws_id2uuid"]
        self._wf_id2workflow_id = data["wf_id2uuid"]
        self._wf_item_ids = data.get("wf_item_ids", [])
        self._col_id2uuid = data["col_id2uuid"]
        self._imported = data["imported"]
        self._done = data["done"]
        self._versions = data["versions"]
        self._migrated_versions = data.get("migrated_versions", [])

    def iter_protocol_variants(self, handle: str):
        """Yield alternative protocol variants of a handle (http <-> https)."""
        if handle.startswith("https://"):
            yield handle.replace("https://", "http://", 1)
        elif handle.startswith("http://"):
            yield handle.replace("http://", "https://", 1)

    def _normalize_version_date(self, version_date_issued: str, item_uuid: str):
        """
        Normalize and validate version date string to YYYY-MM-DD format.
        Returns normalized date in YYYY-MM-DD format if valid or None if invalid (error is logged)
        """
        for date_format, suffix, format_desc in DATE_FORMATS:
            try:
                # Parse and validate the date using strptime
                parsed_date = datetime.strptime(version_date_issued, date_format)

                # Check year is in reasonable range (1000-9999)
                if not (1000 <= parsed_date.year <= 9999):
                    _logger.error(
                        f"Invalid year for item UUID {item_uuid}: '{version_date_issued}'. "
                        "Year must be between 1000 and 9999. Skipping version import."
                    )
                    return None

                # Convert to YYYY-MM-DD format
                if suffix:
                    # For incomplete dates (YYYY or YYYY-MM), append the suffix
                    normalized_date = version_date_issued + suffix
                    _logger.info(
                        f"Date for item UUID {item_uuid} was '{version_date_issued}' ({format_desc}). "
                        f"Normalized to {normalized_date}."
                    )
                else:
                    # Convert parsed date to YYYY-MM-DD format
                    normalized_date = parsed_date.strftime('%Y-%m-%d')
                    if normalized_date != version_date_issued:
                        _logger.info(
                            f"Date for item UUID {item_uuid} was '{version_date_issued}' ({format_desc}). "
                            f"Normalized to {normalized_date}."
                        )

                return normalized_date

            except ValueError:
                continue

        # No format matched
        format_list = ', '.join([desc for _, _, desc in DATE_FORMATS])
        _logger.error(
            f"Invalid date format for item UUID {item_uuid}: '{version_date_issued}'. "
            f"Expected one of: {format_list}. Skipping version import."
        )
        return None

    def _migrate_versions(self, env, db7, db5_dspace, metadatas):
        _logger.info(
            f"Migrating versions [{len(self._id2item or {})}], "
            f"already done:[{len(self._migrated_versions or [])}]")

        admin_username = env["backend"]["user"]
        admin_uuid = db7.get_admin_uuid(admin_username)

        self._migrated_versions = []

        # Get version date fields from project settings
        # Must be configured in project_settings.py as version_date_fields
        date_fields_to_try = env.get("version_date_fields")
        if not date_fields_to_try:
            _logger.critical("version_date_fields not configured in project settings!")
            raise ValueError(
                "version_date_fields configuration is required but not found in project settings")

        # Migrate versions for every Item
        for item_id, item in progress_bar(self._id2item.items()):
            # Do not process versions of the item that have already been processed.
            if item_id in self._migrated_versions:
                continue

            # This sequence contains handles of all versions of the Item ordered from the first version to the latest one
            versions = self.get_all_versions(item_id, metadatas)

            # Do not process item which does not have any version
            if len(versions or []) == 0:
                continue

            _logger.debug(f'Processing all versions for the item with ID: {item_id}')

            # All versions of this Item are going to be processed
            # Insert data into `versionhistory` table
            versionhistory_new_id = db7.get_last_id(
                'versionhistory', 'versionhistory_id') + 1
            db7.exe_sql(f"""
INSERT INTO versionhistory(versionhistory_id) VALUES ({versionhistory_new_id})
SELECT setval('versionhistory_seq', {versionhistory_new_id})
""")

            # Insert data into `versionitem` with `versionhistory` id
            versionitem_new_id = db7.get_last_id('versionitem', 'versionitem_id') + 1

            for index, i_handle in enumerate(versions, 1):
                # Get the handle of the x.th version of the Item
                i_handle_d = metadatas.versions.get(i_handle, None)

                # If handle not found, try with different protocol (http vs https)
                if i_handle_d is None:
                    for alternative_handle in self.iter_protocol_variants(i_handle):
                        i_handle_d = metadatas.versions.get(alternative_handle, None)
                        if i_handle_d is not None:
                            _logger.debug(
                                f"Found handle data using alternative protocol: {alternative_handle}")
                            break

                # If the item is withdrawn the new version could be stored in our repo or in another. Do import that version
                # only if the item is stored in our repo.
                if i_handle_d is None:
                    current_item = self._id2item.get(str(item_id))
                    if current_item and current_item.get('withdrawn'):
                        _logger.info(
                            f"The item handle: {i_handle} cannot be migrated because it is stored in another repository."
                        )
                    else:
                        _logger.error(
                            f"Missing handle data for item {item_id}. "
                            f"Item may not exist or handle lookup failed. Skipping migration."
                        )
                    continue

                # Get item_id using the handle
                item_id = i_handle_d['item_id']
                # Get the uuid of the item using the item_id
                item_uuid = self.uuid(item_id)
                if not item_uuid:
                    _logger.critical(
                        f"Cannot find UUID for item ID {item_id} with handle {i_handle}. "
                        f"Skipping version import for this item.")
                    continue

                version_date_issued = None

                for date_field in date_fields_to_try:
                    # Parse field like "dc.date.issued" into element="date", qualifier="issued"
                    # or "dc.date" into element="date", qualifier=None
                    field_parts = date_field.split(".")
                    if len(field_parts) >= 2:
                        short_id = field_parts[0]
                        element = field_parts[1]
                        qualifier = field_parts[2] if len(field_parts) > 2 else None

                        # Single query that handles both qualified and unqualified fields
                        qualifier_condition = f"AND qualifier = '{qualifier}'" if qualifier else "AND qualifier IS NULL"

                        query = f"""
                                SELECT text_value
                                FROM metadatavalue
                                WHERE dspace_object_id = '{item_uuid}'
                                  AND metadata_field_id IN (
                                    SELECT metadata_field_id
                                    FROM metadatafieldregistry
                                    WHERE metadata_schema_id = (
                                      SELECT metadata_schema_id
                                      FROM metadataschemaregistry
                                      WHERE short_id = '{short_id}'
                                    )
                                    AND element = '{element}'
                                    {qualifier_condition}
                                  );
                            """

                        version_date_issued = db7.fetch_one(query)
                    else:
                        _logger.critical(f"Invalid date field format: '{date_field}'.")
                        continue

                    if version_date_issued is not None:
                        _logger.debug(
                            f"Found version date from field '{date_field}' for item UUID {item_uuid}: {version_date_issued}")
                        break

                # Handle case where no date metadata is found in any of the configured fields
                if version_date_issued is None:
                    _logger.critical(
                        f"No version date found for item UUID {item_uuid} in any of the configured fields: {date_fields_to_try}. Skipping version import for this item.")
                    continue

                # Strip whitespace that might be present in database fields
                version_date_issued = version_date_issued.strip()

                # Normalize and validate the date
                normalized_date = self._normalize_version_date(
                    version_date_issued, item_uuid)
                if normalized_date is None:
                    continue  # Error already logged in _normalize_version_date

                # Use parameterized query to prevent SQL injection (primary security measure),
                # regardless of input validation. normalized_date is also validated by datetime.strptime().
                sql = """INSERT INTO public.versionitem(versionitem_id, version_number, version_date,
                                                 version_summary, versionhistory_id, eperson_id, item_id) VALUES 
                                                 (%(versionitem_id)s, %(version_number)s, TO_TIMESTAMP(%(version_date)s, 'YYYY-MM-DD'), 
                                                 %(version_summary)s, %(versionhistory_id)s, %(eperson_id)s, %(item_id)s)"""

                params = {
                    'versionitem_id': versionitem_new_id,
                    'version_number': index,
                    'version_date': normalized_date,
                    'version_summary': '',
                    'versionhistory_id': versionhistory_new_id,
                    'eperson_id': admin_uuid,
                    'item_id': item_uuid
                }

                db7.exe_sql(sql, params)
                # Update sequence
                db7.exe_sql(f"SELECT setval('versionitem_seq', {versionitem_new_id})")
                versionitem_new_id += 1
                self._migrated_versions.append(str(item_id))

        _logger.info(
            f"Migrated versions [{len(self._migrated_versions or [])}]")

    def raw_after_import(self, env, db7, db5_dspace, metadatas):
        # Migration process
        self._migrate_versions(env, db7, db5_dspace, metadatas)
        self._check_sum(db7, db5_dspace, metadatas)

    def get_newer_versions(self, item_id: int, metadatas):
        return self._get_versions(item_id, metadatas, metadatas.V5_DC_RELATION_ISREPLACEDBY_ID)

    def get_older_versions(self, item_id: int, metadatas):
        return self._get_versions(item_id, metadatas, metadatas.V5_DC_RELATION_REPLACES_ID)

    def _get_versions(self, item_id: int, metadatas, metadata_field: int):
        """
            Return all previous or newer versions of the item using connection between `dc.relation.replaces` and
            `dc.relation.isreplacedby` item metadata.
            @return: list of versions or empty list
        """

        def _get_version(cur_item_id):
            item_versions = metadatas.value(items.TYPE, cur_item_id, metadata_field)
            if len(item_versions or []) == 0:
                # _logger.debug(f"Item [{cur_item_id}] does not have any version.")
                return None
            return item_versions[0]

        versions = []
        cur_item_id = item_id
        visited = set()

        cur_item_version = _get_version(cur_item_id)

        while cur_item_version is not None:
            if cur_item_version in visited:
                _logger.warning(
                    f"Detected cyclic version reference for handle: {cur_item_version}. Breaking loop.")
                break
            visited.add(cur_item_version)
            versions.append(cur_item_version)

            # Check if handle exists in versions, try both http and https protocols
            handle_data = metadatas.versions.get(cur_item_version, None)
            if handle_data is None:
                for alternative_handle in self.iter_protocol_variants(cur_item_version):
                    handle_data = metadatas.versions.get(alternative_handle, None)
                    if handle_data is not None:
                        break

            if handle_data is None:
                # Check if current item is withdrawn
                cur_item = self._id2item.get(str(cur_item_id))
                if cur_item['withdrawn']:
                    _logger.debug(f'Item [{cur_item_version}] is withdrawn')
                    self._versions["withdrawn"].append(cur_item_version)
                else:
                    _logger.error(
                        f'The item with handle: {cur_item_version} has not been imported!')
                    self._versions["not_imported"].append(cur_item_version)
                break

            next_item_id = handle_data['item_id']
            next_item_version = _get_version(next_item_id)
            if next_item_version in visited:
                versions.append(next_item_version)
                break
            cur_item_id = next_item_id
            cur_item_version = next_item_version

        return versions

    def get_all_versions(self, item_id: int, metadatas):
        """
            Return all versions of the item in ordered list from the first version to the latest including the handle of the
            current Item
            @return: list of the item versions or if the item doesn't have any version return None
        """
        # The newer versions of the item
        newer_versions = self.get_newer_versions(item_id, metadatas)
        # The previous versions of the item
        previous_versions = self.get_older_versions(item_id, metadatas)
        # Previous versions are in wrong order - reverse the list
        previous_versions = previous_versions[::-1]

        # If this item does not have any version return None
        if not newer_versions and not previous_versions:
            return None

        # Get handle of the current Item
        cur_handle = metadatas.value(
            items.TYPE, item_id, metadatas.V5_DC_IDENTIFIER_URI_ID)
        if len(cur_handle or []) == 0:
            _logger.error(f'Cannot find handle for the item with id: {item_id}')
            self._versions["not_imported_handles"].append(item_id)
            return None

        return previous_versions + [cur_handle[0]] + newer_versions

    def _check_sum(self, db7, db5_dspace, metadatas):
        """
            Check if item versions importing was successful
            Select item ids from CLARIN-DSpace5 which has some version metadata
            Select items uuids from CLARIN-DSpace7 `versionitem` table where are stored item's version
            Check if all items from CLARIN-DSpace5 has record in the CLARIN-DSpace7 history version table - check uuids
        """

        # Select item ids from CLARIN-DSpace5 which has some version metadata
        clarin_5_item_ids = db5_dspace.fetch_all(
            f"SELECT resource_id FROM metadatavalue WHERE metadata_field_id in ({metadatas.V5_DC_RELATION_REPLACES_ID},{metadatas.V5_DC_RELATION_ISREPLACEDBY_ID}) group by resource_id;"
        )

        # Select item uuids from CLARIN-DSpace7 which record in the `versionitem` table
        clarin_7_item_uuids = db7.fetch_all("select item_id from versionitem")

        if clarin_5_item_ids is None or clarin_7_item_uuids is None:
            _logger.error('Cannot check result of importing item versions.')
            return

        clarin_7_item_uuids = set([x[0] for x in clarin_7_item_uuids])

        # Some items could not be imported - uuid
        clarin_5_ids_to_uuid = set([self.uuid(x[0]) for x in clarin_5_item_ids])

        # Check v7
        problematic = []
        for uuid7 in clarin_7_item_uuids:
            if uuid7 in clarin_5_ids_to_uuid:
                continue
            if uuid7 in self._ws_id2uuid.values():
                continue
            # if item is in wf/ws it will have the relation stored in versionitem
            # in v5, we stored it after item installation

            problematic.append(uuid7)
        if problematic:
            _logger.warning(
                f'We have [{len(problematic or [])}] versions in v7 `versionitem` that are not expected!')
            for uuid in problematic:
                _logger.warning(f'UUID: {uuid}')

        # Check v5
        problematic = []
        for uuid5 in clarin_5_ids_to_uuid:
            if uuid5 in clarin_7_item_uuids:
                continue
            # if withdrawn, we do not expect it to be in v7 versionitem
            # TODO(jm): check that previous version is replaced by external item
            item_d = self.find_by_uuid(uuid5)
            if (item_d or {}).get('withdrawn', False):
                continue

            problematic.append(uuid5)
        if problematic:
            _logger.warning(
                f'We have [{len(problematic)}] versions in v5 not migrated into `versionitem`!')
            for uuid in problematic:
                _logger.warning(f'UUID: {uuid}')
