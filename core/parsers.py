"""
Registry of supported potentiostats.

To add a new instrument: write a BaseParser subclass (see ``BaseParser.read_data``)
and add it here. The key is the name stored in config.json
(``experiment_info.potentiostat``) and shown in the launcher's config dialog.
"""

from core.base_parser import BaseParser
from core.elins_parser import ElinsParser
from core.yarst_parser import YarstParser

PARSERS: dict[str, type[BaseParser]] = {
    'Yarst': YarstParser,
    'Elins': ElinsParser,
}
