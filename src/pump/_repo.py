import logging
import os

from ._utils import time_method

from ._handle import handles
from ._metadata import metadatas

from ._group import groups
from ._community import communities
from ._collection import collections
from ._registrationdata import registrationdatas
from ._eperson import epersons
from ._eperson import groups as eperson_groups
from ._userregistration import userregistrations
from ._bitstreamformatregistry import bitstreamformatregistry
from ._license import licenses
from ._item import items
from ._tasklistitem import tasklistitems
from ._bundle import bundles
from ._bitstream import bitstreams
from ._resourcepolicy import resourcepolicies
from ._usermetadata import usermetadatas
from ._db import db, differ
from ._sequences import sequences

_logger = logging.getLogger("pump.repo")


class repo:
    @time_method
    def __init__(self, env: dict, dspace):
        def _f(name): return os.path.join(env["input"]["datadir"], name)

        # load groups
        self.groups = groups(
            _f("epersongroup.json"),
            _f("group2group.json"),
        )
        self.groups.from_rest(dspace)

        # load handles
        self.handles = handles(_f("handle.json"))

        # load metadata
        self.metadatas = metadatas(
            env,
            dspace,
            _f("metadatavalue.json"),
            _f("metadatafieldregistry.json"),
            _f("metadataschemaregistry.json"),
        )

        # load community
        self.communities = communities(
            _f("community.json"),
            _f("community2community.json"),
        )

        self.collections = collections(
            _f("collection.json"),
            _f("community2collection.json"),
            _f("metadatavalue.json"),
        )

        self.registrationdatas = registrationdatas(
            _f("registrationdata.json")
        )

        self.epersons = epersons(
            _f("eperson.json")
        )

        self.egroups = eperson_groups(
            _f("epersongroup2eperson.json")
        )

        self.userregistrations = userregistrations(
            _f("user_registration.json")
        )

        self.bitstreamformatregistry = bitstreamformatregistry(
            _f("bitstreamformatregistry.json")
        )

        self.licenses = licenses(
            _f("license_label.json"),
            _f("license_definition.json"),
            _f("license_label_extended_mapping.json"),
        )

        self.items = items(
            _f("item.json"),
            _f("workspaceitem.json"),
            _f("workflowitem.json"),
            _f("collection2item.json"),
        )

        self.tasklistitems = tasklistitems(
            _f("tasklistitem.json")
        )

        self.bundles = bundles(
            _f("bundle.json"),
            _f("item2bundle.json"),
        )

        self.bitstreams = bitstreams(
            _f("bitstream.json"),
            _f("bundle2bitstream.json"),
        )

        self.usermetadatas = usermetadatas(
            _f("user_metadata.json"),
            _f("license_resource_user_allowance.json"),
            _f("license_resource_mapping.json")
        )

        self.resourcepolicies = resourcepolicies(
            _f("resourcepolicy.json")
        )

        self.raw_db_7 = db(env["db_dspace_7"])
        self.raw_db_dspace_5 = db(env["db_dspace_5"])
        self.raw_db_utilities_5 = db(env["db_utilities_5"])

        self.sequences = sequences()

    def diff(self, to_validate=None):
        if to_validate is None:
            to_validate = [
                getattr(getattr(self, x), "validate_table")
                for x in dir(self) if hasattr(getattr(self, x), "validate_table")
            ]
        else:
            if not hasattr(to_validate, "validate_table"):
                _logger.warning(f"Missing validate_table in {to_validate}")
                return
            to_validate = [to_validate.validate_table]

        diff = differ(self.raw_db_dspace_5, self.raw_db_utilities_5, self.raw_db_7)
        diff.validate(to_validate)

    # =====
    def uuid(self, res_type_id: int, res_id: int):
        # find object id based on its type
        try:
            if res_type_id == self.communities.TYPE:
                return self.communities.uuid(res_id)
            if res_type_id == self.collections.TYPE:
                return self.collections.uuid(res_id)
            if res_type_id == self.items.TYPE:
                return self.items.uuid(res_id)
            if res_type_id == self.bitstreams.TYPE:
                return self.bitstreams.uuid(res_id)
            if res_type_id == self.bundles.TYPE:
                return self.bundles.uuid(res_id)
            if res_type_id == self.epersons.TYPE:
                return self.epersons.uuid(res_id)
            if res_type_id == self.groups.TYPE:
                arr = self.groups.uuid(res_id)
                if len(arr or []) > 0:
                    return arr[0]
        except Exception as e:
            return None
        return None
