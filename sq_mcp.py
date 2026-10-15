# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2,<3"]
# ///
"""MCP tools for the live SQ MixPad bridge. Object ids are re-resolved on every call."""

import threading
import time
from contextlib import contextmanager
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

import sqctl

_KINDS = {
    "input": "cInputChannel",
    "aux": "cMixChannel",
    "group": "cGroupChannel",
    "main": "cMainLRChannel",
    "matrix": "cMatrixChannel",
    "fx_send": "cFXSendChannel",
    "fx_return": "cFXReturnChannel",
    "dca": "cDCAChannel",
    "mute_group": "cMuteGroupChannel",
    "soft_key": "cSoftKeyChannel",
}
Kind = Literal["input", "aux", "group", "main", "matrix", "fx_send", "fx_return", "dca", "mute_group", "soft_key"]

server = MCPServer(
    "sq-mixpad",
    instructions=(
        "SQ MixPad를 run-bridge.sh로 실행하고 믹서(또는 Offline)에 Connect한 뒤에만 동작합니다. "
        "오프라인 모드의 변경은 앱 안의 오프라인 쇼만 바꿉니다. "
        "온라인 모드의 변경은 실제 콘솔에 즉시 반영되므로 공연 중에는 신중히 쓰세요. "
        "EQ·다이내믹스 같은 처리는 sq_select_channel로 채널을 고른 뒤 다룹니다. "
        "그 밖의 기능(팬, 라우팅 등)은 sq_root/sq_inspect/sq_get/sq_set/sq_call로 찾고, "
        "어떤 속성을 쓰는지는 qrc-dump/의 QML에서 ConnectionInterface. 뒤를 따라가면 보입니다."
    ),
)


def _call(req: dict[str, Any]) -> Any:
    # ToolError, unlike other exceptions, reaches the model with its message.
    try:
        return sqctl.call(req)
    except TimeoutError as e:
        raise ToolError("bridge did not answer within 5 s; whether the request was applied is unknown, "
                        "so read the value back before retrying") from e
    except OSError as e:
        raise ToolError(f"bridge not reachable ({e}); start the app with run-bridge.sh") from e
    except RuntimeError as e:
        raise ToolError(f"bridge: {e}") from e


def _get(obj_id: int, prop: str) -> Any:
    return _call({"op": "get", "id": obj_id, "prop": prop})


def _root() -> int:
    return _call({"op": "singleton", "uri": "ConnectionInterfaceSingleton", "type": "ConnectionInterface"})["id"]


def _channel_ids(manager: int, cls: str) -> list[int]:
    # find matches class names by substring, so keep exact matches only.
    found = _call({"op": "find", "id": manager, "class": cls, "limit": 1000})
    return [ref["id"] for ref in found if ref["class"] == cls]


def _norm(s: str) -> str:
    return "".join(s.split()).casefold()


def _resolve(channel: str) -> tuple[int, str]:
    manager = _get(_root(), "channelManager")["id"]
    for cls in _KINDS.values():
        for ch in _channel_ids(manager, cls):
            designator = _get(ch, "designator")
            if _norm(designator) == _norm(channel):
                return ch, designator
    raise ToolError(f"channel {channel!r} not found")


def _driver(ch: int, prop: str, channel: str) -> int:
    try:
        ref = _get(ch, prop)
    except ToolError as e:  # channel classes without the driver lack the property entirely
        if "no property" not in str(e):
            raise
        ref = None
    if not ref:
        raise ToolError(f"channel {channel!r} has no {prop}")
    return ref["id"]


_select_lock = threading.Lock()  # mix/channel selection is app-global, so concurrent tool calls must not interleave


@contextmanager
def _selected_mix(mix: str):
    # Source-channel faders control the send to the selected mix: select `mix`, then restore the old selection.
    with _select_lock:
        root = _root()
        manager = _get(root, "channelManager")["id"]
        sel = _get(manager, "selected")["id"]
        buttons = _get(manager, "mixButtonChannels")["id"]
        refs = [_get(manager, "mainLRChannel")] + [
            _call({"op": "call", "id": buttons, "method": "get(int)", "args": [i]}) for i in range(_get(buttons, "count"))]
        mixes = {_get(r["id"], "designator"): r["id"] for r in refs}
        target = next((i for d, i in mixes.items() if _norm(d) == _norm(mix)), None)
        if target is None:
            raise ToolError(f"mix {mix!r} is not selectable; choose one of {', '.join(mixes)}")
        before = _get(sel, "mix")["id"]
        if before != target:
            _call({"op": "set", "id": sel, "prop": "mix", "value": {"id": target}})
            time.sleep(0.3)  # faderValueString follows the new mix asynchronously
        try:
            yield
        finally:
            if before != target:
                _call({"op": "set", "id": sel, "prop": "mix", "value": {"id": before}})


@server.tool()
def sq_status() -> dict[str, Any]:
    """연결 모드(offline/online), 세션 진입 여부(connected, 오프라인 세션도 true), 현재·다음 씬, 앱 버전을 돌려줍니다."""
    root = _root()
    conn = _get(root, "connectionManager")["id"]
    scene = _get(root, "sceneManager")["id"]
    return {
        "mode": "offline" if _get(conn, "offlineMode") else "online",
        "connected": bool(_get(conn, "primaryConnectionComplete")),
        "current_scene": _get(scene, "currentScene"),
        "next_scene": _get(scene, "nextScene"),
        "app_version": _get(root, "versionString"),
    }


@server.tool()
def sq_channels(kind: Kind = "input", mix: str = "MainLR") -> list[dict[str, Any]]:
    """한 종류의 채널 목록을 돌려줍니다. channel은 다른 도구에 넘기는 표기(예: Ip1, Aux3, MainLR), fader는 dB 표시 문자열, mute·fader가 없는 채널은 null입니다. 입력·FX 리턴 같은 소스 채널의 fader는 mix(MainLR, Aux1, Grp1 등)로 보내는 레벨이고, Aux·MainLR 같은 출력 채널은 mix와 관계없이 마스터 레벨입니다."""
    manager = _get(_root(), "channelManager")["id"]
    out = []
    with _selected_mix(mix):
        for ch in _channel_ids(manager, _KINDS[kind]):
            p = _call({"op": "inspect", "id": ch})["properties"]
            mute = _get(p["muteDriver"]["id"], "value") if p.get("muteDriver") else None
            out.append({
                "channel": p["designator"],
                "name": p["channelName"],
                "mute": None if mute is None else bool(mute),
                "fader": p["faderValueString"] if p.get("hasFader") else None,
            })
    return out


@server.tool()
def sq_set_mute(channel: str, on: bool) -> dict[str, Any]:
    """채널 뮤트를 켜거나 끄고 다시 읽은 값을 돌려줍니다. channel은 Ip1, Aux3, MainLR 같은 표기(대소문자·공백 무시). 앱이 실제 SQ에 연결돼 있으면 콘솔에 즉시 반영됩니다."""
    ch, designator = _resolve(channel)
    mute = _driver(ch, "muteDriver", channel)
    _call({"op": "set", "id": mute, "prop": "value", "value": int(on)})
    return {"channel": designator, "mute": bool(_get(mute, "value"))}


@server.tool()
def sq_set_fader(channel: str, db: float, mix: str = "MainLR") -> dict[str, Any]:
    """채널 페이더를 dB로 맞춥니다(최대 +10, -90 이하는 -inf). 입력·FX 리턴 같은 소스 채널은 mix(MainLR, Aux1, Grp1 등)로 보내는 레벨을, Aux·MainLR 같은 출력 채널은 마스터 레벨을 바꿉니다. 다시 읽은 내부 값과 dB 표시 문자열을 돌려줍니다. 앱이 실제 SQ에 연결돼 있으면 콘솔에 즉시 반영됩니다."""
    if db > 10:
        raise ToolError("fader dB must be 10 or lower")
    with _selected_mix(mix):
        ch, designator = _resolve(channel)
        fader = _driver(ch, "faderDriver", channel)
        value = 0 if db <= -90 else _call(
            {"op": "call", "id": fader, "method": "getFloatValueForDBValue", "args": [round(db * 256)]})
        _call({"op": "set", "id": fader, "prop": "floatValue", "value": value})
        time.sleep(0.3)  # faderValueString refreshes asynchronously
        return {"channel": designator, "float_value": _get(fader, "floatValue"), "fader": _get(ch, "faderValueString")}


@server.tool()
def sq_select_channel(channel: str) -> dict[str, Any]:
    """처리(PEQ, 게이트, 컴프, 딜레이 등)를 다룰 채널을 선택하고 그 채널의 처리 드라이버 묶음 channelDrivers 참조를 돌려줍니다. 처리 값은 sq_inspect로 channelDrivers 아래 드라이버를 찾아 sq_get/sq_set으로 읽고 씁니다(예: compDriverHolder.thresholdDriver.value = dB*256+32768). 선택을 바꾸면 직전에 쓴 처리 값이 사라질 수 있어 이 도구는 바꾸기 전에 1초 기다립니다. 채널 선택은 sq_set 대신 항상 이 도구로 하세요. 앱이 실제 SQ에 연결돼 있으면 처리 값 변경은 콘솔에 즉시 반영됩니다."""
    ch, designator = _resolve(channel)
    with _select_lock:
        sel = _get(_get(_root(), "channelManager")["id"], "selected")["id"]
        time.sleep(1.0)  # a write to the old selection's drivers is dropped if the selection changes first
        _call({"op": "set", "id": sel, "prop": "channel", "value": {"id": ch}})
        time.sleep(0.5)
        return {"channel": designator, "channelDrivers": _get(sel, "channelDrivers")}


@server.tool()
def sq_recall_scene(number: int) -> dict[str, Any]:
    """씬 번호(1부터)를 불러오고 현재·다음 씬 이름을 돌려줍니다. 앱이 실제 SQ에 연결돼 있으면 콘솔에 즉시 반영됩니다."""
    scene = _get(_root(), "sceneManager")["id"]
    maximum = _get(scene, "maxNumberOfScenes")
    if not 1 <= number <= maximum:
        raise ToolError(f"scene number must be between 1 and {maximum}")
    _call({"op": "call", "id": scene, "method": "recallScene", "args": [number - 1]})
    time.sleep(0.3)
    return {"current_scene": _get(scene, "currentScene"), "next_scene": _get(scene, "nextScene")}


@server.tool()
def sq_root() -> dict[str, Any]:
    """앱 최상위 객체(ConnectionInterface)의 참조를 돌려줍니다. 범용 도구 탐색의 시작점입니다. id는 앱 프로세스마다 새로 매겨지므로 앱을 다시 켜면 sq_root부터 다시 찾으세요."""
    return _call({"op": "singleton", "uri": "ConnectionInterfaceSingleton", "type": "ConnectionInterface"})


@server.tool()
def sq_inspect(id: int) -> dict[str, Any]:
    """객체의 클래스, 부모·자식, 모든 속성 값, 호출 가능한 메서드 목록을 돌려줍니다. id는 앱 프로세스마다 새로 매겨지므로 앱을 다시 켜면 sq_root부터 다시 찾으세요."""
    return _call({"op": "inspect", "id": id})


@server.tool()
def sq_find(id: int, cls: str = "", name: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """객체 아래 자손 중 클래스 이름·objectName에 주어진 문자열이 들어간 것을 찾습니다. id는 앱 프로세스마다 새로 매겨지므로 앱을 다시 켜면 sq_root부터 다시 찾으세요."""
    return _call({"op": "find", "id": id, "class": cls, "name": name, "limit": limit})


@server.tool()
def sq_get(id: int, prop: str) -> Any:
    """객체 속성 하나를 읽습니다. 객체를 가리키는 속성은 {id, class} 참조로 돌아옵니다. id는 앱 프로세스마다 새로 매겨지므로 앱을 다시 켜면 sq_root부터 다시 찾으세요."""
    return _get(id, prop)


@server.tool()
def sq_set(id: int, prop: str, value: Any) -> Any:
    """객체 속성 하나를 쓰고 다시 읽은 값을 돌려줍니다. 앱이 실제 SQ에 연결돼 있으면 콘솔에 즉시 반영됩니다. id는 앱 프로세스마다 새로 매겨지므로 앱을 다시 켜면 sq_root부터 다시 찾으세요."""
    return _call({"op": "set", "id": id, "prop": prop, "value": value})


@server.tool()
def sq_call(id: int, method: str, args: list[Any] = []) -> Any:
    """sq_inspect의 methods에 나온 공개 메서드를 호출합니다. 앱이 실제 SQ에 연결돼 있으면 콘솔에 즉시 반영됩니다. id는 앱 프로세스마다 새로 매겨지므로 앱을 다시 켜면 sq_root부터 다시 찾으세요."""
    return _call({"op": "call", "id": id, "method": method, "args": args})


if __name__ == "__main__":
    server.run()
