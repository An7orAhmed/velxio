import os
import pty
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("cleanup.sh")
RUNNER = SCRIPT.parent.parent / "run.sh"


class CleanupTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.home = Path(self.temp.name) / "home"
    self.repo = self.home / "Research" / "velxio"
    (self.repo / "backend" / "app").mkdir(parents=True)
    (self.repo / "frontend").mkdir()
    (self.repo / ".git").mkdir()
    shutil.copyfile(RUNNER, self.repo / "run.sh")
    (self.repo / "scripts").mkdir()
    shutil.copyfile(SCRIPT, self.repo / "scripts" / "cleanup.sh")
    (self.repo / "backend" / "app" / "main.py").write_text("app = None\n", encoding="utf-8")
    (self.repo / "frontend" / "package.json").write_text("{}\n", encoding="utf-8")
    (self.repo / "backend" / ".venv").mkdir()
    (self.repo / ".run").mkdir()

    self.shared = self.home / ".platformio"
    self.shared.mkdir(parents=True)
    (self.shared / "packages").mkdir()
    self.arduino = self.home / "Library" / "Arduino15"
    self.arduino.mkdir(parents=True)
    (self.arduino / "package_index.json").write_text("{}", encoding="utf-8")

    self.personal = self.home / "Documents" / "Arduino" / "my-sketch"
    self.personal.mkdir(parents=True)
    (self.personal / "my-sketch.ino").write_text("void setup() {}", encoding="utf-8")

    self.other = self.home / "Research" / "other-project"
    self.other.mkdir(parents=True)
    self.env = dict(os.environ)
    self.env["HOME"] = str(self.home)

  def run_cleanup(self, mode, *, dry_run=False, confirmation=None):
    command = ["bash", str(self.repo / "run.sh"), "--cleanup"]
    if mode == "all":
      command.append("all")
    if dry_run:
      command.append("--dry-run")

    if confirmation is None:
      return subprocess.run(
        command,
        env=self.env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
      )

    master, slave = pty.openpty()
    try:
      proc = subprocess.Popen(
        command,
        env=self.env,
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
      )
      os.close(slave)
      slave = -1
      os.write(master, (confirmation + "\n").encode())
      output, _ = proc.communicate(timeout=15)
      return subprocess.CompletedProcess(command, proc.returncode, output.decode(), "")
    finally:
      os.close(master)
      if slave >= 0:
        os.close(slave)

  def test_preview_does_not_delete(self):
    result = self.run_cleanup("tools", dry_run=True)
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn("Dry run complete", result.stdout)
    self.assertTrue(self.shared.exists())
    self.assertTrue(self.arduino.exists())
    self.assertTrue(self.repo.exists())

  def test_preview_creates_no_runner_artifacts(self):
    (self.repo / ".run").rmdir()
    result = self.run_cleanup("tools", dry_run=True)
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertFalse((self.repo / ".run").exists())

  def test_does_not_delete_without_interactive_confirmation(self):
    result = self.run_cleanup("all")
    self.assertNotEqual(result.returncode, 0)
    self.assertIn("interactive terminal", result.stderr)
    self.assertTrue(self.repo.exists())

  def test_wrong_confirmation_preserves_files(self):
    result = self.run_cleanup("tools", confirmation="no")
    self.assertEqual(result.returncode, 0, result.stdout)
    self.assertIn("cancelled", result.stdout)
    self.assertTrue(self.shared.exists())
    self.assertTrue((self.repo / ".run").exists())

  def test_cleanup_removes_toolchains_not_source(self):
    result = self.run_cleanup("tools", confirmation="CLEAN TOOLCHAINS")
    self.assertEqual(result.returncode, 0, result.stdout)
    self.assertFalse(self.shared.exists())
    self.assertFalse(self.arduino.exists())
    self.assertFalse((self.repo / ".run").exists())
    self.assertFalse((self.repo / "backend" / ".venv").exists())
    self.assertTrue((self.repo / ".git").exists())
    self.assertTrue(self.personal.exists())
    self.assertTrue(self.other.exists())

  def test_cleanup_all_removes_checkout(self):
    result = self.run_cleanup("all", confirmation="DELETE VELXIO")
    self.assertEqual(result.returncode, 0, result.stdout)
    self.assertFalse(self.repo.exists())
    self.assertFalse(self.shared.exists())
    self.assertFalse(self.arduino.exists())
    self.assertTrue(self.personal.exists())
    self.assertTrue(self.other.exists())

  def test_unknown_checkout_cannot_be_removed(self):
    (self.repo / "backend" / "app" / "main.py").unlink()
    result = self.run_cleanup("all", confirmation="DELETE VELXIO")
    self.assertNotEqual(result.returncode, 0)
    self.assertIn("not a Velxio checkout", result.stdout)
    self.assertTrue(self.repo.exists())


if __name__ == "__main__":
  unittest.main()
