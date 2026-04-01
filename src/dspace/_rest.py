import logging
import time
# from json import JSONDecodeError
from ._http import response_to_json

_logger = logging.getLogger("dspace.rest")
from dspace_rest_client import client  # noqa

ANONYM_EMAIL = True

# HTTP retry configuration
HTTP_MAX_RETRIES = 3
HTTP_RETRY_DELAY = 1  # seconds
HTTP_RETRY_BACKOFF = 1.5
HTTP_RETRYABLE_CODES = [500, 502, 503, 504, 408, 429]

# Circuit breaker for persistent errors
HTTP_CIRCUIT_BREAKER_THRESHOLD = 5  # consecutive errors before circuit opens
HTTP_CIRCUIT_BREAKER_TIMEOUT = 60  # seconds before retry


def ascii(s, default="unknown"):
    try:
        return str(s).encode("ascii", "ignore").decode("ascii")
    except Exception as e:
        pass
    return default


def progress_bar(arr):
    if len(arr) < 2:
        return iter(arr)
    try:
        from tqdm import tqdm
    except Exception as e:
        return iter(arr)

    mininterval = 5 if len(arr) < 500 else 10
    return tqdm(arr, mininterval=mininterval, maxinterval=2 * mininterval)


def sanitize_log_content(content, max_length=200):
    """Sanitize content for logging to prevent log injection."""
    if not content:
        return "No content"
    # Convert to string and limit length
    sanitized = str(content)[:max_length]
    # Remove/replace potentially dangerous characters for log injection
    sanitized = sanitized.replace('\n', '\\n').replace(
        '\r', '\\r').replace('\t', '\\t')
    return sanitized


class rest:
    """
        Serves as proxy to Dspace REST API.
        Mostly uses attribute d which represents (slightly modified) dspace_client from
        original python rest api by dspace developers
    """

    def __init__(self, endpoint: str, user: str, password: str, auth: bool = True):
        _logger.info(f"Initialise connection to DSpace REST backend [{endpoint}]")

        self._acceptable_resp = []
        self._get_cnt = 0
        self._post_cnt = 0

        # Circuit breaker: tracks consecutive errors to prevent overwhelming a failing server
        self._consecutive_500_errors = 0
        self._circuit_breaker_open_time = None

        client.check_response = lambda x, y: self._resp_check(x, y)
        self._response_map = {
            201: lambda r: self._resp_ok(r),
            200: lambda r: self._resp_ok(r),
            500: lambda r: self._resp_error(r),
            400: lambda r: self._resp_error(r)
        }

        self.client = client.DSpaceClient(
            api_endpoint=endpoint, username=user, password=password)
        if auth:
            if not self.client.authenticate():
                _logger.error(f'Error auth to dspace REST API at [{endpoint}]!')
                raise ConnectionError("Cannot connect to dspace!")
            _logger.debug(f"Successfully logged in to [{endpoint}]")
        _logger.info(f"DSpace REST backend is available at [{endpoint}]")
        self.endpoint = endpoint.rstrip("/")

    # =======

    @property
    def get_cnt(self):
        return self._get_cnt

    @property
    def post_cnt(self):
        return self._post_cnt

    # =======

    def push_acceptable(self, arr: list):
        self._acceptable_resp.append(arr)

    def pop_acceptable(self):
        self._acceptable_resp.pop()

    # =======

    def clarin_put_handles(self, handle_arr: list):
        """
            Import handles which have no objects into database.
            Other handles are imported by dspace objects.
            Mapped table: handles
        """
        url = 'clarin/import/handle'
        arr = [{'handle': h['handle'], 'resourceTypeID': h['resource_type_id'],
                'dead': h['dead'], 'deadSince': h['dead_since']}
               for h in handle_arr]
        return self._put(url, arr)

    def put_handles(self, handle_arr: list):
        url = 'core/handles'
        arr = [{'handle': h['handle'], 'url': h['url'], 'dead': h['dead'],
                'deadSince': h['dead_since']} for h in handle_arr]
        return self._put(url, arr)

    # =======

    def fetch_existing_epersongroups(self):
        """
            Get all existing eperson groups from database.
        """
        url = 'eperson/groups'
        resp = self._fetch(url, self.get_many, '_embedded')
        return resp["groups"]

    def fetch_metadata_schemas(self):
        """
            Get all existing data from table metadataschemaregistry.
        """
        url = 'core/metadataschemas'
        arr = self._fetch(url, self.get_many, None)
        if arr is None or "_embedded" not in arr:
            return None
        return arr["_embedded"]['metadataschemas']

    def fetch_metadata_fields(self):
        """
        """
        url = 'core/metadatafields'
        arr = self._fetch(url, self.get_many, None)
        if arr is None or "_embedded" not in arr:
            return None
        return arr["_embedded"]['metadatafields']

    def fetch_metadata_field(self, object_id):
        """
        """
        url = 'core/metadatafields'
        return self._fetch(url, self.get_one, None, object_id=object_id)

    def fetch_schema(self, object_id):
        """
            Get all existing data from table metadataschemaregistry.
        """
        url = 'core/metadataschemas'
        return self._fetch(url, self.get_one, None, object_id=object_id)

    def put_metadata_schema(self, data):
        url = 'core/metadataschemas'
        return list(self._iput(url, [data]))[0]

    def put_metadata_field(self, data: list, params: list):
        url = 'core/metadatafields'
        return list(self._iput(url, [data], [params]))[0]

    # =======

    def put_community(self, param: dict, data: dict):
        url = 'core/communities'
        _logger.debug(f"Importing [{data}] using [{url}]")
        arr = list(self._iput(url, [data], [param]))
        if len(arr) == 0:
            return None
        return arr[0]

    def put_community_admin_group(self, com_id: int):
        url = f'core/communities/{com_id}/adminGroup'
        _logger.debug(f"Adding admin group to [{com_id}] using [{url}]")
        return list(self._iput(url, [{}], [{}]))[0]

    # =======

    def put_collection(self, param: dict, data: dict):
        url = 'core/collections'
        _logger.debug(f"Importing [{data}] using [{url}]")
        arr = list(self._iput(url, [data], [param]))
        if len(arr) == 0:
            return None
        return arr[0]

    def put_collection_editor_group(self, col_id: int):
        url = f'core/collections/{col_id}/workflowGroups/editor'
        _logger.debug(f"Adding editor group to [{col_id}] using [{url}]")
        return list(self._iput(url, [{}], [{}]))[0]

    def put_collection_submitter(self, col_id: int):
        url = f'core/collections/{col_id}/submittersGroup'
        _logger.debug(f"Adding submitter group to [{col_id}] using [{url}]")
        return list(self._iput(url, [{}], [{}]))[0]

    def put_collection_bitstream_read_group(self, col_id: int):
        url = f'core/collections/{col_id}/bitstreamReadGroup'
        _logger.debug(f"Adding bitstream read group to [{col_id}] using [{url}]")
        return list(self._iput(url, [{}], [{}]))[0]

    def put_collection_item_read_group(self, col_id: int):
        url = f'core/collections/{col_id}/itemReadGroup'
        _logger.debug(f"Adding item read group to [{col_id}] using [{url}]")
        return list(self._iput(url, [{}], [{}]))[0]

    # =======

    def put_registrationdata(self, param: dict, data: dict):
        url = 'eperson/registrations'
        _logger.debug(f"Importing [{data}] using [{url}]")
        return list(self._iput(url, [data], [param]))[0]

    # =======

    def put_eperson_group(self, param: dict, data: dict):
        url = 'eperson/groups'
        _logger.debug(f"Importing [{data}] using [{url}]")
        return list(self._iput(url, [data], [param]))[0]

    def put_group2group(self, parent, child):
        url = f'clarin/eperson/groups/{parent}/subgroups'
        child_url = f'{self.endpoint}/eperson/groups/{child}'
        _logger.debug(f"Importing [{parent}][{child}] using [{url}]")
        return list(self._iput(url, [child_url]))[0]

    def put_eperson(self, param: dict, data: dict):
        url = 'clarin/import/eperson'
        _logger.debug(f"Importing [{data}] using [{url}]")
        return list(self._iput(url, [data], [param]))[0]

    def put_userregistration(self, data: dict):
        url = 'clarin/import/userregistration'
        _logger.debug(f"Importing [{data}] using [{url}]")
        return list(self._iput(url, [data]))[0]

    def put_egroup(self, gid: int, eid: int):
        url = f'clarin/eperson/groups/{gid}/epersons'
        _logger.debug(f"Importing group[{gid}] e:[{eid}] using [{url}]")
        eperson_url = f'{self.endpoint}/eperson/groups/{eid}'
        return list(self._iput(url, [eperson_url]))[0]

    # =======

    def fetch_bitstreamregistry(self):
        url = 'core/bitstreamformats'
        arr = self._fetch(url, self.get_many, None)
        if arr is None or "_embedded" not in arr:
            return None
        return arr["_embedded"]["bitstreamformats"]

    def put_bitstreamregistry(self, data: dict):
        url = 'core/bitstreamformats'
        _logger.debug(f"Importing [{data}] using [{url}]")
        return list(self._iput(url, [data]))[0]

    # =======

    def fetch_licenses(self):
        url = 'core/clarinlicenses'
        _logger.debug(f"Fetch [] using [{url}]")
        page = 0
        licenses = []
        while True:
            r = self._fetch(url, self.get, "_embedded",
                            params={"page": page, "size": 100})
            if r is None:
                break
            key = "clarinlicenses"
            licenses_data = r.get(key, [])
            if licenses_data:
                licenses.extend(licenses_data)
            else:
                _logger.warning(f"Key [{key}] does not exist in response: {r}")
            page += 1
        return licenses

    def put_license_label(self, data: dict):
        url = 'core/clarinlicenselabels'
        _logger.debug(f"Importing [{data}] using [{url}]")
        return list(self._iput(url, [data]))[0]

    def put_license(self, param: dict, data: dict):
        url = 'clarin/import/license'
        _logger.debug(f"Importing [{data}] using [{url}]")
        return list(self._iput(url, [data], [param]))[0]

    # =======

    def put_bundle(self, item_uuid: int, data: dict):
        url = f'core/items/{item_uuid}/bundles'
        _logger.debug(f"Importing [{data}] using [{url}]")
        return list(self._iput(url, [data],))[0]

    # =======

    def fetch_raw_item(self, uuid: str):
        url = f'core/items/{uuid}'
        _logger.debug(f"Fetching [{uuid}] using [{url}]")
        r = self.get(url)
        if not r.ok:
            raise Exception(r)
        return response_to_json(r)

    # =======

    def put_usermetadata(self, params: dict, data: dict):
        url = 'clarin/import/usermetadata'
        _logger.debug(f"Importing [{data}] using [{url}]")
        return list(self._iput(url, [data], [params]))[0]

    # =======

    def put_resourcepolicy(self, params: dict, data: dict):
        url = 'authz/resourcepolicies'
        _logger.debug(f"Importing [{data}] using [{url}]")
        return list(self._iput(url, [data], [params]))[0]

    # =======

    def add_checksums(self):
        """
            Fill the tables most_recent_checksum and checksum_result based
            on imported bitstreams that haven't already their checksum
            calculated.
        """
        url = 'clarin/import/core/bitstream/checksum'
        _logger.debug(f"Checksums using [{url}]")
        r = self.post(url)
        if not r.ok:
            raise Exception(r)

    def put_bitstream(self, param: dict, data: dict):
        url = 'clarin/import/core/bitstream'
        _logger.debug(f"Importing [][{param}] using [{url}]")
        return list(self._iput(url, [data], [param]))[0]

    def put_com_logo(self, param: dict):
        url = 'clarin/import/logo/community'
        _logger.debug(f"Importing [][{param}] using [{url}]")
        r = self.post(url, params=param, data=None)
        if not r.ok:
            raise Exception(r)
        return response_to_json(r)

    def put_col_logo(self, param: dict):
        url = 'clarin/import/logo/collection'
        _logger.debug(f"Importing [][{param}] using [{url}]")
        r = self.post(url, params=param, data=None)
        if not r.ok:
            raise Exception(r)
        return response_to_json(r)

    # =======

    def fetch_item(self, uuid: str):
        url = f'clarin/import/{uuid}/item'
        _logger.debug(f"Importing [] using [{url}]")
        return self._fetch(url, self.get, None)

    def fetch_items(self, page_size: int = 100, limit=None):
        url = 'core/items'
        _logger.debug(f"Fetch [] using [{url}]")
        page = 0
        items = []
        while True:
            r = self._fetch(url, self.get, "_embedded",
                            params={"page": page, "size": page_size})
            if r is None:
                break
            key = "items"
            items_data = r.get(key, [])
            if items_data:
                items.extend(items_data)
            else:
                _logger.warning(f"Key [{key}] does not exist in response: {r}")
            page += 1

            if limit is not None and len(items) > limit:
                return items[:limit]
        return items

    def iter_items(self, page_size: int = 100, limit: int = -1, uuid: str = None, reauth: int = 0):
        from tqdm import tqdm

        url = 'core/items'
        _logger.debug(f"Fetch iter [] using [{url}]")
        page = 0
        len_items = 0
        item_key = "items"
        fetch_key = "_embedded"

        if uuid is not None:
            fetch_key = None
            url = f"{url}/{uuid}"
            pbar = None
        else:
            pbar = tqdm(desc="Fetching items", unit=" items")

        try:
            while True:
                r = self._fetch(url, self.get, fetch_key,
                                params={"page": page, "size": page_size})
                if r is None:
                    break
                # only one
                if uuid is not None:
                    yield r
                    return

                items_data = r.get(item_key, [])
                if items_data:
                    len_items += len(items_data)
                    yield items_data
                else:
                    _logger.warning(f"Key [{item_key}] does not exist in response: {r}")
                page += 1
                if pbar is not None:
                    pbar.update(len(items_data))

                # make sure we have fresh token
                if reauth > 0 and page % reauth == 0:
                    self.client.authenticate()

                if len_items >= limit > 0:
                    return
        finally:
            if pbar is not None:
                pbar.close()

    def put_ws_item(self, param: dict, data: dict):
        url = 'clarin/import/workspaceitem'
        _logger.debug(f"Importing [{data}] using [{url}]")
        return list(self._iput(url, [data], [param]))[0]

    def put_wf_item(self, param: dict):
        url = 'clarin/import/workflowitem'
        _logger.debug(f"Importing [][{param}] using [{url}]")
        r = self.post(url, params=param, data=None)
        if not r.ok:
            raise Exception(r)
        return r

    def put_item(self, param: dict, data: dict):
        url = 'clarin/import/item'
        item_info = ""
        if 'uuid' in (data or {}):
            item_info = f" UUID: {data.get('uuid', 'unknown')}"
        elif 'id' in (param or {}):
            item_info = f" ID: {param.get('id', 'unknown')}"

        _logger.debug(f"Importing item{item_info}")
        _logger.debug(f"Importing [][{param}] using [{url}]")
        result = list(self._iput(url, [data], [param]))[0]

        if result is None:
            _logger.error(f"Failed to import item{item_info}")

        return result

    def put_item_to_col(self, item_uuid: str, data: list):
        url = f'clarin/import/item/{item_uuid}/mappedCollections'
        _logger.debug(f"Importing [{data}] using [{url}]")
        col_url = 'core/collections/'
        # Prepare request body which should looks like this:
        # `"https://localhost:8080/spring-rest/api/core/collections/{collection_uuid_1}" + \n
        # "https://localhost:8080/spring-rest/api/core/collections/{collection_uuid_2}"
        data = [f"{self.endpoint}/{col_url}/{x}" for x in data]
        return list(self._iput(url, [data]))[0]

    # =======

    def fetch_search_items(self, item_type: str = "ITEM", page: int = 0, size: int = 100):
        """
            TODO(jm): make generic
        """
        url = f'discover/search/objects?sort=score,DESC&size={size}&page={page}&configuration=default&dsoType={item_type}&embed=thumbnail&embed=item%2Fthumbnail'
        r = self.get(url)
        if not r.ok:
            raise Exception(r)
        return response_to_json(r)

    # =======

    def _fetch(self, url: str, method, key: str, re_auth=True, **kwargs):
        r = None
        try:
            r = method(url, **kwargs)
            js = response_to_json(r)

            if r.status_code == 200:
                # 200 OK - success!
                if key is None:
                    return js
                return js[key]

            if re_auth and r.status_code == 401:
                # 401 Unauthorized
                logging.debug('Re-authenticating in _fetch')
                if self.client.authenticate():
                    return self._fetch(url, method, key, re_auth=False, **kwargs)

            _logger.error(f'GET [{url}] failed. Status: {r.status_code}]')
            return None
        except Exception as e:
            detail = ""
            if r is not None:
                try:
                    detail = r.content.decode('utf-8')
                except Exception:
                    pass
            _logger.error(f'GET [{url}] failed. Exception: [{str(e)}] [{detail}]')
        return None

    def _put(self, url: str, arr: list, params: list = None):
        return len(list(self._iput(url, arr, params)))

    def _iput(self, url: str, arr: list, params=None):
        _logger.debug(f"Importing {len(arr)} using [{url}]")
        if params is not None:
            assert len(params) == len(arr)

        for i, data in enumerate(progress_bar(arr)):
            param = params[i] if params is not None else None
            result = self._post_with_retry(url, data, param, i, len(arr))
            yield result
        _logger.debug(f"Imported [{url}] successfully")

    def _post_with_retry(self, url: str, data, param, item_index: int, total_items: int):
        """POST with retry logic for handling temporary server errors"""

        # Check if circuit breaker is blocking requests due to consecutive errors
        if self._is_circuit_breaker_open():
            _logger.warning(
                f"Circuit breaker open - skipping request to [{url}] (too many consecutive 500 errors)")
            return None

        ascii_data = ascii(data)
        if ANONYM_EMAIL:
            # Truncate data containing email addresses for privacy in logs
            if "@" in ascii_data or "email" in ascii_data:
                ascii_data = ascii_data[:5]
        if len(ascii_data) > 80:
            ascii_data = f"{ascii_data[:70]}..."

        last_exception = None
        last_response = None

        for attempt in range(HTTP_MAX_RETRIES):
            try:
                r = self.post(url, params=param, data=data)

                if r.ok:
                    # Success - reset circuit breaker and return parsed response
                    self._handle_circuit_breaker(r.status_code)
                    try:
                        js = None
                        if len(r.content or '') > 0:
                            js = response_to_json(r)
                        if attempt > 0:
                            _logger.debug(
                                f"POST [{url}] succeeded on attempt {attempt + 1}/{HTTP_MAX_RETRIES}")
                        return js
                    except Exception:
                        return r

                # Handle HTTP errors
                elif r.status_code in HTTP_RETRYABLE_CODES:
                    last_response = r
                    self._handle_circuit_breaker(r.status_code)
                    retry_delay = HTTP_RETRY_DELAY * (HTTP_RETRY_BACKOFF ** attempt)

                    if attempt == HTTP_MAX_RETRIES - 1:
                        # Last attempt - no retry will happen
                        _logger.warning(
                            f"POST [{url}] HTTP {r.status_code} (attempt {attempt + 1}/{HTTP_MAX_RETRIES}) - final attempt")
                    elif attempt == 0:
                        # First attempt - log with retry info
                        _logger.warning(
                            f"POST [{url}] HTTP {r.status_code} (attempt {attempt + 1}/{HTTP_MAX_RETRIES}) - retrying in {retry_delay}s")
                    else:
                        _logger.debug(
                            f"POST [{url}] HTTP {r.status_code} (attempt {attempt + 1}/{HTTP_MAX_RETRIES})")

                    if attempt < HTTP_MAX_RETRIES - 1:
                        time.sleep(retry_delay)

                        # Re-authenticate on certain errors
                        if r.status_code in [401, 403]:
                            _logger.debug("Re-authenticating due to auth error")
                            if not self.client.authenticate():
                                _logger.warning("Re-authentication failed")
                                break
                        continue
                else:
                    # Non-retryable error
                    last_response = r
                    _logger.error(
                        f"[FAILED] POST [{url}] failed with non-retryable HTTP {r.status_code} for [{ascii_data}]: {r.text}")
                    return None

            except Exception as e:
                last_exception = e
                retry_delay = HTTP_RETRY_DELAY * (HTTP_RETRY_BACKOFF ** attempt)

                if attempt == 0 or attempt == HTTP_MAX_RETRIES - 1:
                    _logger.warning(
                        f"POST [{url}] exception (attempt {attempt + 1}/{HTTP_MAX_RETRIES}): {str(e)}")
                else:
                    _logger.debug(
                        f"POST [{url}] exception (attempt {attempt + 1}/{HTTP_MAX_RETRIES}): {str(e)}")

                if attempt < HTTP_MAX_RETRIES - 1:
                    time.sleep(retry_delay)
                    continue

        # All retries exhausted
        msg_r = ""
        try:
            msg_r = str(last_response) if last_response else ""
        except Exception:
            pass

        # Provide detailed error information with sanitized content

        if last_response:
            status_code = getattr(last_response, 'status_code', 'Unknown')
            if hasattr(last_response, 'text'):
                error_text = sanitize_log_content(last_response.text)
                error_detail = f"HTTP {status_code}: {error_text}"
            else:
                error_detail = f"HTTP {status_code}: No response text"
        else:
            error_detail = sanitize_log_content(
                str(last_exception) if last_exception else "Unknown error")

        msg = f"POST [{url}] for [{ascii_data}] failed after {HTTP_MAX_RETRIES} attempts. Final error: {error_detail}"
        _logger.error(msg)
        return None

    # =======

    def get_many(self, command: str, size: int = 1000):
        params = {'size': size}
        return self.get(command, params)

    def get_one(self, command: str, object_id: int):
        url = command + '/' + str(object_id)
        return self.get(url, {})

    def get(self, command: str, params=None, data=None):
        url = self.endpoint + '/' + command
        self._get_cnt += 1
        return self.client.api_get(url, params, data)

    def post(self, command: str, params=None, data=None):
        url = self.endpoint + '/' + command
        self._post_cnt += 1
        return self.client.api_post(url, params or {}, data or {})

    def _is_circuit_breaker_open(self):
        """Check if circuit breaker is open due to too many consecutive failures"""
        if self._circuit_breaker_open_time is None:
            return False

        # Check if timeout period has passed
        if time.time() - self._circuit_breaker_open_time > HTTP_CIRCUIT_BREAKER_TIMEOUT:
            _logger.info("Circuit breaker timeout expired - attempting to close circuit")
            self._circuit_breaker_open_time = None
            self._consecutive_500_errors = 0
            return False

        return True

    def _handle_circuit_breaker(self, status_code):
        """Update circuit breaker state based on response"""
        if status_code == 500:
            self._consecutive_500_errors += 1
            if self._consecutive_500_errors >= HTTP_CIRCUIT_BREAKER_THRESHOLD:
                if self._circuit_breaker_open_time is None:
                    self._circuit_breaker_open_time = time.time()
                    _logger.error(
                        f"Circuit breaker OPENED - {self._consecutive_500_errors} consecutive 500 errors. Will retry in {HTTP_CIRCUIT_BREAKER_TIMEOUT}s")
        else:
            # Reset on any non-500 response
            if self._consecutive_500_errors > 0:
                _logger.info(
                    f"Circuit breaker reset - got non-500 response after {self._consecutive_500_errors} errors")
            self._consecutive_500_errors = 0
            self._circuit_breaker_open_time = None

    # =======

    def _resp_check(self, r, msg):
        if r is None:
            _logger.error(f"Failed to receive response [{msg}] ")
            raise Exception("No response from server where one was expected")
        _logger.debug(f"{str(msg)}: {r.status_code}")

        # explicit accepted
        for ar in self._acceptable_resp:
            if r.status_code in ar:
                return

        if r.status_code not in self._response_map:
            _logger.warning(f"Unexpected response: {r.status_code}; [{r.url}]; {r.text}")
        else:
            self._response_map[r.status_code](r)

    def _resp_error(self, r):
        raise ConnectionError(r.text)

    def _resp_ok(self, r):
        return True
