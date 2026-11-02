from typing import assert_type

from harnessed import Plugin, PluginConfig
from harnessed.plugins import Filesystem, Shell, ShellTools
from harnessed.plugins.filesystem import FilesystemConfig
from harnessed.plugins.shell import ShellConfig, ShellToolsConfig


class FileToolsConfig(PluginConfig):
    max_read_bytes: int = 100_000


class FileTools(Plugin[FileToolsConfig]):
    def limit(self) -> int:
        assert_type(self.config, FileToolsConfig)
        return self.config.max_read_bytes


files = FileTools(config=FileToolsConfig(max_read_bytes=200))
assert_type(files.config, FileToolsConfig)
FileTools()  # ty: ignore[invalid-argument-type]
FileTools(config=PluginConfig())  # ty: ignore[no-matching-overload]
FileTools(max_read_bytes=200)  # ty: ignore[no-matching-overload]


class Observer(Plugin):
    pass


observer = Observer()


class GenericTools[ConfigT: FileToolsConfig](Plugin[ConfigT]):
    def limit(self) -> int:
        return self.config.max_read_bytes


class ExtendedConfig(FileToolsConfig):
    enabled: bool = True


class ExtendedTools(GenericTools[ExtendedConfig]):
    pass


assert_type(ExtendedTools(config=ExtendedConfig()).config, ExtendedConfig)
assert_type(files.create_instance(), FileTools)
assert_type(Filesystem().config, FilesystemConfig)
assert_type(Shell().config, ShellConfig)
assert_type(ShellTools().config, ShellToolsConfig)
