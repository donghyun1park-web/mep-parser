from pathlib import Path
import unittest


class WindowsLauncherTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]

    def test_cmd_launchers_are_ascii_safe(self):
        # cmd.exe can split UTF-8 multibyte text in LF batch files before
        # chcp takes effect. Keep executable batch logic ASCII; the web UI and
        # Python application remain Korean.
        for name in (
            "install_cfd.bat", "install_openfoam2606.bat", "run_cfd.bat",
            "run_gui.bat", "run_pipeline.bat",
        ):
            with self.subTest(name=name):
                (self.repo / name).read_bytes().decode("ascii")

    def test_install_and_launcher_have_noninteractive_checks(self):
        install = (self.repo / "install_cfd.bat").read_text(encoding="ascii")
        openfoam = (self.repo / "install_openfoam2606.bat").read_text(
            encoding="ascii"
        )
        launcher = (self.repo / "run_cfd.bat").read_text(encoding="ascii")
        self.assertIn("--no-pause", install)
        self.assertIn("--check", openfoam)
        self.assertIn("--no-pause", openfoam)
        self.assertIn("Start-Process", openfoam)
        self.assertIn("-Verb RunAs", openfoam)
        self.assertIn("fltmc > nul 2> nul", openfoam)
        self.assertIn("wsl.exe --install -d Ubuntu-24.04 --no-launch", openfoam)
        self.assertIn("apt-get install -y openfoam2606", openfoam)
        self.assertIn("echo MEP_WSL_READY", openfoam)
        self.assertIn("if not defined VERIFY_OK goto :failed", openfoam)
        self.assertIn("--check", launcher)
        self.assertIn(".venv\\Scripts\\python.exe", launcher)


if __name__ == "__main__":
    unittest.main()
