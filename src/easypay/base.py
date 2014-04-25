#!/usr/bin/python
# -*- coding: utf-8 -*-

# Hive Easypay API
# Copyright (C) 2008-2014 Hive Solutions Lda.
#
# This file is part of Hive Easypay API.
#
# Hive Easypay API is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Hive Easypay API is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Hive Easypay API. If not, see <http://www.gnu.org/licenses/>.

__author__ = "João Magalhães <joamag@hive.pt>"
""" The author(s) of the module """

__version__ = "1.0.0"
""" The version of the module """

__revision__ = "$LastChangedRevision$"
""" The revision number of the module """

__date__ = "$LastChangedDate$"
""" The last change date of the module """

__copyright__ = "Copyright (c) 2008-2014 Hive Solutions Lda."
""" The copyright for the module """

__license__ = "GNU General Public License (GPL), Version 3"
""" The license for the module """

import time
import uuid
import shelve
import threading

import xml.dom.minidom
import xml.etree.ElementTree

import appier

from easypay import mb
from easypay import errors

BASE_URL = "https://www.easypay.pt/_s/"
""" The default base url to be used for a production
based environment, should be used carefully """

BASE_URL_TEST = "http://test.easypay.pt/_s/"
""" The base url for the sandbox endpoint, this is used
for testing purposes only and the password is sent using
a non encrypted model (no protection provided) """

class Scheduler(threading.Thread):
    """
    Scheduler thread that is used to poll the remote easypay
    server for the detailed information on the document and
    then notify the final api client about the new information.
    """

    def __init__(self, api):
        threading.Thread.__init__(self)
        self.api = api
        self.daemon = True

    def stop(self):
        self.running = False

    def run(self):
        self.running  = True
        while self.running:
            self.tick()
            time.sleep(5)

    def tick(self):
        """
        Runs one tick operation, meaning that all the pending
        documents will be retrieved and a try will be made to
        retrieve the detailed information on them.
        """

        docs = self.api.list_docs()
        for doc in docs:
            _doc = doc["doc"]
            details = self.api.details_mb(_doc)
            self.api.mark_mb(details)

class Api(
    appier.Observable,
    mb.MBApi
):
    """
    Top level entry point for the easypay api services,
    should provide the abstract implementations for the
    services offered by easypay.

    Concrete implementations of this api should provide
    other storage options that should include persistence.
    """

    def __init__(self, *args, **kwargs):
        appier.Observable.__init__(self, *args, **kwargs)
        self.production = kwargs.get("production", False)
        self.username = kwargs.get("username", None)
        self.password = kwargs.get("password", None)
        self.cin = kwargs.get("cin", None)
        self.entity = kwargs.get("entity", None)
        self.base_url = BASE_URL if self.production else BASE_URL_TEST
        self.counter = 0
        self.references = list()
        self.docs = dict()
        self.lock = threading.RLock()
        self.scheduler = Scheduler(self)

    def start_scheduler(self):
        self.scheduler.start()

    def stop_scheduler(self):
        self.scheduler.stop()

    def request(self, method, *args, **kwargs):
        result = method(*args, **kwargs)
        result = self.loads(result)
        status = result.get("ep_status", "err1")
        message = result.get("ep_message", "no message defined")
        if not status == "ok0": raise errors.ApiError(message)
        return result

    def build_kwargs(self, kwargs, auth = True, token = False):
        if self.cin: kwargs["ep_cin"] = self.cin
        if self.username: kwargs["ep_user"] = self.username

    def get(self, url, auth = True, token = False, **kwargs):
        self.build_kwargs(kwargs, auth = auth, token = token)
        return self.request(
            appier.get,
            url,
            params = kwargs
        )

    def post(self, url, auth = True, token = False, data = None, data_j = None, **kwargs):
        self.build_kwargs(kwargs, auth = auth, token = token)
        return self.request(
            appier.post,
            url,
            params = kwargs,
            data = data,
            data_j = data_j
        )

    def gen_reference(self, data):
        cin = data["ep_cin"]
        username = data["ep_user"]
        entity = data["ep_entity"]
        reference = data["ep_reference"]
        value = data["ep_value"]
        identifier = data["t_key"]
        reference = dict(
            cin = cin,
            username = username,
            entity = entity,
            reference = reference,
            value = value,
            identifier = identifier,
            status = "pending"
        )
        self.new_reference(reference)

    def gen_doc(self, identifier, key):
        doc = dict(
            cin = self.cin,
            username = self.username,
            identifier = identifier,
            key = key
        )
        self.new_doc(doc)

    def new_reference(self, reference):
        identifier = reference["identifier"]
        self.references[identifier] = reference

    def del_reference(self, identifier):
        del self.references[identifier]

    def list_references(self):
        references = self.references.values()
        return appier.eager(references)

    def get_reference(self, identifier):
        return self.references[identifier]

    def new_doc(self, doc):
        identifier = doc["identifier"]
        self.docs[identifier] = doc

    def del_doc(self, identifier):
        del self.docs[identifier]

    def list_docs(self):
        docs = self.docs.values()
        return appier.eager(docs)

    def get_doc(self, identifier):
        return self.docs[identifier]

    def next(self):
        self.lock.acquire()
        try: self.counter += 1; next = self.counter
        finally: self.lock.release()
        return next

    def generate(self):
        identifier = str(uuid.uuid4())
        return identifier

    def validate(self, cin = None, username = None):
        if cin and not cin == self.cin:
            raise errors.SecurityError("invalid cin")
        if username and not username == self.username:
            raise errors.SecurityError("invalid username")

    def loads(self, data):
        result = dict()
        document = xml.dom.minidom.parseString(data)
        base = document.childNodes[0]
        for node in base.childNodes:
            name = node.nodeName
            value = self._text(node)
            if value == None: continue
            result[name] = value
        return result

    def dumps(self, map, root = "getautoMB_detail", encoding = "utf-8"):
        root = xml.etree.ElementTree.Element(root)
        for name, value in map.items():
            value = value if type(value) in appier.STRINGS else str(value)
            child = xml.etree.ElementTree.SubElement(root, name)
            child.text = value
        result = xml.etree.ElementTree.tostring(
            root,
            encoding = encoding,
            method = "xml"
        )
        header = appier.bytes("<?xml version=\"1.0\" encoding=\"%s\"?>" % encoding)
        result = header + result
        return result

    def _text(self, node):
        if not node.childNodes: return None
        return node.childNodes[0].nodeValue

class ShelveApi(Api):
    """
    Shelve api based infra-structure, that provides a storage
    engine based for secondary storage persistence. This class
    should be used only as a fallback storage as the performance
    is considered poor, due to large overhead in persistence.
    """

    def __init__(self, path = "easypay.shelve", *args, **kwargs):
        Api.__init__(self, *args, **kwargs)
        self.shelve = shelve.open(
            path,
            protocol = 2,
            writeback = True
        )

    def new_reference(self, reference):
        identifier = reference["identifier"]
        self.lock.acquire()
        try:
            references = self.shelve.get("references", {})
            references[identifier] = reference
            self.shelve["references"] = references
            self.shelve.sync()
        finally:
            self.lock.release()

    def del_reference(self, identifier):
        self.lock.acquire()
        try:
            references = self.shelve.get("references", {})
            del references[identifier]
            self.shelve["references"] = references
            self.shelve.sync()
        finally:
            self.lock.release()

    def list_references(self):
        references = self.shelve.get("references", {})
        references = references.values()
        return appier.eager(references)

    def get_reference(self, identifier):
        references = self.shelve.get("references", {})
        return references[identifier]        

    def new_doc(self, doc):
        identifier = doc["identifier"]
        self.lock.acquire()
        try:
            docs = self.shelve.get("docs", {})
            docs[identifier] = docs
            self.shelve["docs"] = docs
            self.shelve.sync()
        finally:
            self.lock.release()

    def del_doc(self, identifier):
        self.lock.acquire()
        try:
            docs = self.shelve.get("docs", {})
            del docs[identifier]
            self.shelve["docs"] = docs
            self.shelve.sync()
        finally:
            self.lock.release()

    def list_docs(self):
        docs = self.shelve.get("docs", {})
        docs = docs.values()
        return appier.eager(docs)

    def get_doc(self, identifier):
        docs = self.shelve.get("docs", {})
        return docs[identifier]

    def next(self):
        self.lock.acquire()
        try:
            counter = self.shelve.get("counter", 0)
            counter += 1
            next = counter
            self.shelve["counter"] = counter
            self.shelve.sync()
        finally:
            self.lock.release()
        return next

class MongoApi(Api):
    pass
