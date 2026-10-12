import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from collector import core

class PackagingTests(unittest.TestCase):
    def test_extension_declares_scripting_permission(self):
        manifest=json.loads((Path(__file__).resolve().parents[1]/'extension/manifest.json').read_text())
        self.assertIn('scripting',manifest['permissions'])
        self.assertEqual(manifest['host_permissions'],['https://www.instagram.com/*'])

    def test_ffmpeg_found_from_normal_installed_package(self):
        module=SimpleNamespace(get_ffmpeg_exe=lambda:'/fixture/ffmpeg')
        with patch.object(core.shutil,'which',return_value=None),patch.dict(sys.modules,{'imageio_ffmpeg':module}):
            self.assertEqual(core.find_ffmpeg(),'/fixture/ffmpeg')
