import json
import os
import socket
import tempfile
import threading
import unittest
from unittest import mock

import sqctl
import sq_mcp


class FakeBridge:
    def __init__(self, path):
        self.path = path
        self.requests = []
        self.objects = {
            1: {
                "id": 1,
                "class": "ConnectionInterface",
                "name": "ConnectionInterface",
                "children": [],
                "properties": {"channelManager": {"id": 2, "class": "cChannelManager"}},
            },
            2: {
                "id": 2,
                "class": "cChannelManager",
                "name": "channelManager",
                "children": [10, 11, 12],
                "properties": {
                    "selected": {"id": 3, "class": "cSelectionManager"},
                    "mixButtonChannels": {"id": 4, "class": "QQmlObjectListModelBase"},
                    "mainLRChannel": {"id": 13, "class": "cMainLRChannel"},
                },
            },
            3: {"id": 3, "class": "cSelectionManager", "name": "", "children": [], "properties": {"mix": {"id": 13}}},
            4: {"id": 4, "class": "QQmlObjectListModelBase", "name": "", "children": [], "properties": {"count": 1}},
            13: {"id": 13, "class": "cMainLRChannel", "name": "", "children": [], "properties": {"designator": "MainLR"}},
            14: {"id": 14, "class": "cMixChannel", "name": "", "children": [], "properties": {"designator": "Aux1"}},
            10: self._channel(10, "cInputChannel", "Ip1", "Kick", 110, 210),
            11: self._channel(11, "cInputChannel", "Ip47/48", "Stereo", 111, 211),
            12: self._channel(12, "cChannel", "Ip1", "Decoy", 112, 212),
            110: {"id": 110, "class": "cMuteDriver", "name": "mute1", "children": [], "properties": {"value": False}},
            111: {"id": 111, "class": "cMuteDriver", "name": "mute47", "children": [], "properties": {"value": False}},
            112: {"id": 112, "class": "cMuteDriver", "name": "decoyMute", "children": [], "properties": {"value": False}},
            210: {"id": 210, "class": "cFaderDriver", "name": "fader1", "children": [], "properties": {"floatValue": 0.5}},
            211: {"id": 211, "class": "cFaderDriver", "name": "fader47", "children": [], "properties": {"floatValue": 0.5}},
            212: {"id": 212, "class": "cFaderDriver", "name": "decoyFader", "children": [], "properties": {"floatValue": 0.5}},
        }
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(path)
        self.server.listen()
        self.server.settimeout(0.1)
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @staticmethod
    def _channel(object_id, cls, designator, channel_name, mute_id, fader_id):
        return {
            "id": object_id,
            "class": cls,
            "name": designator,
            "children": [],
            "properties": {
                "designator": designator,
                "channelName": channel_name,
                "hasFader": True,
                "faderValueString": "-6.0 dB",
                "muteDriver": {"id": mute_id, "class": "cMuteDriver"},
                "faderDriver": {"id": fader_id, "class": "cFaderDriver"},
            },
        }

    def close(self):
        self.stopping.set()
        self.server.close()
        self.thread.join(timeout=2)

    def _serve(self):
        while not self.stopping.is_set():
            try:
                conn, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                if data:
                    request = json.loads(data)
                    self.requests.append(request)
                    result = self._handle(request)
                    conn.sendall(json.dumps({"ok": True, "result": result}).encode() + b"\n")

    def _handle(self, request):
        op = request["op"]
        if op == "singleton":
            self.assert_singleton_request(request)
            return {"id": 1, "class": "ConnectionInterface", "name": "ConnectionInterface"}
        if op == "inspect":
            return self.objects[request["id"]]
        if op == "find":
            found = []
            todo = list(self.objects[request["id"]]["children"])
            while todo and len(found) < request.get("limit", 50):
                object_id = todo.pop(0)
                obj = self.objects[object_id]
                if (not request.get("class") or request["class"] in obj["class"]) and (
                    not request.get("name") or request["name"] in obj["name"]
                ):
                    found.append({"id": object_id, "class": obj["class"], "name": obj["name"]})
                todo.extend(obj["children"])
            return found
        obj = self.objects[request["id"]]
        if op == "get":
            return obj["properties"].get(request["prop"])
        if op == "set":
            obj["properties"][request["prop"]] = request["value"]
            return request["value"]
        if op == "call":
            if request["method"] == "getFloatValueForDBValue":
                return 0.42
            if request["method"] == "get(int)":
                return {"id": 14, "class": "cMixChannel"}
            return None
        raise AssertionError(f"unexpected bridge operation: {op}")

    @staticmethod
    def assert_singleton_request(request):
        if request.get("uri") != "ConnectionInterfaceSingleton" or request.get("type") != "ConnectionInterface":
            raise AssertionError(f"unexpected singleton request: {request}")


class SQMCPTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="sq", dir="/tmp")
        self.socket_path = os.path.join(self.tempdir.name, "s")
        self.bridge = FakeBridge(self.socket_path)
        self.sock_patch = mock.patch.object(sqctl, "SOCK", self.socket_path)
        self.sock_patch.start()
        self.sleep_patch = mock.patch("sq_mcp.time.sleep", return_value=None)
        self.sleep_patch.start()

    def tearDown(self):
        self.sleep_patch.stop()
        self.sock_patch.stop()
        self.bridge.close()
        self.tempdir.cleanup()

    def test_input_lookup_ignores_class_decoy_and_matches_designator_case_and_spaces(self):
        channels = sq_mcp.sq_channels("input")
        self.assertEqual({item["channel"] for item in channels}, {"Ip1", "Ip47/48"})

        self.assertEqual(sq_mcp.sq_set_mute(" iP 1 ", True), {"channel": "Ip1", "mute": True})
        self.assertIs(type(self.bridge.objects[110]["properties"]["value"]), int)
        self.assertEqual(self.bridge.objects[110]["properties"]["value"], 1)
        self.assertFalse(self.bridge.objects[112]["properties"]["value"])

    def test_fader_db_conversion_calls_driver_and_sets_float_value(self):
        result = sq_mcp.sq_set_fader("Ip1", -10)

        conversion = next(r for r in self.bridge.requests if r["op"] == "call" and r["method"] == "getFloatValueForDBValue")
        self.assertEqual(conversion["method"], "getFloatValueForDBValue")
        self.assertEqual(conversion["args"], [-2560])
        self.assertEqual(self.bridge.objects[210]["properties"]["floatValue"], 0.42)
        self.assertEqual(result["float_value"], 0.42)
        self.assertEqual(result["fader"], "-6.0 dB")

    def test_fader_at_or_below_minus_90_sets_zero_without_conversion(self):
        result = sq_mcp.sq_set_fader("Ip1", -100)

        self.assertEqual(result["float_value"], 0)
        self.assertEqual(self.bridge.objects[210]["properties"]["floatValue"], 0)
        self.assertFalse(any(r["op"] == "call" and r["method"] == "getFloatValueForDBValue" for r in self.bridge.requests))

    def test_fader_selects_requested_mix_then_restores_previous(self):
        sq_mcp.sq_set_fader("Ip1", -10, mix="aux 1")

        mix_writes = [r["value"] for r in self.bridge.requests if r["op"] == "set" and r["prop"] == "mix"]
        self.assertEqual(mix_writes, [{"id": 14}, {"id": 13}])

    def test_unknown_channel_mute_raises_value_error(self):
        with self.assertRaisesRegex(sq_mcp.ToolError, "channel"):
            sq_mcp.sq_set_mute("Ip99", True)


if __name__ == "__main__":
    unittest.main()
