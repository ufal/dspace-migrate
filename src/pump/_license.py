import os
import logging
from ._utils import read_json, time_method, serialize, deserialize, progress_bar, log_before_import, log_after_import

_logger = logging.getLogger("pump.license")


class licenses:

    validate_table = [
        ["license_definition", {
            "compare": ["name", "confirmation", "required_info"],
            "db": "clarin-utilities",
        }],
        ["license_label", {
            "compare": ["label", "title"],
            "db": "clarin-utilities",
        }],
        ["license_label", {
            "compare": ["label", "title"],
            "db": "clarin-utilities",
        }],
        ["license_label_extended_mapping", {
            "nonnull": ["license_id"],
            "db": "clarin-utilities",
        }],
        ["license_resource_user_allowance", {
            "nonnull": ["mapping_id"],
            "db": "clarin-utilities",
        }],
        ["license_resource_mapping", {
            "nonnull": ["license_id"],
            "db": "clarin-utilities",
        }],
    ]

    def __init__(self,
                 license_labels_file_str: str,
                 license_defs_file_str: str,
                 license_map_file_str: str):
        self._labels = read_json(license_labels_file_str)
        self._licenses = read_json(license_defs_file_str)
        self._map = read_json(license_map_file_str)

        self._license2label = {}
        self._created_labels = {}

        self._imported = {
            "label": 0,
            "licenses": 0,
        }

        if len(self._labels) == 0:
            _logger.info(f"Empty input: [{license_labels_file_str}].")
        if len(self._map) == 0:
            _logger.info(f"Empty input: [{license_map_file_str}].")
        if len(self._licenses) == 0:
            _logger.info(f"Empty input: [{license_defs_file_str}].")

    def __len__(self):
        return len(self._labels)

    @property
    def imported_labels(self):
        return self._imported['label']

    @property
    def imported_licenses(self):
        return self._imported['licenses']

    def import_to(self, env, dspace, epersons):
        self._import_license_labels(env, dspace)
        self._import_license_defs(env, dspace, epersons)

    @time_method
    def _import_license_labels(self, env, dspace):
        """
            Mapped tables: license_label
        """
        expected = len(self._labels)
        log_key = "license labels"
        log_before_import(log_key, expected)

        no_icon_for_labels = env.get("ignore", {}).get("missing-icons", [])

        for label in progress_bar(self._labels):
            l_id = label['label_id']
            l_name = label['label']
            data = {
                'label': l_name,
                'title': label['title'],
                'extended': label['is_extended'],
                'icon': None
            }

            # find image with label name
            icon_path = os.path.join(env["input"]["icondir"],
                                     l_name.lower() + ".png")
            try:
                if l_name not in no_icon_for_labels:
                    with open(icon_path, "rb") as fin:
                        data['icon'] = list(fin.read())
            except Exception as e:
                _logger.error(
                    f"Problem reading label icon [{os.path.abspath(icon_path)}] [{l_name}]: str(e)")

            try:
                resp = dspace.put_license_label(data)
                del resp['license']
                del resp['_links']
                self._created_labels[str(l_id)] = resp
                self._imported["label"] += 1
            except Exception as e:
                _logger.error(f'put_license_label: [{l_id}] failed [{str(e)}]')

        for m in self._map:
            lic_id = m['license_id']
            lab_id = m['label_id']
            self._license2label.setdefault(str(lic_id), []).append(
                self._created_labels[str(lab_id)])

        log_after_import(log_key, expected, self.imported_labels)

    @time_method
    def _import_license_defs(self, env, dspace, epersons):
        expected = len(self._licenses)
        log_key = "license defs"
        log_before_import(log_key, expected)

        # import license_definition
        for lic in progress_bar(self._licenses):
            lic_id = lic['license_id']
            lab_id = lic['label_id']
            updated_def = update_license_def(env, lic['definition'])
            data = {
                'name': lic['name'],
                'definition': updated_def,
                'confirmation': lic['confirmation'],
                'requiredInfo': lic['required_info'],
                'clarinLicenseLabel': self._created_labels[str(lab_id)]
            }
            lic_id = str(lic_id)
            if lic_id in self._license2label:
                data['extendedClarinLicenseLabels'] = self._license2label[lic_id]

            params = {'eperson': epersons.uuid(lic['eperson_id'])}
            try:
                resp = dspace.put_license(params, data)
                self._imported["licenses"] += 1
            except Exception as e:
                _logger.error(f'XXX: [{lic_id}] failed [{str(e)}]')

        log_after_import(log_key, expected, self.imported_licenses)

    # =============

    def serialize(self, file_str: str):
        data = {
            "labels": self._labels,
            "licenses": self._licenses,
            "map": self._map,
            "license2label": self._license2label,
            "created_labels": self._created_labels,
            "imported": self._imported,
        }
        serialize(file_str, data)

    def deserialize(self, file_str: str):
        data = deserialize(file_str)
        self._labels = data["labels"]
        self._licenses = data["licenses"]
        self._map = data["map"]
        self._license2label = data["license2label"]
        self._created_labels = data["created_labels"]
        self._imported = data["imported"]


def update_license_def(env, lic_def_url: str):
    """
        Replace license definition url from current site url to a new site url
        e.g., from `https://lindat.mff.cuni.cz/repository/xmlui/page/licence-hamledt`
        to `https://lindat.mff.cuni.cz/repository/static/licence-hamledt.html`
    """
    env_lic = env.get("licenses", {})
    if "to_replace_def_url" not in env_lic:
        _logger.info(
            "License def URL is not replaced, absolute"
            "path to the new repo must match the old one!")
        return lic_def_url

    # Replace old site url to a new site url
    if env_lic["to_replace_def_url"] in lic_def_url:
        if 'XXX' in env_lic['replace_with_def_url']:
            _logger.warning(f"New license contains XXX and is probably misconfigured: "
                            f"{lic_def_url}")
        lic_def_url = lic_def_url.replace(
            env_lic["to_replace_def_url"],
            env_lic["replace_with_def_url"]
        )
        # File name has a missing `.html` suffix -> add that suffix
        # to the end of the definition url
        lic_def_url += '.html'

    return lic_def_url
