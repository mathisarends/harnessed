from importlib.resources import files


def worker_source() -> str:
    """Bundle the standard-library worker for an isolated ``python -c`` process.

    Modules load from the supplied source in memory. Neither the workspace nor a
    writable temporary directory participates in worker import resolution.
    """
    root = files("harnessed.integrations.sandbox.worker")
    sources = {
        name: root.joinpath(f"{name}.py").read_text(encoding="utf-8")
        for name in ("models", "filesystem", "runtime", "__init__", "__main__")
    }
    return "_sources = " + repr(sources) + "\n" + _BOOTSTRAP


_BOOTSTRAP = """\
import sys
from types import ModuleType

package = ModuleType("_harnessed_worker")
package.__path__ = []
package.__package__ = package.__name__
sys.modules[package.__name__] = package
for name, source in _sources.items():
    if name == "__init__":
        module = package
    else:
        module = ModuleType(package.__name__ + "." + name)
        module.__package__ = package.__name__
        sys.modules[module.__name__] = module
        setattr(package, name, module)
    module.__file__ = "<bundled-worker/" + name + ".py>"
    exec(compile(source, module.__file__, "exec"), module.__dict__)
"""
