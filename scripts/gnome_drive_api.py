#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read Drive metadata and comments with an existing GNOME Online Account.

Credentials are obtained through GOA, kept in memory, and never logged or saved.
This module does not create, modify, or delete comments or files.
"""
import json
import time
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from gi.repository import Gio


class DriveReader:
    def __init__(self, identity):
        self.identity = identity
        self._token = None
        self._expires = 0

    def _access_token(self):
        if self._token and time.monotonic() < self._expires:
            return self._token
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        objects = bus.call_sync(
            "org.gnome.OnlineAccounts", "/org/gnome/OnlineAccounts",
            "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", None,
            None, Gio.DBusCallFlags.NONE, 10000, None).unpack()[0]
        accounts = [path for path, data in objects.items()
                    if data.get("org.gnome.OnlineAccounts.Account", {}).get("Identity") == self.identity
                    and "org.gnome.OnlineAccounts.OAuth2Based" in data]
        if len(accounts) != 1:
            raise ValueError("Expected one connected GNOME account for " + self.identity)
        self._token, lifetime = bus.call_sync(
            "org.gnome.OnlineAccounts", accounts[0],
            "org.gnome.OnlineAccounts.OAuth2Based", "GetAccessToken", None,
            None, Gio.DBusCallFlags.NONE, 20000, None).unpack()
        self._expires = time.monotonic() + max(0, lifetime - 60)
        return self._token

    def get(self, file_id, resource="", **query):
        url = "https://www.googleapis.com/drive/v3/files/" + quote(file_id, safe="")
        if resource:
            if resource != "comments":
                raise ValueError("Unsupported read-only resource")
            url += "/comments"
        url += "?" + urlencode(query)
        request = Request(url, headers={"Authorization": "Bearer " + self._access_token()})
        with urlopen(request, timeout=45) as response:
            return json.load(response)

    def metadata(self, file_id):
        return self.get(file_id, fields="id,name,size,sha256Checksum,md5Checksum,version,modifiedTime,webViewLink")

    def comments(self, file_id):
        rows, token = [], None
        while True:
            query = {"fields": "nextPageToken,comments(id,content,anchor,quotedFileContent,createdTime,modifiedTime,resolved,deleted,author(displayName,me),replies(id,content,deleted,createdTime,modifiedTime,author(displayName,me)))",
                     "pageSize": 100, "includeDeleted": "true"}
            if token:
                query["pageToken"] = token
            page = self.get(file_id, "comments", **query)
            rows.extend(page.get("comments", []))
            token = page.get("nextPageToken")
            if not token:
                return rows
