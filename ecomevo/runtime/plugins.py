from __future__ import annotations

import inspect
from dataclasses import asdict, dataclass, field
from importlib import metadata
from typing import Any, Callable, Iterable, Mapping


class PluginError(RuntimeError):
    """Base error for runtime plugin operations."""


class PluginContractError(PluginError):
    """Raised when a plugin does not implement its declared runtime contract."""


class PluginLifecycleError(PluginError):
    """Raised when a synchronous plugin lifecycle hook cannot be completed."""


@dataclass(frozen=True)
class PluginContract:
    """Small structural contract used without coupling plugins to concrete classes."""

    methods: tuple[str, ...] = ()
    attributes: tuple[str, ...] = ()

    def missing(self, instance: Any) -> list[str]:
        if instance is None:
            return []
        missing = [name for name in self.methods if not callable(getattr(instance, name, None))]
        missing.extend(name for name in self.attributes if not hasattr(instance, name))
        return missing


@dataclass
class PluginDescriptor:
    key: str
    kind: str
    name: str
    version: str = "1.0"
    enabled: bool = True
    description: str = ""
    api_version: str = "1"
    source: str = "builtin"
    generation: int = 1
    required: bool = True
    contract: PluginContract = field(default_factory=PluginContract, repr=False)


PluginChangeHook = Callable[[str, Any, Any, str], None]


class PluginRegistry:
    """Live runtime plugin registry with contracts, lifecycle and atomic rebinding.

    Discovery is metadata-only. Loading an entry point is deliberately explicit because
    importing a third-party plugin executes third-party Python code.
    """

    ENTRY_POINT_GROUP = "ecomevo.plugins"

    def __init__(self, *, on_change: PluginChangeHook | None = None):
        self._plugins: dict[str, PluginDescriptor] = {}
        self._instances: dict[str, Any] = {}
        self._on_change = on_change

    def register(
        self,
        key: str,
        kind: str,
        name: str,
        description: str = "",
        version: str = "1.0",
        instance: Any = None,
        *,
        api_version: str = "1",
        source: str = "builtin",
        required: bool = True,
        contract: PluginContract | None = None,
    ) -> None:
        if key in self._plugins:
            raise PluginError(f"plugin already registered: {key}")
        self._plugins[key] = PluginDescriptor(
            key=key,
            kind=kind,
            name=name,
            version=version,
            enabled=True,
            description=description,
            api_version=api_version,
            source=source,
            required=required,
            contract=contract or PluginContract(),
        )
        if instance is not None:
            self._instances[key] = instance

    def get(self, key: str, default: Any = None) -> Any:
        descriptor = self._plugins.get(key)
        if not descriptor or not descriptor.enabled:
            return default
        return self._instances.get(key, default)

    def descriptor(self, key: str) -> PluginDescriptor:
        if key not in self._plugins:
            raise KeyError(key)
        return self._plugins[key]

    def validate(self, key: str, instance: Any) -> None:
        descriptor = self.descriptor(key)
        if instance is None and descriptor.required:
            raise PluginContractError(f"required plugin cannot be empty: {key}")
        missing = descriptor.contract.missing(instance)
        if missing:
            joined = ", ".join(missing)
            raise PluginContractError(f"plugin {key} is missing contract members: {joined}")

    @staticmethod
    def _lifecycle(instance: Any, hook: str, context: Mapping[str, Any]) -> None:
        callback = getattr(instance, hook, None)
        if not callable(callback):
            return
        result = callback(context)
        if inspect.isawaitable(result):
            close = getattr(result, "close", None)
            if callable(close):
                close()
            raise PluginLifecycleError(
                f"{hook} must be synchronous; use runtime methods for async resources"
            )

    def replace(
        self,
        key: str,
        instance: Any,
        *,
        version: str | None = None,
        source: str = "runtime",
    ) -> None:
        """Atomically replace and rebind a plugin, rolling back on any hook failure."""

        descriptor = self.descriptor(key)
        self.validate(key, instance)
        previous = self._instances.get(key)
        previous_version = descriptor.version
        previous_source = descriptor.source
        previous_generation = descriptor.generation
        context = {
            "registry": self,
            "key": key,
            "descriptor": descriptor,
            "previous": previous,
        }
        self._lifecycle(instance, "plugin_start", context)
        self._instances[key] = instance
        descriptor.version = version or descriptor.version
        descriptor.source = source
        descriptor.generation += 1
        descriptor.enabled = True
        try:
            if self._on_change:
                self._on_change(key, instance, previous, "replaced")
        except Exception:
            self._instances[key] = previous
            descriptor.version = previous_version
            descriptor.source = previous_source
            descriptor.generation = previous_generation
            try:
                self._lifecycle(instance, "plugin_stop", context)
            finally:
                raise
        if previous is not None and previous is not instance:
            try:
                self._lifecycle(previous, "plugin_stop", {**context, "replacement": instance})
            except Exception:
                if self._on_change:
                    self._on_change(key, previous, instance, "rollback")
                self._instances[key] = previous
                descriptor.version = previous_version
                descriptor.source = previous_source
                descriptor.generation = previous_generation
                self._lifecycle(instance, "plugin_stop", context)
                self._lifecycle(previous, "plugin_start", {**context, "rollback": True})
                raise

    def set_enabled(self, key: str, enabled: bool) -> None:
        """Enable optional plugins; required runtime components cannot be disabled."""

        descriptor = self.descriptor(key)
        enabled = bool(enabled)
        if descriptor.enabled == enabled:
            return
        if not enabled and descriptor.required:
            raise PluginContractError(f"required plugin cannot be disabled: {key}")
        instance = self._instances.get(key)
        context = {"registry": self, "key": key, "descriptor": descriptor}
        if enabled:
            self.validate(key, instance)
            self._lifecycle(instance, "plugin_start", context)
        descriptor.enabled = enabled
        try:
            if self._on_change:
                self._on_change(
                    key,
                    instance if enabled else None,
                    instance,
                    "enabled" if enabled else "disabled",
                )
        except Exception:
            descriptor.enabled = not enabled
            if enabled:
                self._lifecycle(instance, "plugin_stop", context)
            raise
        if not enabled:
            try:
                self._lifecycle(instance, "plugin_stop", context)
            except Exception:
                descriptor.enabled = True
                if self._on_change:
                    self._on_change(key, instance, None, "rollback")
                self._lifecycle(instance, "plugin_start", {**context, "rollback": True})
                raise

    def describe(self) -> list[dict[str, Any]]:
        rows = []
        for key, descriptor in self._plugins.items():
            row = asdict(descriptor)
            row.pop("contract", None)
            instance = self._instances.get(key)
            missing = descriptor.contract.missing(instance)
            row.update(
                {
                    "loaded": instance is not None,
                    "contract_valid": (instance is not None or not descriptor.required) and not missing,
                    "contract_missing": missing,
                }
            )
            rows.append(row)
        return rows

    def by_kind(self, kind: str) -> list[dict[str, Any]]:
        return [row for row in self.describe() if row["kind"] == kind]

    @classmethod
    def discover_entry_points(cls, group: str | None = None) -> list[dict[str, str]]:
        """List installed plugin entry points without importing their modules."""

        selected_group = group or cls.ENTRY_POINT_GROUP
        points = metadata.entry_points()
        selected: Iterable[Any]
        if hasattr(points, "select"):
            selected = points.select(group=selected_group)
        else:  # pragma: no cover - compatibility for older importlib implementations
            selected = points.get(selected_group, [])
        return sorted(
            [
                {
                    "name": point.name,
                    "value": point.value,
                    "group": selected_group,
                    "distribution": str(
                        getattr(getattr(point, "dist", None), "name", "") or ""
                    ),
                }
                for point in selected
            ],
            key=lambda row: (row["name"], row["value"]),
        )

    def load_entry_point(self, name: str, *, group: str | None = None) -> dict[str, Any]:
        """Explicitly load a package exposing ``manifest`` and ``create()``.

        The manifest key must target an already registered runtime slot. This prevents an
        external package from silently inventing an ungoverned execution path.
        """

        selected_group = group or self.ENTRY_POINT_GROUP
        points = metadata.entry_points()
        if hasattr(points, "select"):
            selected = points.select(group=selected_group, name=name)
        else:  # pragma: no cover - compatibility for older importlib implementations
            selected = [
                point for point in points.get(selected_group, []) if point.name == name
            ]
        selected = list(selected)
        if len(selected) != 1:
            raise PluginError(f"expected one entry point named {name!r}, found {len(selected)}")
        bundle = selected[0].load()
        if inspect.isclass(bundle):
            bundle = bundle()
        manifest = getattr(bundle, "manifest", None)
        create = getattr(bundle, "create", None)
        if not isinstance(manifest, Mapping) or not callable(create):
            raise PluginContractError("entry point must expose a manifest mapping and create()")
        key = str(manifest.get("key") or "")
        if key not in self._plugins:
            raise PluginContractError(
                f"entry point targets unknown runtime slot: {key or '<empty>'}"
            )
        descriptor = self.descriptor(key)
        api_version = str(manifest.get("api_version") or "")
        if api_version != descriptor.api_version:
            raise PluginContractError(
                f"plugin API mismatch for {key}: expected {descriptor.api_version}, "
                f"got {api_version or '<empty>'}"
            )
        instance = create()
        self.replace(
            key,
            instance,
            version=str(manifest.get("version") or descriptor.version),
            source=f"entry-point:{name}",
        )
        return next(row for row in self.describe() if row["key"] == key)
