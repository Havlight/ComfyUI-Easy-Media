"""A node's settings by the names ComfyUI gives its widgets, checked against the node's definition.

The names are those of a saved workflow's ``widgets_values_named``: an option of a dynamic combo
is ``<combo>.<name>`` (``sampling_mode.upscale_by``, ``resolution.width``), and the seed's mode is
``control_after_generate``. The same check serves a skill agent setting an open tab and one
patching a workflow file, so neither keeps its own list of settings.
"""

import math
from collections.abc import Callable, Mapping
from typing import Any

from aiohttp import web

DYNAMIC_COMBO = "COMFY_DYNAMICCOMBO_V3"
VALUE_TYPES = {"INT", "FLOAT", "BOOLEAN", "STRING", "COMBO", DYNAMIC_COMBO}
CONTROL = "control_after_generate"
CONTROL_MODES = ("fixed", "increment", "decrement", "randomize")
SHOWN_CHOICES = 20
# The page holds numbers as JavaScript doubles; a whole number past this loses digits there.
MAX_EXACT_INT = 2 ** 53 - 1

Spec = tuple[str, dict]


class SettingsError(ValueError):
    """A setting the node does not have, or a value it does not take; the message says which."""


def _inputs(input_types: Mapping) -> dict[str, Spec]:
    """Each input's type and options, required then optional, as the node lays its widgets out."""
    specs: dict[str, Spec] = {}
    for section in ("required", "optional"):
        for name, spec in (input_types.get(section) or {}).items():
            kind = spec[0]
            options = dict(spec[1]) if len(spec) > 1 and isinstance(spec[1], Mapping) else {}
            if isinstance(kind, (list, tuple)):
                kind, options = "COMBO", {**options, "options": list(kind)}
            specs[name] = (kind, options)
    return specs


def _is_setting(spec: Spec) -> bool:
    """A widget holding a plain value; sockets and the Editor's own widgets (track data) are not."""
    kind, options = spec
    return kind in VALUE_TYPES and not options.get("forceInput")


def _option(spec: Spec, key: Any) -> dict[str, Spec] | None:
    """The inputs a dynamic combo adds for one of its options."""
    option = next((item for item in spec[1].get("options", []) if item.get("key") == key), None)
    return None if option is None else _inputs(option.get("inputs") or {})


def _controlled(top: dict[str, Spec]) -> bool:
    return any(kind == "INT" and options.get(CONTROL) for kind, options in top.values())


class NodeSettings:
    def __init__(self, label: str, input_types: Mapping, named: dict) -> None:
        self.label = label
        self.top = _inputs(input_types)
        self.named = named

    def available(self) -> list[str]:
        """The settings it has now, and those its definition has that the saved values lack."""
        names = list(self.settings())
        return names + [name for name, spec in self.top.items() if _is_setting(spec) and name not in names]

    def settings(self) -> dict[str, Any]:
        """The node's current settings, in widget order."""
        result = {}
        for name, value in self.named.items():
            if name == CONTROL:
                if _controlled(self.top):
                    result[name] = value
                continue
            spec = self._find(name)
            if spec is not None and _is_setting(spec):
                result[name] = value
        return result

    def _find(self, name: str) -> Spec | None:
        specs, path, spec = self.top, "", None
        for part in name.split("."):
            if spec is not None:
                if spec[0] != DYNAMIC_COMBO:
                    return None
                specs = _option(spec, self.named.get(path)) or {}
            if part not in specs:
                return None
            spec, path = specs[part], f"{path}.{part}" if path else part
        return spec

    def spec(self, name: str) -> Spec:
        spec = self._find(name)
        if spec is not None:
            return spec
        # Name the option that has it when the combo is set to another one.
        parts = name.split(".")
        for depth in range(1, len(parts)):
            path, combo = ".".join(parts[:depth]), self._find(".".join(parts[:depth]))
            if combo is None or combo[0] != DYNAMIC_COMBO:
                break
            child = parts[depth]
            owners = [str(item.get("key")) for item in combo[1].get("options", [])
                      if child in _inputs(item.get("inputs") or {})]
            if child not in (_option(combo, self.named.get(path)) or {}) and owners:
                raise SettingsError(
                    f"{name} is a setting of {path} {' or '.join(owners)}, and {path} is "
                    f"{self.named.get(path)!r}; set {path} to {owners[0]} as well.")
        raise SettingsError(f"{self.label} has no setting {name}; its settings are: {', '.join(self.available())}")

    def insert(self, name: str, value: Any) -> None:
        """Add a setting the saved values lack (an optional input saved before it existed) where the
        node lays it out: after the settings that come before it, and before the frontend's own widgets."""
        controlled = next((key for key, (kind, options) in self.top.items() if kind == "INT" and options.get(CONTROL)), None)
        parent, _dot, own = name.rpartition(".")
        # The settings laid out before it: of its combo's option, or of the node.
        siblings = list(_option(self._find(parent), self.named.get(parent)) or {}) if parent else list(self.top)
        earlier = set(siblings[:siblings.index(own)]) if own in siblings else set(siblings)

        def before(key: str) -> bool:
            if name == CONTROL:
                return key == controlled
            if parent:
                if key == parent:
                    return True
                child = key[len(parent) + 1:] if key.startswith(f"{parent}.") else None
                return child is not None and child.split(".")[0] in earlier
            owner = controlled if key == CONTROL else key.split(".")[0]
            return owner in earlier

        items = list(self.named.items())
        position = max((index + 1 for index, (key, _value) in enumerate(items) if before(key)), default=0)
        items.insert(position, (name, value))
        self.named = dict(items)

    def replace_option(self, name: str, spec: Spec, value: Any) -> None:
        """Switch a dynamic combo: the old option's widgets go, the new one's follow it at their defaults."""
        named: dict = {}
        for key, current in self.named.items():
            if key.startswith(f"{name}."):
                continue
            if key == name:
                named[key] = value
                named.update(_option_defaults(name, spec, value))
            else:
                named[key] = current
        self.named = named


def _default(spec: Spec) -> Any:
    kind, options = spec
    if "default" in options:
        return options["default"]
    if kind == DYNAMIC_COMBO:
        return next((item.get("key") for item in options.get("options", [])), None)
    if kind == "COMBO":
        return next(iter(options.get("options", [])), None)
    return {"BOOLEAN": False, "INT": 0, "FLOAT": 0.0, "STRING": ""}.get(kind)


def _option_defaults(path: str, spec: Spec, key: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for child, child_spec in (_option(spec, key) or {}).items():
        if not _is_setting(child_spec):
            continue
        name, default = f"{path}.{child}", _default(child_spec)
        values[name] = default
        if child_spec[0] == DYNAMIC_COMBO:
            values.update(_option_defaults(name, child_spec, default))
    return values


def _choices(spec: Spec) -> list[Any]:
    kind, options = spec
    if kind == DYNAMIC_COMBO:
        return [item.get("key") for item in options.get("options", [])]
    return list(options.get("options", []))


def _value(name: str, spec: Spec, value: Any) -> Any:
    """The value as the widget holds it. Text from a command line becomes a number or a switch."""
    kind, options = spec
    if kind == "BOOLEAN":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise SettingsError(f"{name} must be true or false")
    if kind in ("INT", "FLOAT"):
        number = value
        if isinstance(value, str):
            try:
                number = int(value) if kind == "INT" else float(value)
            except ValueError:
                number = None
        try:
            finite = isinstance(number, (int, float)) and math.isfinite(number)
        except OverflowError:
            finite = False
        if isinstance(number, bool) or not isinstance(number, int if kind == "INT" else (int, float)) or not finite:
            raise SettingsError(f"{name} must be {'a whole number' if kind == 'INT' else 'a number'}")
        if kind == "INT" and abs(number) > MAX_EXACT_INT:
            raise SettingsError(f"{name} must be at most {MAX_EXACT_INT}; the page cannot hold larger whole numbers exactly")
        number = float(number) if kind == "FLOAT" else number
        low, high = options.get("min"), options.get("max")
        if (low is not None and number < low) or (high is not None and number > high):
            raise SettingsError(f"{name} must be from {low} to {high}")
        # The widget would round a whole number to its step; say so instead.
        step = options.get("step")
        if kind == "INT" and isinstance(step, int) and step > 1 and number % step:
            raise SettingsError(f"{name} must be a multiple of {step}")
        return number
    if kind == "STRING":
        if not isinstance(value, str):
            raise SettingsError(f"{name} must be text")
        return value
    if value is None:
        raise SettingsError(f"{name} needs one of its options, written out")
    choices = _choices(spec)
    text = ("true" if value else "false") if isinstance(value, bool) else str(value)
    for choice in choices:
        if str(choice) == text:
            return choice
    shown = ", ".join(map(str, choices[:SHOWN_CHOICES])) + (", …" if len(choices) > SHOWN_CHOICES else "")
    raise SettingsError(f"{name} must be one of: {shown}")


def change_settings(input_types: Mapping, node: Mapping, changes: Mapping) -> dict:
    """Check changes to a node's settings and apply them to its named widget values.

    Returns the changes in the order to set them (a combo before its options), the settings after
    them, and the node's widget values after them (named, and in widget order for a file).
    """
    label = f"{node.get('type', 'The node')} {node.get('id', '')}".strip()
    named = node.get("widgets_values_named")
    if not isinstance(named, Mapping):
        raise SettingsError(f"{label} has no named widget values; open the workflow in ComfyUI and save it again")
    if not isinstance(changes, Mapping) or not all(isinstance(name, str) for name in changes):
        raise SettingsError("changes must map setting names to values")
    settings = NodeSettings(label, input_types, dict(named))
    linked = {entry["widget"].get("name") for entry in node.get("inputs") or []
              if isinstance(entry, Mapping) and isinstance(entry.get("widget"), Mapping)
              and entry.get("link") is not None}
    controlled = next((key for key, (kind, options) in settings.top.items() if kind == "INT" and options.get(CONTROL)), None)
    applied = []
    # A combo comes before its options, which exist only once it has its new value.
    for name in sorted(changes, key=lambda key: key.count(".")):
        if name == CONTROL:
            if controlled is None:
                raise SettingsError(f"{label} has no setting {name}")
            value = changes[name]
            if value not in CONTROL_MODES:
                raise SettingsError(f"{name} must be one of: {', '.join(CONTROL_MODES)}")
            # The page hides the mode of a number that comes from a link.
            if controlled in linked and value != settings.named.get(name):
                raise SettingsError(f"{name} belongs to {controlled}, which comes from a link in the workflow")
        else:
            spec = settings.spec(name)
            if not _is_setting(spec):
                raise SettingsError(f"{name} is not a setting with a value; {label} takes it from its "
                                    "plan or a link")
            value = _value(name, spec, changes[name])
            # Setting a linked widget to the value it has changes nothing, so it is allowed.
            if value != settings.named.get(name) and any(
                    name == source or name.startswith(f"{source}.") for source in linked):
                raise SettingsError(f"{name} comes from a link in the workflow; change it where it comes from")
            if name not in settings.named:
                settings.insert(name, None)
            if spec[0] == DYNAMIC_COMBO and settings.named.get(name) != value:
                settings.replace_option(name, spec, value)
        if name not in settings.named:
            settings.insert(name, value)
        settings.named[name] = value
        applied.append([name, value])
    return {"changes": applied, "settings": settings.settings(),
            "widgets_values_named": settings.named, "widgets_values": list(settings.named.values())}


def comfy_input_types(node_type: str) -> Mapping | None:
    """A node's definition from ComfyUI's registry, the one the frontend gets from /object_info."""
    import nodes  # ComfyUI's node registry; only there once the server runs

    node_class = nodes.NODE_CLASS_MAPPINGS.get(node_type)
    return None if node_class is None else node_class.INPUT_TYPES()


def register_settings_routes(routes: web.RouteTableDef,
                             input_types: Callable[[str], Mapping | None] = comfy_input_types) -> None:
    def respond(payload: Any) -> dict:
        if not isinstance(payload, dict) or not isinstance(payload.get("node"), Mapping):
            raise SettingsError("Send the workflow node and the changes to its settings")
        node = payload["node"]
        definition = input_types(str(node.get("type")))
        if definition is None:
            raise SettingsError(f"ComfyUI has no node type {node.get('type')!r}")
        return change_settings(definition, node, payload.get("changes") or {})

    @routes.post("/easy-media/workflow/settings")
    async def settings(request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            return web.json_response({"error": "Content-Type must be application/json"}, status=415)
        try:
            payload = await request.json()
        except (ValueError, UnicodeError):
            return web.json_response({"error": "Invalid JSON"}, status=400)
        try:
            return web.json_response(respond(payload))
        except SettingsError as error:
            return web.json_response({"error": str(error)}, status=400)
