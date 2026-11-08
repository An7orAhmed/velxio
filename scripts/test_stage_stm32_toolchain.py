import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stage_stm32_toolchain import host_name, matches_archive, select_archive


def sample_index():
  def tool(version):
    return {
      "name": "xpack-arm-none-eabi-gcc",
      "version": version,
      "systems": [
        {
          "host": "arm64-apple-darwin",
          "url": "https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases/download/v" + version + "/compiler.tar.gz",
          "archiveFileName": "compiler.tar.gz",
          "checksum": "SHA-256:" + "a" * 64,
          "size": "3",
        }
      ],
    }

  return {
    "packages": [
      {
        "name": "STMicroelectronics",
        "platforms": [
          {
            "architecture": "stm32",
            "version": "2.9.0",
            "toolsDependencies": [{"name": "xpack-arm-none-eabi-gcc", "version": "13.2.1-1.1"}],
          },
          {
            "architecture": "stm32",
            "version": "2.10.0",
            "toolsDependencies": [{"name": "xpack-arm-none-eabi-gcc", "version": "14.2.1-1.1"}],
          },
        ],
        "tools": [tool("13.2.1-1.1"), tool("14.2.1-1.1")],
      }
    ]
  }


class Stm32ToolchainTests(unittest.TestCase):
  def test_mac_arm_host(self):
    self.assertEqual(host_name("Darwin", "arm64"), "arm64-apple-darwin")
    self.assertEqual(host_name("Darwin", "x86_64"), "x86_64-apple-darwin")

  def test_linux_hosts(self):
    self.assertEqual(host_name("Linux", "aarch64"), "aarch64-linux-gnu")
    self.assertEqual(host_name("Linux", "amd64"), "x86_64-pc-linux-gnu")

  def test_selects_latest_platform_dependency(self):
    archive = select_archive(sample_index(), "arm64-apple-darwin")
    self.assertEqual(archive["version"], "14.2.1-1.1")
    self.assertEqual(archive["filename"], "compiler.tar.gz")

  def test_rejects_unofficial_source(self):
    index = sample_index()
    index["packages"][0]["tools"][1]["systems"][0]["url"] = "https://example.com/compiler.tar.gz"
    with self.assertRaisesRegex(ValueError, "official"):
      select_archive(index, "arm64-apple-darwin")

  def test_detects_invalid_checksum(self):
    index = sample_index()
    index["packages"][0]["tools"][1]["systems"][0]["checksum"] = "MD5:aaa"
    with self.assertRaisesRegex(ValueError, "checksum"):
      select_archive(index, "arm64-apple-darwin")

  def test_rejects_filename_traversal(self):
    index = sample_index()
    index["packages"][0]["tools"][1]["systems"][0]["archiveFileName"] = "../compiler.tar.gz"
    with self.assertRaisesRegex(ValueError, "filename"):
      select_archive(index, "arm64-apple-darwin")

  def test_verifies_cached_archive_bytes(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "archive.tar.gz"
      path.write_bytes(b"abc")
      archive = {
        "size": 3,
        "checksum": hashlib.sha256(b"abc").hexdigest(),
      }
      self.assertTrue(matches_archive(path, archive))
      path.write_bytes(b"abd")
      self.assertFalse(matches_archive(path, archive))
      path.unlink()
      self.assertFalse(matches_archive(path, archive))


if __name__ == "__main__":
  unittest.main()
