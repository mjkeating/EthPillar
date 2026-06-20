"""Contract tests for deploy client module protocols."""

import importlib
import inspect
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deploy.protocols import (
    ALL_CLIENT_MODULES,
    ReleaseInfoProvider,
)


@pytest.mark.parametrize("module_name,required_generators", sorted(ALL_CLIENT_MODULES.items()))
def test_client_module_exports_required_functions(module_name: str, required_generators: list[str]) -> None:
    module = importlib.import_module(f"deploy.{module_name}")

    assert hasattr(module, "get_release_info"), f"{module_name} missing get_release_info"
    assert callable(module.get_release_info)

    sig = inspect.signature(module.get_release_info)
    assert len(sig.parameters) == 2

    for func_name in required_generators:
        assert hasattr(module, func_name), f"{module_name} missing {func_name}"
        assert callable(getattr(module, func_name))


@pytest.mark.parametrize("module_name", sorted(ALL_CLIENT_MODULES.keys()))
def test_client_module_satisfies_release_info_provider(module_name: str) -> None:
    module = importlib.import_module(f"deploy.{module_name}")
    assert isinstance(module, ReleaseInfoProvider)
